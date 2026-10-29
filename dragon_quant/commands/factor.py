"""factor 命令：因子层 build / list / query / status / export / eval / fin。"""

from __future__ import annotations

import json
import sys


def handle(args, services):
    """因子层命令：build / list / query / status / export / eval / fin。"""
    action = getattr(args, "factor_action", None)
    if action == "fin":
        _cmd_factor_fin(args, services)
        return
    # 门面只提供 13 个公共名；CLI 的用例包装（日期归一/ok 退出码语义）在文档化
    # 公共子模块 ``infra.factor.service``。运行期导入保证 service.* 的 patch 目标仍生效。
    from dragon_quant.infra.factor import CleanConfig, service

    as_json = getattr(args, "json", False)
    try:
        if action == "build":
            payload = service.build(
                date=args.date,
                date_from=getattr(args, "from_date", None),
                date_to=getattr(args, "to_date", None),
                only=args.only,
                all_factors=getattr(args, "all_factors", False),
                services=services,
            )
        elif action == "list":
            payload = {"ok": True, "factors": service.list_factors()}
        elif action == "query":
            payload = service.query(
                date=args.date,
                date_from=getattr(args, "from_date", None),
                date_to=getattr(args, "to_date", None),
                name=args.name,
                code=args.code,
                version=args.version,
            )
        elif action == "status":
            payload = service.status()
        elif action == "export":
            names = service.parse_only(args.names)
            if not names:
                raise ValueError("export 至少需要一个 --name")
            low, high = (float(part) for part in str(args.limits).split(",", 1))
            if args.clean:
                clean = CleanConfig(
                    missing=args.missing or "nan",
                    winsorize=args.winsorize or "mad",
                    limits=(low, high),
                    normalize=args.normalize or "rank")
            elif args.missing or args.winsorize or args.normalize:
                clean = CleanConfig(
                    missing=args.missing or "nan",
                    winsorize=args.winsorize or "none",
                    limits=(low, high),
                    normalize=args.normalize or "none")
            else:
                # identity_clean() 等价：原始值视图（不缩尾、不归一）。
                clean = CleanConfig(
                    missing="nan", winsorize="none", normalize="none")
            payload = service.export_matrix(
                args.from_date, args.to_date, names=names,
                version=args.version, codes=service.parse_only(args.codes),
                clean=clean, fmt=args.fmt, out_path=args.out_path)
        elif action == "eval":
            from dragon_quant.review_account.main import MarketData
            provider = getattr(getattr(services, "providers", None), "xueqiu", None)
            data = MarketData(provider=provider) if provider is not None else MarketData()
            payload = service.evaluate_factor(
                args.name, args.from_date, args.to_date, data=data,
                horizon=args.horizon, groups=args.groups, version=args.version,
                min_days=args.min_days, min_cross_section=args.min_cross_section,
                pit_policy=args.pit_policy)
        elif action == "decision-review":
            from dragon_quant.review_account.main import MarketData
            provider = getattr(getattr(services, "providers", None), "xueqiu", None)
            data = MarketData(provider=provider) if provider is not None else MarketData()
            payload = service.decision_review(
                args.source, args.from_date, args.to_date,
                horizons=args.horizons, cost_bps=args.cost_bps,
                min_days=args.min_days,
                min_group_samples=args.min_group_samples,
                include_samples=args.include_samples, data=data)
        else:
            _emit_factor_error(
                {"error": "未知 factor 子命令（build/list/query/status/export/eval/fin）"},
                as_json, 2)
            return
    except ValueError as exc:
        _emit_factor_error({"error": str(exc)}, as_json, 2)
        return
    except Exception as exc:  # schema/IO 等运行时失败：不抛栈
        _emit_factor_error({"error": f"{type(exc).__name__}: {exc}"}, as_json, 1)
        return

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_factor_human(action, payload)
    if not payload.get("ok"):
        sys.exit(1)


def _cmd_factor_fin(args, services):
    """``factor fin``：sync（snapshot/backfill）/ calibrate / status。"""
    # 同 handle：经文档化公共子模块 infra.factor.service 调用 CLI 用例包装。
    from dragon_quant.infra.factor import service

    sub = getattr(args, "fin_action", None)
    as_json = getattr(args, "json", False)
    try:
        if sub == "sync":
            payload = service.fin_sync(
                snapshot=getattr(args, "snapshot", False),
                backfill=getattr(args, "backfill", False),
                date=getattr(args, "date", None),
                from_period=getattr(args, "from_period", None),
                to_period=getattr(args, "to_period", None),
                force=getattr(args, "force", False),
                host=getattr(args, "host", None),
                services=services,
            )
        elif sub == "calibrate":
            payload = service.fin_calibrate(
                sample=getattr(args, "sample", 20),
                period=getattr(args, "period", None),
                host=getattr(args, "host", None),
                services=services,
            )
        elif sub == "status":
            payload = service.fin_status()
        else:
            _emit_factor_error(
                {"error": "未知 factor fin 子命令（sync/calibrate/status）"},
                as_json, 2)
            return
    except ValueError as exc:
        _emit_factor_error({"error": str(exc)}, as_json, 2)
        return
    except Exception as exc:
        _emit_factor_error({"error": f"{type(exc).__name__}: {exc}"}, as_json, 1)
        return

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_fin_human(sub, payload)
    if not payload.get("ok"):
        sys.exit(1)


def _emit_factor_error(payload: dict, as_json: bool, exit_code: int) -> None:
    """factor 错误输出：默认人读一行到 stderr；--json 保持 JSON 到 stdout。

    与 prep/auction 同策略：参数错误 exit 2，运行时失败 exit 1。
    """
    body = {"ok": False, **payload}
    if as_json:
        print(json.dumps(body, ensure_ascii=False, indent=2))
    else:
        print(f"❌ 因子操作失败：{body.get('error', '未知错误')}", file=sys.stderr)
    sys.exit(exit_code)


def _print_factor_human(action: str, payload: dict) -> None:
    """factor 子命令的人读输出（默认模式；JSON 契约不受影响）。"""
    if action == "list":
        print(f"{'因子':28s} {'版本':14s} {'源':6s} {'阶段':8s} {'PIT':10s} 默认")
        print("-" * 78)
        for item in payload.get("factors") or []:
            print(f"{item['name']:28s} {item['version']:14s} {item['source']:6s} "
                  f"{item['available_phase']:8s} {item['pit_rule']:10s} "
                  f"{'开' if item['default_enabled'] else '关'}")
        return

    if action == "build":
        for report in payload.get("reports") or []:
            entries = list((report.get("factors") or {}).values())
            counts: dict[str, int] = {}
            for entry in entries:
                counts[entry.get("status", "?")] = counts.get(entry.get("status", "?"), 0) + 1
            status_text = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "-"
            rows = sum(int(entry.get("rows") or 0) for entry in entries)
            sector = report.get("sector_snapshot") or {}
            print(f"{report.get('trade_date')}: 因子 {len(entries)} 个 / 行 {rows} "
                  f"[{status_text}] 主板块 {sector.get('rows', 0)} "
                  f"build_id={report.get('build_id')}")
            for warning in report.get("warnings") or []:
                print(f"  ⚠️ {warning}")
        if not payload.get("reports"):
            print("(区间内无扫描记录，未执行 build)")
        if payload.get("mode") == "range":
            print(f"合计 {len(payload.get('dates') or [])} 个交易日，"
                  f"build_ids={payload.get('build_ids')}")
        for warning in payload.get("warnings") or []:
            print(f"⚠️ {warning}")
        return

    if action == "query":
        if payload.get("mode") == "day":
            values = payload.get("values") or {}
            if not values:
                print(f"{payload.get('trade_date')}: (无因子值)")
            for code in sorted(values):
                for name in sorted(values[code]):
                    available = ((payload.get("available_at") or {}).get(code) or {}).get(name)
                    print(f"{payload.get('trade_date')} {code} {name} "
                          f"{values[code][name]:g} available_at={available}")
        else:
            rows = payload.get("rows") or []
            if not rows:
                print(f"{payload.get('factor')}: (区间内无因子值)")
            for row in rows:
                print(f"{row['trade_date']} {row['code']} {row['factor_version']} "
                      f"{row['value']:g} available_at={row['available_at']}")
        print(f"resolved_versions={payload.get('resolved_versions')}")
        return

    if action == "status":
        values = payload.get("factor_values") or {}
        registry = payload.get("factor_registry") or {}
        sector = payload.get("factor_sector_snapshots") or {}
        runs = payload.get("build_runs") or {}
        last = runs.get("last") or {}
        print(f"因子值：{values.get('rows', 0)} 行 / {values.get('factors', 0)} 因子 / "
              f"{values.get('dates', 0)} 交易日 "
              f"({values.get('first_date')} ~ {values.get('last_date')})")
        print(f"注册表快照：{registry.get('rows', 0)} 行 / {registry.get('factors', 0)} 因子")
        print(f"主板块快照：{sector.get('rows', 0)} 行 / {sector.get('dates', 0)} 交易日 "
              f"{sector.get('by_category') or {}}")
        print(f"build 审计：{runs.get('rows', 0)} 次"
              + (f"，最近 build_id={last.get('id')} @ {last.get('trade_date')}" if last else ""))
        missing = [f["name"] for f in payload.get("factors") or []
                   if not f.get("registered_version")]
        if missing:
            print(f"未注册（尚未 build）：{', '.join(missing)}")
        return

    if action == "export":
        print(f"导出矩阵：{payload.get('date_from')} ~ {payload.get('date_to')}，"
              f"{len(payload.get('dates') or [])} 日 × {len(payload.get('codes') or [])} 只，"
              f"列 {len(payload.get('columns') or [])} 个")
        print(f"resolved_versions={payload.get('resolved_versions')}")
        print(f"missing_rate_by_factor={payload.get('missing_rate_by_factor')}")
        if payload.get("winsorize_skipped"):
            print(f"⚠️ winsorize_skipped={payload.get('winsorize_skipped')}")
        if payload.get("normalize_skipped"):
            print(f"⚠️ normalize_skipped={payload.get('normalize_skipped')}")
        if payload.get("dropped_rows"):
            print(f"drop 缺失行：{payload.get('dropped_rows')} 行")
        print(f"out={payload.get('out_path')} meta={payload.get('meta_path')}")
        for warning in payload.get("warnings") or []:
            print(f"⚠️ {warning}")
        return

    if action == "eval":
        print(f"评估 {payload.get('factor')}@{payload.get('resolved_version')}："
              f"{payload.get('status')}"
              + (f"（{payload.get('reason')}）" if payload.get("reason") else ""))
        print(f"horizon={payload.get('horizon')} nw_lag={payload.get('nw_lag')} "
              f"days={payload.get('days')} obs={payload.get('observations')} "
              f"effective_days={payload.get('effective_independent_days')}")
        ic = payload.get("ic") or {}
        print(f"RankIC mean={ic.get('mean')} std={ic.get('std')} "
              f"ICIR={ic.get('icir')} t={ic.get('t_stat')} nw_t={ic.get('nw_t_stat')}")
        coverage = payload.get("coverage") or {}
        print(f"coverage mean={coverage.get('mean')} min={coverage.get('min')} "
              f"missing_rate={coverage.get('missing_rate')}")
        for group in payload.get("groups") or []:
            print(f"  group={group.get('group')} n={group.get('n')} "
                  f"mean_return={group.get('mean_return')} "
                  f"hit_rate={group.get('hit_rate')}")
        print(f"pit_policy={payload.get('pit_policy')} "
              f"pit_basis_mix={payload.get('pit_basis_mix')}")
        for warning in payload.get("warnings") or []:
            print(f"⚠️ {warning}")
        for item in payload.get("degraded") or []:
            print(f"⚠️ degraded: {item}")
        return

    if action == "decision-review":
        _print_decision_review(payload)
        return


def _print_decision_review(payload: dict) -> None:
    """``factor decision-review`` 人读摘要（默认模式；JSON 契约不受影响）。"""
    source = payload.get("source", "?")
    date_from = payload.get("date_from")
    date_to = payload.get("date_to")
    snapshots = payload.get("snapshots") or {}
    config = payload.get("config") or {}
    requested = snapshots.get("requested_days")
    usable = snapshots.get("usable")
    unsupported = snapshots.get("unsupported_version") or 0
    cost = config.get("cost_bps")
    total_samples = 0
    for group in payload.get("groups") or []:
        for entry in (group.get("horizons") or {}).values():
            total_samples += int(entry.get("observations") or 0)
    period = (f"{usable}/{requested}" if requested is not None
              else str(snapshots.get("with_snapshot") or 0))
    cost_text = f"{cost:g}" if isinstance(cost, (int, float)) else "?"
    print(f"决策回测 {source} {date_from}~{date_to}｜快照 {period} 日"
          f"（unsupported {unsupported}）｜样本 {total_samples}｜成本 {cost_text}bp")

    pair_labels = {"prep": ("推荐", "不推荐"), "auction": ("强", "弱")}
    for horizon in config.get("horizons") or []:
        key = str(horizon)
        print(f"horizon={horizon}")
        print("  组          方向  命中      有效/观测   覆盖     "
              "mean净收益  quality")
        for group in payload.get("groups") or []:
            entry = (group.get("horizons") or {}).get(key) or {}
            arrow = {"up": "↑", "down": "↓"}.get(
                group.get("expected_direction"), "-")
            hit = entry.get("direction_hit_rate")
            hit_text = f"{hit * 100:.1f}%" if isinstance(hit, (int, float)) else "-"
            valid = entry.get("valid") or 0
            observations = entry.get("observations") or 0
            coverage = entry.get("coverage")
            coverage_text = (f"{coverage * 100:.1f}%"
                             if isinstance(coverage, (int, float)) else "-")
            mean_net = entry.get("mean_net_return_pct")
            mean_text = (f"{mean_net:+.2f}%"
                         if isinstance(mean_net, (int, float)) else "-")
            quality = entry.get("quality") or "-"
            print(f"  {group.get('label', '-'):<10}{arrow:<4}{hit_text:<10}"
                  f"{f'{valid}/{observations}':<12}{coverage_text:<8}"
                  f"{mean_text:<12}{quality}")
        spread = (payload.get("spreads") or {}).get(key) or {}
        pair = spread.get("pair") or []
        if len(pair) == 2:
            left, right = pair_labels.get(source, (pair[0], pair[1]))
            value = spread.get("value")
            value_text = (f"{value:+.2f}pp"
                          if isinstance(value, (int, float)) else "-")
            print(f"  差值 {left}−{right}: {value_text}")

    print("参考线 65%（只描述，不做策略判定）；quality 只描述样本充足度")
    if config.get("include_samples"):
        print("（--include-samples 仅影响 --json；人读模式不打印逐样本账本）")
    for warning in payload.get("warnings") or []:
        print(f"⚠️ {warning}")
    print(f"说明：命中/涨跌按扣除 {cost_text}bp 后的净收益判定；"
          "flat=净收益恰为 0（保本）；Wilson 区间受同日/相邻 horizon 相关性影响，偏窄。")


def _print_fin_human(action: str, payload: dict) -> None:
    if action == "sync":
        mode = payload.get("mode")
        if mode == "snapshot":
            print(f"财务观测 {payload.get('trade_date')}：候选 {payload.get('codes', 0)} 只，"
                  f"成功 {payload.get('ok', 0)}，状态 {payload.get('status')}")
            for updated, count in sorted((payload.get("updated_dates") or {}).items()):
                print(f"  updated_date={updated}: {count}")
            for error in payload.get("errors") or []:
                print(f"  ❌ {error.get('code')}: {error.get('error')}")
            return
        print(f"财务回填：报告期 {payload.get('periods') or '-'}")
        print(f"  下载 {payload.get('downloaded', 0)} / 跳过 {payload.get('skipped', 0)} / "
              f"新增行 {payload.get('inserted', 0)} / 值未变 {payload.get('unchanged', 0)} / "
              f"撤销 {payload.get('retracted', 0)} / 观测失配 {payload.get('observed_unmatched', 0)}")
        if payload.get("layouts"):
            print(f"  布局：{payload.get('layouts')}")
        for failure in payload.get("failed") or []:
            print(f"  ❌ {failure.get('period')} {failure.get('md5')}: {failure.get('error')}")
        return

    if action == "calibrate":
        print(f"字段校准 {payload.get('layout_key')}：{payload.get('status')} "
              f"(sample={payload.get('sample_n')}, period={payload.get('report_period')})")
        anchors = payload.get("anchors") or {}
        print(f"  锚定：恒等式={anchors.get('identity')} 层级不等式={anchors.get('hierarchy')}")
        for field, spec in sorted((payload.get("mapping") or {}).items()):
            print(f"  {field}: index={spec.get('index')} scale={spec.get('scale')}")
        return

    if action == "status":
        observed = payload.get("observed") or {}
        files = payload.get("report_files") or {}
        records = payload.get("report_records") or {}
        mix = payload.get("pit_basis_mix") or {}
        print(f"观测快照：{observed.get('rows', 0)} 行 / {observed.get('dates', 0)} 日 "
              f"(最近 {observed.get('last_date')})")
        print(f"期文件：{files.get('rows', 0)} 行 / {files.get('periods', 0)} 期 "
              f"{files.get('by_status') or {}}")
        print(f"账本：{records.get('rows', 0)} 行 / {records.get('periods', 0)} 期 / "
              f"{records.get('codes', 0)} code / 撤销 {records.get('retracted', 0)} "
              f"{records.get('by_basis') or {}}")
        print(f"PIT 口径：observed 占比 {mix.get('observed_ratio')}，"
              f"回填不可证伪 {mix.get('restatement_unverifiable', 0)} 行")
        print(f"校准布局：{payload.get('verified_layouts') or '-'}")
        print(f"最近报告期：{payload.get('latest_period') or '-'}"
              f"（下一截止日 {payload.get('next_deadline') or '-'}，"
              f"逾期={payload.get('overdue')}）")
        print(f"依赖：pandas={((payload.get('dependencies') or {}).get('pandas'))}")
        return


