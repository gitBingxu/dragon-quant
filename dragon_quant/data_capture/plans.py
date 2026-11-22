"""事前母集构建：先登记、再观察，结果不能反选。

母集只**读取既有本地数据**（``assets.db`` 的 dragons 镜像与 prep 快照），
不新增选股评分、不触发 scan/prep、不联网：

- 最近 ``recent_days`` 个真实交易日的龙头记录（``dragons`` 镜像）；
- 严格早于计划日的最近一盘前快照的全部 ``review_candidates``（含 position=0/null
  与被过滤项，不做二次筛选）；
- 显式传入的持仓代码与补充代码；
- 上一版计划中尚未到 ``track_until`` 的待成熟观察对象（跨交易日承接）。

上游已经截断的候选取多少就登记多少，截断范围写进 ``pool`` 缺口，绝不宣称覆盖
全市场。计划一经冻结不可变；盘中新增成员只能追加 revision。
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Iterable, Optional

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets import observations as store

from .types import (
    CODE_TOPICS,
    PLAN_SCHEMA_VERSION,
    TOPICS,
    CaptureError,
    PlanMember,
)

DEFAULT_RECENT_DAYS = 5
DEFAULT_TRACK_DAYS = 10

#: 首版默认时槽（计划只是登记，不代表系统定时器已安装）。
DEFAULT_SLOTS = (
    {"phase": "auction", "slot": "0926",
     "topics": ["auction_path", "auction_final"], "grace_minutes": 30},
    {"phase": "intraday", "slot": "1450",
     "topics": ["minute_bars", "quote"], "grace_minutes": 60},
    {"phase": "postclose", "slot": "1510",
     "topics": ["daily_bars", "minute_bars"], "grace_minutes": 180},
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_trade_date(value: str) -> str:
    text = str(value or "").strip()
    if not _DATE_RE.match(text):
        raise CaptureError("invalid_trade_date",
                           f"计划交易日必须 YYYY-MM-DD: {value!r}")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise CaptureError("invalid_trade_date",
                           f"非法交易日: {value!r}") from exc
    return text


def normalize_topics(values: Optional[Iterable[str]]) -> tuple[str, ...]:
    if values is None:
        return TOPICS
    selected = tuple(dict.fromkeys(str(item) for item in values))
    unknown = [item for item in selected if item not in TOPICS]
    if unknown:
        raise CaptureError("unknown_topic", f"未知采集主题: {unknown}",
                           topics=list(unknown))
    if not selected:
        raise CaptureError("unknown_topic", "至少需要一个采集主题")
    return selected


def _recent_dragon_rows(trade_date: str, recent_days: int,
                        db_path) -> tuple[list[str], list[tuple], Optional[str]]:
    """严格早于 ``trade_date`` 的最近 N 个 dragons 交易日及其记录（只读）。

    返回 ``(dates, rows, error)``：读库失败与「确实没有记录」分开报告，绝不把
    源异常吞成空池。
    """
    conn = assets_db._connect(create=False, path=db_path)
    if conn is None:
        return [], [], None
    try:
        assets_db._ensure_schema(conn, migrate=False)
        dates = [row[0] for row in conn.execute(
            "SELECT DISTINCT trade_date FROM dragons WHERE trade_date < ? "
            "ORDER BY trade_date DESC LIMIT ?",
            (trade_date, int(recent_days)),
        )]
        if not dates:
            return [], [], None
        placeholders = ", ".join("?" * len(dates))
        rows = conn.execute(
            "SELECT trade_date, code, COALESCE(name, '') FROM dragons "
            f"WHERE trade_date IN ({placeholders}) "
            "ORDER BY trade_date, code",
            tuple(dates),
        ).fetchall()
        return sorted(dates), rows, None
    except Exception as exc:  # noqa: BLE001 - 读失败必须显式上报
        return [], [], f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()


def _latest_prep_snapshot(trade_date: str, db_path) -> tuple[Optional[dict], dict]:
    """严格早于计划日的最近一盘前快照（只读，零建库）。"""
    conn = assets_db._connect(create=False, path=db_path)
    if conn is None:
        return None, {"trade_date": None, "error": None}
    try:
        assets_db._ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT trade_date, result_json FROM prep_snapshots "
            "WHERE trade_date < ? AND source = 'v2' "
            "ORDER BY trade_date DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
    except Exception:
        return None, {"trade_date": None, "error": "prep_snapshots_unreadable"}
    finally:
        conn.close()
    if row is None:
        return None, {"trade_date": None, "error": None}
    payload_text = row[1]
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError) as exc:
        return None, {"trade_date": row[0],
                      "error": f"prep_snapshot_corrupt: {exc}"}
    if not isinstance(payload, dict):
        return None, {"trade_date": row[0],
                      "error": "prep_snapshot_corrupt: not an object"}
    return payload, {"trade_date": row[0], "error": None}


def _evaluation_deadline(trade_date: str, track_days: int) -> str:
    """事前评价截止日（日历日口径；不是交易有效期，也不用于策略授权）。"""
    return (date.fromisoformat(trade_date) + timedelta(days=int(track_days))
            ).isoformat()


def build_plan_payload(*, trade_date: str, now: str,
                       codes: Iterable[str] = (),
                       holdings_codes: Iterable[str] = (),
                       holdings_gap: Optional[dict] = None,
                       recent_days: int = DEFAULT_RECENT_DAYS,
                       track_days: int = DEFAULT_TRACK_DAYS,
                       topics: Optional[Iterable[str]] = None,
                       sectors: Iterable[str] = (),
                       db_path=None) -> dict:
    """组装计划 payload（未落库）；``now`` 为首次获知时间。"""
    trade_date = normalize_trade_date(trade_date)
    if not now:
        raise CaptureError("invalid_timestamp", "计划需要首次获知时间")
    if int(recent_days) < 1:
        raise CaptureError("invalid_recent_days", "recent_days 必须 >= 1")
    if int(track_days) < 1:
        raise CaptureError("invalid_track_days", "track_days 必须 >= 1")
    selected_topics = normalize_topics(topics)
    deadline = _evaluation_deadline(trade_date, track_days)

    members: dict[str, PlanMember] = {}
    gaps: list[dict] = []

    def _add(code: str, *, reason: str, source: str, name: str = "",
             first_seen_at: Optional[str] = None,
             track_until: Optional[str] = None,
             extra_gaps: Iterable[str] = ()) -> None:
        code = str(code or "").strip()
        if not code:
            return
        existing = members.get(code)
        if existing is None:
            members[code] = PlanMember(
                code=code,
                name=name,
                first_seen_at=first_seen_at or now,
                entry_reason=reason,
                pool_sources=(source,),
                track_until=track_until or deadline,
                coverage_gaps=tuple(dict.fromkeys(extra_gaps)),
            )
            return
        sources = tuple(dict.fromkeys(existing.pool_sources + (source,)))
        gaps_for_member = tuple(dict.fromkeys(
            existing.coverage_gaps + tuple(extra_gaps)))
        incoming_first = first_seen_at or now
        # 事前评价截止与入池原因都取**最早一次登记**的声明：承接对象保留原期限，
        # 既不被后一日的默认期限延长，也不被它缩短。
        earlier = incoming_first < existing.first_seen_at
        members[code] = PlanMember(
            code=existing.code,
            name=existing.name or name,
            first_seen_at=min(existing.first_seen_at, incoming_first),
            entry_reason=reason if earlier else existing.entry_reason,
            pool_sources=sources,
            track_until=((track_until or deadline) if earlier
                         else existing.track_until),
            coverage_gaps=gaps_for_member,
        )

    dragon_dates, dragon_rows, dragon_error = _recent_dragon_rows(
        trade_date, int(recent_days), db_path)
    for row_date, code, name in dragon_rows:
        _add(code, reason=f"dragon:{row_date}", source=f"dragons:{row_date}",
             name=name)
    if dragon_error:
        gaps.append({"pool": "dragons", "reason": "pool_unreadable",
                     "detail": dragon_error})
    elif not dragon_rows:
        gaps.append({"pool": "dragons", "reason": "pool_empty",
                     "detail": "最近 N 日 dragons 镜像无记录（不补零、不猜）"})

    prep_payload, prep_meta = _latest_prep_snapshot(trade_date, db_path)
    prep_candidates: list[dict] = []
    if prep_payload is None:
        if prep_meta.get("error"):
            gaps.append({"pool": "prep", "reason": prep_meta["error"],
                         "trade_date": prep_meta.get("trade_date")})
        else:
            gaps.append({"pool": "prep", "reason": "prep_snapshot_missing",
                         "detail": "无严格早于计划日的 prep 快照"})
    else:
        raw = prep_payload.get("review_candidates")
        if not isinstance(raw, list):
            gaps.append({"pool": "prep", "reason": "review_candidates_missing",
                         "trade_date": prep_meta.get("trade_date")})
        else:
            prep_candidates = [item for item in raw if isinstance(item, dict)]
            for item in prep_candidates:
                _add(item.get("code"),
                     reason=f"prep:{prep_meta['trade_date']}",
                     source=f"prep:{prep_meta['trade_date']}",
                     name=str(item.get("name") or ""))
            truncation = prep_payload.get("candidate_truncation")
            if truncation:
                gaps.append({"pool": "prep", "reason": "upstream_truncated",
                             "detail": truncation})

    try:
        previous = store.previous_plan(trade_date, db_path=db_path)
    except store.ObservationStoreError as exc:
        previous = None
        gaps.append({"pool": "pending", "reason": "pool_unreadable",
                     "detail": str(exc)})
    pending_carried = 0
    if previous is not None:
        for item in previous.get("members") or []:
            member = PlanMember.from_dict(item)
            if not member.code or member.track_until < trade_date:
                continue
            _add(member.code, reason=member.entry_reason,
                 source=f"pending:{previous['plan_id']}",
                 name=member.name,
                 first_seen_at=member.first_seen_at,
                 track_until=member.track_until,
                 extra_gaps=member.coverage_gaps)
            pending_carried += 1

    holdings = [str(code).strip() for code in holdings_codes if str(code).strip()]
    for code in holdings:
        _add(code, reason="holdings", source="holdings")
    if holdings_gap is not None:
        gaps.append(dict(holdings_gap))

    explicit = [str(code).strip() for code in codes if str(code).strip()]
    for code in explicit:
        _add(code, reason="explicit", source="explicit")

    if not members:
        gaps.append({"pool": "members", "reason": "plan_empty",
                     "detail": "母集为空：不生成采集任务，也不宣称已观察"})

    ordered = sorted(members.values(), key=lambda item: item.code)
    task_config = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "topics": list(selected_topics),
        "sectors": sorted({str(item) for item in sectors if str(item)}),
        "slots": [dict(slot) for slot in DEFAULT_SLOTS],
        "recent_days": int(recent_days),
        "track_days": int(track_days),
    }
    pool = {
        "dragons": {"dates": dragon_dates, "rows": len(dragon_rows)},
        "prep": {"trade_date": prep_meta.get("trade_date"),
                 "candidates": len(prep_candidates),
                 "error": prep_meta.get("error")},
        "holdings": {"codes": len(holdings),
                     "source": "explicit" if holdings_gap is None
                     else "unavailable"},
        "pending": {"plan_id": previous["plan_id"] if previous else None,
                    "carried": pending_carried},
        "explicit": len(explicit),
        "gaps": gaps,
    }
    return {
        "trade_date": trade_date,
        "pool": pool,
        "members": [member.to_dict() for member in ordered],
        "task_config": task_config,
        "config_version": PLAN_SCHEMA_VERSION,
    }


def freeze_plan(payload: dict, *, now: str, db_path=None) -> dict:
    """落库一版计划（同内容幂等；不同内容追加 revision）。"""
    return store.save_plan(
        trade_date=payload["trade_date"],
        pool=payload["pool"],
        members=payload["members"],
        task_config=payload["task_config"],
        config_version=payload["config_version"],
        created_at=now,
        db_path=db_path,
    )


def code_topics(task_config: dict) -> tuple[str, ...]:
    """计划里以个股代码为范围的采集主题。"""
    topics = task_config.get("topics") or list(TOPICS)
    return tuple(topic for topic in topics if topic in CODE_TOPICS)
