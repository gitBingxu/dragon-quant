"""表规格注册表与规格驱动的资产源（M6 全表同步）。

设计见技术方案 §4.3 / §5 / §6.4 / §8.2：
  - **注册表模式**：``TABLE_SPECS`` 是同步单元的唯一事实源，新增表 = 加一行规格；
  - **模板方法**：``CompositeTableSource`` 固定「枚举分区 → 读取 → 组 shard →
    水位 → 恢复 → 行合并」骨架，差异点由 ``TableSpec`` 与少量 hook 提供；
  - **策略**：``partition_kind`` 决定分区枚举与 ``--from/--to`` 语义，
    ``merge_granularity`` 决定 pull 决策，``volatile`` 决定新鲜度策略；
  - **组合**：一个 domain source（``factor`` / ``fin``）聚合多张表。

分层约束：本模块只读 ``assets.db``（经 ``storage.assets_db`` 的连接门面），
不 import ``dragon_quant.infra.factor`` / ``finance`` 的业务模块，也不 import ``scorers`` / ``providers``。
"""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass

from dragon_quant.infra.assets.sync import codec
from dragon_quant.infra.assets.sync.errors import IntegrityError
from dragon_quant.infra.assets.sync.models import AssetShard, RestoreReport, ShardBatch
from dragon_quant.infra.assets import db as assets_db

STATIC_FACTOR = "static-factor"
STATIC_FIN = "static-fin"

_BUILD_RUN_UID = "build_run_uid"


@dataclass(frozen=True)
class TableSpec:
    """一个同步单元的同步契约（表 / 分区 / 身份 / 列 / 水位 / 合并粒度）。"""

    source: str
    asset_type: str
    table: str
    partition_kind: str            # date | period | period_raw | static
    partition_column: str | None   # date: trade_date/ex_date；period(_raw): report_period
    columns: tuple[str, ...]       # 语义列（进内容哈希），顺序即 payload 列序
    identity: tuple[str, ...]      # 行身份键（恢复时比对）；hook 单元可为空
    static_partition: str | None = None
    side_columns: tuple[str, ...] = ()      # created_at 等旁路列
    json_columns: tuple[str, ...] = ()      # 文本列：导出解析为对象、导入序列化
    order_by: tuple[str, ...] = ()          # 确定性排序（决定内容哈希）
    volatile: bool = False                  # push 时跳过水位短路，强制重读
    clock_column: str | None = None         # 水位 max 列
    size_expr: str = "0"                    # 水位字节表达式（SQL 表达式）
    merge_granularity: str = "row"
    filter_sql: str = ""                    # 可选 WHERE
    identity_hook: str | None = None        # "build_run_uid"
    drift_ok: bool = False                  # 仅追加式单元允许 status 判 drift
    null_fill_columns: tuple[str, ...] = ()  # 本地 NULL 时可由远端补齐的列

    @property
    def unit_key(self) -> str:
        return f"{self.source}:{self.asset_type}"

    @property
    def static_key(self) -> str:
        """static 分区的字面量键。

        非 static 单元访问属注册错误：``_validate_specs()`` 在模块导入期已拦截
        （``partition_kind="static"`` 必须声明 ``static_partition``），因此
        ``RuntimeError`` 分支在正常运行期不可达。
        """
        if self.static_partition is None:
            raise RuntimeError(
                f"static 单元必须声明 static_partition: {self.unit_key}")
        return self.static_partition


TABLE_SPECS: tuple[TableSpec, ...] = (
    # ── 快照（volatile：原地 UPSERT）────────────────────────────────────────
    TableSpec(
        source="prepsnap", asset_type="snapshots", table="prep_snapshots",
        partition_kind="date", partition_column="trade_date",
        columns=("trade_date", "source", "result_json"),
        identity=("trade_date", "source"),
        side_columns=("created_at",), json_columns=("result_json",),
        order_by=("source",), volatile=True,
        clock_column="created_at", size_expr="LENGTH(result_json)",
    ),
    TableSpec(
        source="auctionsnap", asset_type="snapshots", table="auction_snapshots",
        partition_kind="date", partition_column="trade_date",
        columns=("trade_date", "code", "source", "analysis_json"),
        identity=("trade_date", "code", "source"),
        side_columns=("created_at",), json_columns=("analysis_json",),
        order_by=("source", "code"), volatile=True,
        clock_column="created_at", size_expr="LENGTH(analysis_json)",
    ),
    # ── dragons 镜像（volatile：review 回写只 UPDATE、不刷新 created_at）──────
    TableSpec(
        source="dragons", asset_type="mirror", table="dragons",
        partition_kind="date", partition_column="trade_date",
        columns=("trade_date", "code", "name", "scan_id", "rank", "composite_score",
                 "board_count", "open_px", "close_px", "high_px", "low_px", "pct",
                 "turnover_rate", "amount", "market_cap", "concepts_json",
                 "report_text", "is_true_dragon", "version", "buy_date", "buy_price",
                 "max_return_5d", "max_drawdown_5d", "max_return_hold_days",
                 "review_status", "source"),
        identity=("trade_date", "code"),
        side_columns=("created_at",), json_columns=("concepts_json",),
        order_by=("code",), volatile=True,
        clock_column="created_at",
        size_expr="LENGTH(COALESCE(report_text,''))"
                  "+LENGTH(COALESCE(concepts_json,''))",
    ),
    # ── 因子表（非 volatile：DELETE+INSERT 必刷新时钟列）─────────────────────
    TableSpec(
        source="factor", asset_type="values", table="factor_values",
        partition_kind="date", partition_column="trade_date",
        columns=("trade_date", "code", "factor_name", "factor_version", "value",
                 "available_at", "source", "source_scan_id", "source_meta_json"),
        identity=("trade_date", "code", "factor_name", "factor_version"),
        side_columns=("computed_at",), json_columns=("source_meta_json",),
        order_by=("factor_name", "factor_version", "code"),
        clock_column="computed_at",
        size_expr="LENGTH(COALESCE(source_meta_json,''))"
                  "+LENGTH(COALESCE(source_scan_id,''))",
    ),
    TableSpec(
        source="factor", asset_type="build_runs", table="factor_build_runs",
        partition_kind="date", partition_column="trade_date",
        columns=("trade_date", "started_at", "finished_at", "report_json"),
        identity=(), identity_hook=_BUILD_RUN_UID,
        side_columns=("created_at",), json_columns=("report_json",),
        order_by=("started_at", "report_json"),
        clock_column="created_at", size_expr="LENGTH(report_json)",
        drift_ok=True,
    ),
    TableSpec(
        source="factor", asset_type="sector", table="factor_sector_snapshots",
        partition_kind="date", partition_column="trade_date",
        columns=("trade_date", "code", "industry_code", "industry_name",
                 "category", "source", "scan_id"),
        identity=("trade_date", "code", "category"),
        side_columns=("created_at",),
        order_by=("code", "category"), clock_column="created_at",
        size_expr="LENGTH(COALESCE(industry_code,''))"
                  "+LENGTH(COALESCE(industry_name,''))"
                  "+LENGTH(COALESCE(scan_id,''))",
    ),
    TableSpec(
        source="factor", asset_type="registry", table="factor_registry",
        partition_kind="static", partition_column=None,
        static_partition=STATIC_FACTOR,
        columns=("factor_name", "factor_version", "label", "frequency", "source",
                 "depends_on_json", "lookback", "direction", "unit",
                 "available_phase", "pit_rule", "scope_json", "params_json",
                 "default_enabled", "valid_from", "deprecated_at", "owner",
                 "description", "spec_hash"),
        identity=("factor_name", "factor_version"),
        side_columns=("created_at",),
        json_columns=("depends_on_json", "scope_json", "params_json"),
        order_by=("factor_name", "factor_version"), clock_column="created_at",
        size_expr="LENGTH(COALESCE(depends_on_json,''))"
                  "+LENGTH(COALESCE(scope_json,''))"
                  "+LENGTH(COALESCE(params_json,''))",
    ),
    TableSpec(
        source="factor", asset_type="model", table="factor_model_snapshots",
        partition_kind="static", partition_column=None,
        static_partition=STATIC_FACTOR,
        columns=("model_name", "model_version", "params_json",
                 "factor_versions_json"),
        identity=("model_name", "model_version"),
        side_columns=("created_at",),
        json_columns=("params_json", "factor_versions_json"),
        order_by=("model_name", "model_version"), clock_column="created_at",
        size_expr="LENGTH(params_json)+LENGTH(factor_versions_json)",
    ),
    # ── 财务表（volatile：原地 UPSERT / UPDATE / 撤销）───────────────────────
    TableSpec(
        source="fin", asset_type="observed", table="fin_observed_snapshots",
        partition_kind="date", partition_column="trade_date",
        columns=("trade_date", "code", "updated_date", "period", "values_json",
                 "values_hash", "captured_at"),
        identity=("trade_date", "code"),
        json_columns=("values_json",), order_by=("code",), volatile=True,
        clock_column="captured_at", size_expr="LENGTH(values_json)",
        drift_ok=True,
    ),
    TableSpec(
        source="fin", asset_type="report_files", table="fin_report_files",
        partition_kind="period", partition_column="report_period",
        columns=("report_period", "filename", "md5", "filesize", "first_seen_at",
                 "status", "records_count", "error"),
        identity=("report_period", "md5"),
        order_by=("md5",), volatile=True, clock_column="first_seen_at",
        size_expr="LENGTH(status)+COALESCE(records_count,0)"
                  "+LENGTH(COALESCE(error,''))",
    ),
    TableSpec(
        source="fin", asset_type="report_raw", table="fin_report_files",
        partition_kind="period_raw", partition_column="report_period",
        columns=("report_period", "md5", "raw_blob"),
        identity=("report_period", "md5"),
        order_by=("md5",), volatile=True, clock_column="first_seen_at",
        size_expr="LENGTH(raw_blob)", filter_sql="raw_blob IS NOT NULL",
        null_fill_columns=("raw_blob",),
    ),
    TableSpec(
        source="fin", asset_type="records", table="fin_report_records",
        partition_kind="period", partition_column="report_period",
        columns=("report_period", "code", "file_md5", "layout_key",
                 "values_json", "available_at", "pit_basis", "first_seen_at",
                 "retracted_at"),
        identity=("report_period", "code", "file_md5"),
        json_columns=("values_json",), order_by=("file_md5", "code"),
        volatile=True, clock_column="available_at",
        size_expr="LENGTH(values_json)+LENGTH(COALESCE(retracted_at,''))",
        drift_ok=True,
    ),
    TableSpec(
        source="fin", asset_type="calibrations", table="fin_field_calibrations",
        partition_kind="static", partition_column=None,
        static_partition=STATIC_FIN,
        columns=("layout_key", "num_fields", "mapping_json", "confidence_json",
                 "status", "sample_n"),
        identity=("layout_key",),
        side_columns=("calibrated_at",),
        json_columns=("mapping_json", "confidence_json"),
        order_by=("layout_key",), volatile=True, clock_column="calibrated_at",
        size_expr="LENGTH(mapping_json)+LENGTH(confidence_json)",
    ),
    TableSpec(
        source="fin", asset_type="xdxr", table="fin_xdxr_records",
        partition_kind="date", partition_column="ex_date",
        columns=("code", "ex_date", "category", "payload_json"),
        identity=("code", "ex_date", "category"),
        side_columns=("created_at",), json_columns=("payload_json",),
        order_by=("ex_date", "code", "category"), volatile=True,
        clock_column="created_at", size_expr="LENGTH(payload_json)",
        drift_ok=True,
    ),
)


def _validate_specs() -> None:
    sources = {}
    for spec in TABLE_SPECS:
        if not spec.source or "_" in spec.source:
            raise RuntimeError(f"资产源名称不得为空或包含 '_': {spec.source}")
        if spec.partition_kind not in codec.PARTITION_KINDS:
            raise RuntimeError(f"非法分区 kind: {spec.asset_type}:{spec.partition_kind}")
        if not spec.columns:
            raise RuntimeError(f"同步单元缺少列: {spec.unit_key}")
        if not spec.identity and not spec.identity_hook:
            raise RuntimeError(f"同步单元缺少身份键: {spec.unit_key}")
        if spec.identity and not set(spec.identity) <= set(spec.columns):
            raise RuntimeError(f"身份列必须是列集的子集: {spec.unit_key}")
        if spec.partition_kind == "static":
            if not spec.static_partition or spec.partition_column:
                raise RuntimeError(f"static 单元必须声明 static_partition: {spec.unit_key}")
            if not codec.is_partition(spec.static_partition):
                raise RuntimeError(f"static 分区键非法: {spec.static_partition}")
        else:
            if not spec.partition_column:
                raise RuntimeError(f"非 static 单元必须声明 partition_column: {spec.unit_key}")
            if spec.partition_column not in spec.columns:
                raise RuntimeError(f"分区列必须在列集中: {spec.unit_key}")
            if spec.static_partition:
                raise RuntimeError(f"非 static 单元不得声明 static_partition: {spec.unit_key}")
        if ";" in spec.filter_sql:
            raise RuntimeError(f"filter_sql 不得包含多条语句: {spec.unit_key}")
        if set(spec.json_columns) and not set(spec.json_columns) <= set(spec.columns):
            raise RuntimeError(f"json 列必须在列集中: {spec.unit_key}")
        if set(spec.side_columns) & set(spec.columns):
            raise RuntimeError(f"旁路列不得出现在语义列集中: {spec.unit_key}")
        if spec.merge_granularity not in ("row", "shard"):
            raise RuntimeError(f"非法合并粒度: {spec.unit_key}")
        if spec.unit_key in sources:
            raise RuntimeError(f"同步单元键重复: {spec.unit_key}")
        sources[spec.unit_key] = spec


_validate_specs()

_SPECS_BY_KEY = {spec.unit_key: spec for spec in TABLE_SPECS}


def spec_for(source: str, asset_type: str) -> TableSpec:
    return _SPECS_BY_KEY[f"{source}:{asset_type}"]


def specs_for_source(source: str) -> tuple[TableSpec, ...]:
    return tuple(spec for spec in TABLE_SPECS if spec.source == source)


def has_spec(source: str, asset_type: str) -> bool:
    return f"{source}:{asset_type}" in _SPECS_BY_KEY


# ─── 单元格编解码 ────────────────────────────────────────────────────────────


def _dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)


def build_run_uid(values) -> str:
    """``factor_build_runs`` 的内容身份：剔除本机自增 ``id`` / ``build_ids``。"""
    report = values.get("report_json")
    if isinstance(report, str):
        report = json.loads(report)
    stable = dict(report) if isinstance(report, dict) else report
    if isinstance(stable, dict):
        stable.pop("build_ids", None)
    return codec.hashes(codec.canonical_json({
        "trade_date": values.get("trade_date"),
        "started_at": values.get("started_at"),
        "finished_at": values.get("finished_at"),
        "report_stable": stable,
    }))[0]


def _encode_cell(spec: TableSpec, column: str, value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if column in spec.json_columns:
        if value is None:
            return None
        if isinstance(value, (dict, list, int, float, bool)):
            return value
        return json.loads(value)
    return value


def _decode_cell(spec: TableSpec, column: str, value):
    if column in spec.json_columns:
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return value
    return value


def _serialize_cell(spec: TableSpec, column: str, value):
    if column in spec.json_columns:
        return None if value is None else _dumps(value)
    return value


def _table_info(conn: sqlite3.Connection, table: str) -> dict[str, tuple[bool, object]]:
    info = {}
    for row in conn.execute(f"PRAGMA table_info({table})"):
        # cid, name, type, notnull, dflt_value, pk
        info[row[1]] = (bool(row[3]), row[4])
    return info


def _partition_value(spec: TableSpec, partition: str):
    info = codec.parse_partition(partition)
    if spec.partition_kind == "static":
        return None
    return info.sort_date if spec.partition_kind in ("period", "period_raw") else info.key


def _where_clause(spec: TableSpec, partition: str,
                  *, include_filter: bool = True) -> tuple[str, tuple]:
    clauses = []
    params: list = []
    if spec.partition_kind != "static":
        clauses.append(f"{spec.partition_column} = ?")
        params.append(_partition_value(spec, partition))
    if include_filter and spec.filter_sql:
        clauses.append(f"({spec.filter_sql})")
    if not clauses:
        return "", ()
    return " WHERE " + " AND ".join(clauses), tuple(params)


def _order_clause(spec: TableSpec, columns) -> str:
    usable = [col for col in spec.order_by if col in columns]
    return (" ORDER BY " + ", ".join(usable)) if usable else ""


def _partition_matches(spec: TableSpec, partition: str) -> bool:
    try:
        info = codec.parse_partition(partition)
    except ValueError:
        return False
    if spec.partition_kind != info.kind:
        return False
    if spec.partition_kind == "static":
        return spec.static_partition == partition
    return True


def _row_identity(spec: TableSpec, values: dict) -> tuple:
    if spec.identity_hook == _BUILD_RUN_UID:
        return (build_run_uid(values),)
    return tuple(_norm_identity(values.get(col)) for col in spec.identity)


def _norm_identity(value):
    if isinstance(value, (dict, list)):
        return _dumps(value)
    return value


# ─── 规格驱动的资产源 ────────────────────────────────────────────────────────


class CompositeTableSource:
    """一个 domain source（可聚合多张表），对外仍表现为一个 ``AssetSource``。"""

    name = ""
    merge_granularity = "row"

    def __init__(self, db_path=None):
        self.db_path = db_path

    # ── 连接门面（经模块属性访问，保证测试 patch assets_db._connect 生效）──
    def _open_read(self):
        conn = assets_db._connect(create=False, path=self.db_path)
        if conn is not None:
            assets_db._ensure_schema(conn, migrate=False)
        return conn

    @property
    def specs(self) -> tuple[TableSpec, ...]:
        return specs_for_source(self.name)

    def merge_granularity_for(self, asset_type: str) -> str:
        return "row"

    # ── 协议兼容别名 ───────────────────────────────────────────────────────
    def list_dates(self) -> list[str]:
        return [p for p in self.partitions() if codec.partition_kind(p) == "date"]

    def read_date(self, partition: str) -> ShardBatch:
        return self.read_partition(partition)

    def partition_kind(self, partition: str) -> str:
        return codec.partition_kind(partition)

    def partitions(self) -> list[str]:
        conn = self._open_read()
        if conn is None:
            return []
        found: set[str] = set()
        try:
            for spec in self.specs:
                if spec.partition_kind == "static":
                    where, params = _where_clause(spec, spec.static_key)
                    row = conn.execute(
                        f"SELECT 1 FROM {spec.table}{where} LIMIT 1", params).fetchone()
                    if row is not None:
                        found.add(spec.static_key)
                    continue
                # 分区枚举只保留 filter_sql（不带单分区等值条件）。
                clauses = [f"({spec.filter_sql})"] if spec.filter_sql else []
                sql = f"SELECT DISTINCT {spec.partition_column} FROM {spec.table}"
                if clauses:
                    sql += " WHERE " + " AND ".join(clauses)
                sql += f" ORDER BY {spec.partition_column}"
                for (value,) in conn.execute(sql, ()):
                    if not value:
                        continue
                    found.add(self._partition_key(spec, value))
        finally:
            conn.close()
        return sorted(found, key=codec.partition_sort_key)

    @staticmethod
    def _partition_key(spec: TableSpec, value: str) -> str:
        if spec.partition_kind == "period":
            return f"fin-{value}"
        if spec.partition_kind == "period_raw":
            return f"finraw-{value}"
        return value

    # ── 读取 ───────────────────────────────────────────────────────────────
    def read_partition(self, partition: str) -> ShardBatch:
        try:
            codec.parse_partition(partition)
        except ValueError as exc:
            raise IntegrityError(str(exc)) from exc
        shards: list[AssetShard] = []
        failed: list[dict] = []
        conn = self._open_read()
        if conn is None:
            return ShardBatch(shards=[], failed=[])
        try:
            for spec in self.specs:
                if not _partition_matches(spec, partition):
                    continue
                try:
                    shard = self._read_unit(conn, spec, partition)
                except _RowFailure as exc:
                    failed.append({
                        "source": self.name,
                        "trade_date": shard_trade_date(partition),
                        "partition": partition,
                        "asset_type": spec.asset_type,
                        "reason": str(exc),
                    })
                    continue
                if shard is not None:
                    shards.append(shard)
        finally:
            conn.close()
        return ShardBatch(shards=shards, failed=failed)

    def _read_unit(self, conn, spec: TableSpec, partition: str):
        info = _table_info(conn, spec.table)
        columns: list[str] = []
        for column in spec.columns:
            if column not in info:
                # 规格与建表同 commit 更新；本地缺列 = 库形状不符（不猜测可空性）。
                raise _RowFailure(f"本地表缺少列: {spec.table}.{column}")
            columns.append(column)
        if spec.identity and not set(spec.identity) <= set(columns):
            raise _RowFailure(f"缺少身份列: {spec.table}")
        where, params = _where_clause(spec, partition)
        sql = (f"SELECT {', '.join(columns)} FROM {spec.table}{where}"
               f"{_order_clause(spec, columns)}")
        results = []
        for raw in conn.execute(sql, params):
            values = {}
            for column, value in zip(columns, raw):
                try:
                    values[column] = _encode_cell(spec, column, value)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise _RowFailure(
                        f"{spec.table}.{column} JSON 损坏: {type(exc).__name__}: {exc}")
            results.append(values)
        if not results:
            return None
        body = {
            "schema": 1,
            "table": spec.table,
            "partition": partition,
            "columns": columns,
            "rows": [[row.get(column) for column in columns] for row in results],
        }
        try:
            return codec.build_shard(partition, spec.asset_type, self.name, body)
        except (TypeError, ValueError) as exc:
            raise _RowFailure(f"{spec.table} 序列化失败: {type(exc).__name__}: {exc}")

    # ── 水位 ───────────────────────────────────────────────────────────────
    def watermarks(self) -> dict[str, str]:
        conn = self._open_read()
        if conn is None:
            return {}
        aggregate: dict[str, dict] = {}
        try:
            for spec in self.specs:
                clock = spec.clock_column or "''"
                size = spec.size_expr or "0"
                clauses = [f"({spec.filter_sql})"] if spec.filter_sql else []
                where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
                if spec.partition_kind == "static":
                    rows = conn.execute(
                        f"SELECT MAX({clock}), COUNT(*), "
                        f"COALESCE(SUM({size}), 0) FROM {spec.table}{where}", ()).fetchall()
                    keyed = [(spec.static_key, row) for row in rows]
                else:
                    sql = (f"SELECT {spec.partition_column}, MAX({clock}), COUNT(*), "
                           f"COALESCE(SUM({size}), 0) FROM {spec.table}{where} "
                           f"GROUP BY {spec.partition_column}")
                    keyed = [(self._partition_key(spec, row[0]), row[1:])
                             for row in conn.execute(sql, ())]
                for partition, (max_clock, count, total) in keyed:
                    if not count:
                        continue
                    item = aggregate.setdefault(
                        partition, {"clock": "", "count": 0, "bytes": 0})
                    if max_clock and str(max_clock) > item["clock"]:
                        item["clock"] = str(max_clock)
                    item["count"] += int(count or 0)
                    item["bytes"] += int(total or 0)
        finally:
            conn.close()
        return {
            partition: f"{item['clock']}|{item['count']}|{item['bytes']}"
            for partition, item in aggregate.items()
        }

    def volatile_partitions(self) -> set[str]:
        conn = self._open_read()
        if conn is None:
            return set()
        found: set[str] = set()
        try:
            for spec in self.specs:
                if not spec.volatile:
                    continue
                if spec.partition_kind == "static":
                    found.add(spec.static_key)
                    continue
                clauses = [f"({spec.filter_sql})"] if spec.filter_sql else []
                sql = f"SELECT DISTINCT {spec.partition_column} FROM {spec.table}"
                if clauses:
                    sql += " WHERE " + " AND ".join(clauses)
                for (value,) in conn.execute(sql, ()):
                    if value:
                        found.add(self._partition_key(spec, value))
        finally:
            conn.close()
        return found

    # ── 本地索引 / 行数（status 用）─────────────────────────────────────────
    def local_index(self, partition: str) -> dict[str, str]:
        batch = self.read_partition(partition)
        if batch.failed:
            raise IntegrityError(
                f"本地 {self.name} 资产损坏: {batch.failed[0].get('reason')}")
        return {f"{shard.source}:{shard.asset_type}": shard.sha256
                for shard in batch.shards}

    def local_snapshot(self, partition: str):
        """返回 ``(unit_key -> sha256, unit_key -> 行数, 分片列表)``（status/verify 用）。"""
        batch = self.read_partition(partition)
        if batch.failed:
            raise IntegrityError(
                f"本地 {self.name} 资产损坏: {batch.failed[0].get('reason')}")
        index, counts = {}, {}
        for shard in batch.shards:
            key = f"{shard.source}:{shard.asset_type}"
            index[key] = shard.sha256
            try:
                counts[key] = len(json.loads(shard.content.decode("utf-8")).get("rows") or [])
            except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
                counts[key] = 0
        return index, counts, list(batch.shards)

    # ── 恢复（行级合并）────────────────────────────────────────────────────
    def restore(self, partition: str, shards: list[AssetShard], *,
                force: bool = False) -> RestoreReport:
        report = RestoreReport()
        with assets_db._lock:
            conn = assets_db._connect(path=self.db_path)
            try:
                assets_db._ensure_schema(conn)
                conn.execute("BEGIN IMMEDIATE")
                for shard in shards:
                    if shard.source != self.name:
                        continue
                    if not has_spec(self.name, shard.asset_type):
                        report.failed.append({
                            "key": f"{shard.source}:{shard.asset_type}",
                            "reason": "未知同步单元"})
                        continue
                    spec = spec_for(self.name, shard.asset_type)
                    outcome = self._restore_shard(conn, spec, partition, shard, force)
                    report.restored += outcome["inserted"]
                    report.skipped += outcome["skipped"]
                    report.rows_inserted += outcome["inserted"]
                    report.rows_skipped += outcome["skipped"]
                    report.rows_filled += outcome["filled"]
                    report.conflicts.extend(outcome["conflicts"])
                    report.deferred.extend(outcome["deferred"])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return report

    def _restore_shard(self, conn, spec: TableSpec, partition: str,
                       shard: AssetShard, force: bool) -> dict:
        outcome = {"inserted": 0, "skipped": 0, "filled": 0,
                   "conflicts": [], "deferred": []}
        if not _partition_matches(spec, partition):
            raise IntegrityError(f"分片与分区不匹配: {spec.unit_key} @ {partition}")
        try:
            payload = json.loads(shard.content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IntegrityError(f"分片解析失败: {spec.unit_key}: {exc}") from exc
        if not isinstance(payload, dict):
            raise IntegrityError(f"分片顶层必须是对象: {spec.unit_key}")
        if payload.get("table") != spec.table:
            raise IntegrityError(
                f"分片表名不一致: {payload.get('table')} != {spec.table}")
        if payload.get("partition") != partition:
            raise IntegrityError(
                f"分片分区不一致: {payload.get('partition')} != {partition}")
        columns = payload.get("columns")
        rows = payload.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise IntegrityError(f"分片缺少 columns/rows: {spec.unit_key}")

        info = _table_info(conn, spec.table)
        # 多余列忽略（方案 §6.4）：远端随版本新增的列不影响本端恢复。
        omitted_defaults: set[str] = set()
        for column in spec.columns:
            if column in columns:
                continue
            notnull, default = info.get(column, (False, None))
            if column in spec.identity:
                raise IntegrityError(f"分片缺少身份列: {spec.unit_key}.{column}")
            if notnull and default is None:
                raise IntegrityError(
                    f"分片缺少 NOT NULL 无默认列: {spec.table}.{column}")
            if notnull:
                omitted_defaults.add(column)

        read_columns = [c for c in spec.columns if c in info]
        # 本地查找必须包含 raw_blob 为 NULL 的元数据行（导出侧的 filter 只是优化）。
        where, params = _where_clause(spec, partition, include_filter=False)
        local_map: dict[tuple, dict] = {}
        for raw in conn.execute(
                f"SELECT {', '.join(read_columns)} FROM {spec.table}{where}", params):
            values = {}
            for column, value in zip(read_columns, raw):
                try:
                    values[column] = _decode_cell(spec, column, value)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise IntegrityError(
                        f"本地 {spec.table}.{column} JSON 损坏: {exc}") from exc
            local_map[_row_identity(spec, values)] = values

        insert_columns = [c for c in spec.columns
                          if c in columns and c in info and c not in omitted_defaults]
        update_columns = [c for c in spec.columns
                          if c in columns and c in info and c not in spec.identity
                          and c != spec.partition_column]
        for raw in rows:
            if not isinstance(raw, list) or len(raw) != len(columns):
                raise IntegrityError(f"分片行形状非法: {spec.unit_key}")
            row = dict(zip(columns, raw))
            for column in spec.null_fill_columns:
                if isinstance(row.get(column), str):
                    row[column] = base64.b64decode(row[column].encode("ascii"))
            identity = _row_identity(spec, row)
            existing = local_map.get(identity)
            if existing is None:
                if spec.null_fill_columns:
                    outcome["deferred"].append({
                        "key": spec.unit_key, "identity": list(identity),
                        "reason": "元数据行缺失，raw 延后补齐"})
                    continue
                values = [self._db_value(spec, column, row.get(column))
                          for column in insert_columns]
                conn.execute(
                    f"INSERT INTO {spec.table}({', '.join(insert_columns)}) "
                    f"VALUES ({', '.join('?' * len(insert_columns))})",
                    tuple(values))
                local_map[identity] = dict(row)
                outcome["inserted"] += 1
                continue

            if spec.identity_hook:
                # 内容身份（run_uid 已剔除 build_ids）：身份相同即语义相同。
                outcome["skipped"] += 1
                continue

            diff = [c for c in update_columns
                    if _norm_value(existing.get(c)) != _norm_value(row.get(c))]
            if not diff:
                outcome["skipped"] += 1
                continue
            fillable = [c for c in diff
                        if c in spec.null_fill_columns and existing.get(c) is None]
            remaining = [c for c in diff if c not in fillable]
            if not remaining:
                self._update_row(conn, spec, identity, existing, row, fillable)
                outcome["inserted"] += 1
                outcome["filled"] += 1
                continue
            if not force:
                outcome["conflicts"].append({
                    "key": spec.unit_key, "identity": list(identity),
                    "columns": remaining, "reason": "content differs"})
                continue
            self._update_row(conn, spec, identity, existing, row,
                             list(dict.fromkeys(fillable + remaining)))
            outcome["inserted"] += 1
        return outcome

    @staticmethod
    def _db_value(spec: TableSpec, column: str, value):
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        if column in spec.json_columns:
            return _serialize_cell(spec, column, value)
        if column in spec.null_fill_columns and isinstance(value, str):
            return base64.b64decode(value.encode("ascii"))
        return value

    def _update_row(self, conn, spec: TableSpec, identity: tuple,
                    existing: dict, row: dict, columns: list[str]) -> None:
        if not columns:
            return
        where_sql, where_params = self._identity_where(spec, identity, existing, row)
        assignments = ", ".join(f"{column} = ?" for column in columns)
        values = [self._db_value(spec, column, row.get(column)) for column in columns]
        conn.execute(
            f"UPDATE {spec.table} SET {assignments}{where_sql}",
            tuple(values) + tuple(where_params))

    @staticmethod
    def _identity_where(spec: TableSpec, identity: tuple, existing: dict,
                        row: dict) -> tuple[str, list]:
        if spec.identity_hook == _BUILD_RUN_UID:
            # 内容身份单元按 report_json 定位（同一 uid 只有一个内容）。
            raise IntegrityError("内容身份单元不支持逐列更新")
        clauses, params = [], []
        for column in spec.identity:
            clauses.append(f"{column} = ?")
            params.append(existing.get(column, row.get(column)))
        return " WHERE " + " AND ".join(clauses), params


class _RowFailure(Exception):
    """单分片行级失败（不阻断同源其它单元）。"""


def shard_trade_date(partition: str) -> str:
    """分片失败诊断的兼容字段：仅 date 分区返回分区键。"""
    return partition if codec.partition_kind(partition) == "date" else ""


def _norm_value(value):
    if isinstance(value, (dict, list)):
        return _dumps(value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return value


class PrepsnapAssetSource(CompositeTableSource):
    name = "prepsnap"


class AuctionsnapAssetSource(CompositeTableSource):
    name = "auctionsnap"


class DragonsAssetSource(CompositeTableSource):
    """``assets.db / dragons``：dragon.db.dragons_v2 的镜像（volatile 单元）。"""

    name = "dragons"


class FactorAssetSource(CompositeTableSource):
    name = "factor"


class FinAssetSource(CompositeTableSource):
    name = "fin"
