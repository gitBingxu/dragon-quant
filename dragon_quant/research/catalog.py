"""研究数据集注册表（唯一字段/过滤器/排序契约源）。

数据集是小型显式注册表，不是插件框架：每个条目声明物理位置、字段语义、
可过滤字段、稳定排序键与 owner 只读 loader。``research datasets`` /
``research schema`` / ``research query`` 都只读这里的事实，CLI 参数被严格校验，
不会把未知 dataset / field / filter 静默忽略。
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

SCHEMA_VERSION = "research_v1"
CATALOG_VERSION = "research_catalog_v1"

FILTER_NAMES = ("date_from", "date_to", "code", "scan_id", "run_id",
                "status", "experiment_id")
CLI_FILTER_ARGS = {
    "date_from": "--from",
    "date_to": "--to",
    "code": "--code",
    "scan_id": "--scan-id",
    "run_id": "--run-id",
    "status": "--status",
    "experiment_id": "--experiment-id",
}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    description: str
    unit: str | None = None
    nullable: bool = True
    large: bool = False
    compatibility: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "unit": self.unit,
            "nullable": self.nullable,
            "large": self.large,
            "compatibility": self.compatibility,
        }


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    title: str
    description: str
    db: str
    table: str
    loader: str
    fields: tuple[FieldSpec, ...]
    filters: tuple[str, ...] = ()
    required_filters: tuple[str, ...] = ()
    sort: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    default_fields: tuple[str, ...] | None = None

    def field_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)

    def field(self, name: str) -> FieldSpec:
        for item in self.fields:
            if item.name == name:
                return item
        raise KeyError(name)

    def default_field_names(self) -> tuple[str, ...]:
        if self.default_fields is not None:
            return self.default_fields
        return tuple(item.name for item in self.fields if not item.large)

    @property
    def date_field(self) -> str | None:
        """首个 date 类型字段；用于 ``datasets`` 报告的已存日期范围。"""
        for item in self.fields:
            if item.type == "date":
                return item.name
        return None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "db": self.db,
            "table": self.table,
            "source": "v2",
            "filters": list(self.filters),
            "required_filters": list(self.required_filters),
            "filter_arguments": {name: CLI_FILTER_ARGS[name] for name in self.filters},
            "status_values": list(self.statuses),
            "sort": list(self.sort),
            "default_fields": list(self.default_field_names()),
            "fields": [item.as_dict() for item in self.fields],
            "caveats": list(self.caveats),
        }


def f(name: str, type_: str, description: str, *, unit: str | None = None,
      nullable: bool = True, large: bool = False, compatibility: str = "") -> FieldSpec:
    return FieldSpec(name, type_, description, unit=unit, nullable=nullable,
                     large=large, compatibility=compatibility)


_PRICE = "人民币元"
_PCT_POINT = "百分比点（1.0 = 1%）"
_PCT = "百分比点"

_SCAN_RUN_FIELDS = (
    f("scan_id", "string", "扫描 run 标识", nullable=False),
    f("scan_date", "date", "扫描日（YYYY-MM-DD）", nullable=False),
    f("elapsed_s", "number", "耗时", unit="秒"),
    f("top_n", "integer", "展示/入选上限"),
    f("candidates_n", "integer", "每板块候选数"),
    f("workers", "integer", "并发线程数"),
    f("created_at", "datetime", "写入时间"),
    f("source", "string", "固定 v2"),
)

_CANDIDATE_FIELDS = (
    f("scan_id", "string", "扫描 run 标识", nullable=False),
    f("scan_date", "date", "扫描日", nullable=False),
    f("scan_top_n", "integer", "该 run 的 top_n"),
    f("scan_created_at", "datetime", "该 run 写入时间"),
    f("code", "string", "股票代码", nullable=False),
    f("name", "string", "股票名称"),
    f("rank", "integer", "当日扫描排名"),
    f("composite_score", "number", "五维综合分", unit="分"),
    f("board_count", "integer", "板数", unit="板"),
    f("concepts", "array", "概念标签"),
    f("dim_drive", "number", "带动性维度分", unit="分"),
    f("dim_anti_drop", "number", "抗跌性维度分", unit="分"),
    f("dim_leadership", "number", "领涨性维度分", unit="分"),
    f("dim_absorption", "number", "资金承接维度分", unit="分"),
    f("dim_liquidity", "number", "流动性维度分", unit="分"),
    f("is_true_dragon", "boolean", "是否入选真龙"),
    f("reject_reason", "string", "门槛/否决原因码"),
    f("open_px", "number", "开盘价", unit=_PRICE),
    f("close_px", "number", "收盘价", unit=_PRICE),
    f("high_px", "number", "最高价", unit=_PRICE),
    f("low_px", "number", "最低价", unit=_PRICE),
    f("pct", "number", "当日涨跌幅", unit=_PCT),
    f("turnover_rate", "number", "换手率", unit=_PCT),
    f("amount", "number", "成交额", unit=_PRICE),
    f("market_cap", "number", "市值", unit=_PRICE),
    f("source", "string", "固定 v2"),
)

_DRAGON_FIELDS = (
    f("trade_date", "date", "入选日", nullable=False),
    f("code", "string", "股票代码", nullable=False),
    f("name", "string", "股票名称"),
    f("scan_id", "string", "来源扫描 run"),
    f("rank", "integer", "扫描排名"),
    f("composite_score", "number", "五维综合分", unit="分"),
    f("board_count", "integer", "板数", unit="板"),
    f("open_px", "number", "开盘价", unit=_PRICE),
    f("close_px", "number", "收盘价", unit=_PRICE),
    f("high_px", "number", "最高价", unit=_PRICE),
    f("low_px", "number", "最低价", unit=_PRICE),
    f("pct", "number", "当日涨跌幅", unit=_PCT),
    f("turnover_rate", "number", "换手率", unit=_PCT),
    f("amount", "number", "成交额", unit=_PRICE),
    f("market_cap", "number", "市值", unit=_PRICE),
    f("concepts", "array", "概念标签"),
    f("is_true_dragon", "boolean", "是否真龙"),
    f("review_status", "string", "回测状态"),
    f("version", "string", "写入版本"),
    f("buy_date", "date", "严格口径买入日"),
    f("buy_price", "number", "严格口径买入价", unit=_PRICE),
    f("max_return_5d", "number", "5 日最大收益", unit=_PCT),
    f("max_drawdown_5d", "number", "5 日最大回撤", unit=_PCT),
    f("source", "string", "固定 v2"),
)

_STRICT_FIELDS = (
    f("trade_date", "date", "入选日", nullable=False),
    f("code", "string", "股票代码", nullable=False),
    f("source", "string", "固定 v2", nullable=False),
    f("buy_date", "date", "可成交买入日"),
    f("buy_price", "number", "可成交买入价", unit=_PRICE),
    f("max_return_5d", "number", "5 日最大收益", unit=_PCT),
    f("max_drawdown_5d", "number", "5 日最大回撤", unit=_PCT),
    f("max_return_hold_days", "integer", "最大收益持有日", unit="交易日"),
    f("review_status", "string", "严格口径结论", nullable=False),
    f("reason", "string", "结论原因"),
    f("review_version", "string", "口径版本"),
    f("computed_at", "datetime", "计算时间"),
)

_RUN_FIELDS = (
    f("run_id", "integer", "账户回测批次 id", nullable=False),
    f("source", "string", "候选来源（固定 v2）"),
    f("display_name", "string", "批次显示名"),
    f("strategy_name", "string", "策略名", nullable=False),
    f("strategy_params", "object", "冻结策略参数"),
    f("date_from", "date", "区间起始", nullable=False),
    f("date_to", "date", "区间结束", nullable=False),
    f("initial_cash", "number", "初始资金", unit=_PRICE),
    f("base_equity", "number", "基准最后收盘权益", unit=_PRICE),
    f("final_equity", "number", "期末权益", unit=_PRICE),
    f("total_return", "number", "总收益率", unit=_PCT_POINT),
    f("max_drawdown", "number", "最大回撤", unit=_PCT_POINT),
    f("trade_count", "integer", "成交笔数"),
    f("win_rate", "number", "盈利率", unit=_PCT_POINT),
    f("created_at", "datetime", "创建时间"),
    f("base_run_id", "integer", "增量基准批次"),
    f("start_equity", "number", "期初权益", unit=_PRICE),
    f("start_positions", "array", "期初持仓"),
    f("start_state_recorded", "boolean", "是否记录可验证期初状态"),
    f("start_realized_pnl", "number", "期初累计已实现盈亏", unit=_PRICE),
    f("start_cash", "number", "期初现金", unit=_PRICE),
    f("start_peak_equity", "number", "期初峰值权益", unit=_PRICE),
    f("start_consecutive_losses", "integer", "期初连续亏损次数"),
    f("start_marks", "object", "期初盯市价"),
    f("run_meta", "object", "运行质量元数据"),
)

_SNAPSHOT_FIELDS = (
    f("run_id", "integer", "账户回测批次 id", nullable=False),
    f("trade_date", "date", "交易日", nullable=False),
    f("cash", "number", "现金", unit=_PRICE),
    f("market_value", "number", "持仓市值", unit=_PRICE),
    f("total_equity", "number", "总权益", unit=_PRICE),
    f("daily_return", "number", "当日收益", unit=_PCT_POINT),
    f("cumulative_return", "number", "累计收益", unit=_PCT_POINT),
    f("drawdown", "number", "回撤", unit=_PCT_POINT),
    f("position_code", "string", "持仓代码"),
    f("position_name", "string", "持仓名称"),
    f("position_qty", "integer", "持仓数量", unit="股"),
    f("position_cost", "number", "持仓成本", unit=_PRICE),
    f("position_market_price", "number", "持仓市价", unit=_PRICE),
    f("position_unrealized_return", "number", "持仓浮动收益", unit=_PCT_POINT),
    f("positions", "array", "当日持仓明细"),
)

_TRADE_FIELDS = (
    f("run_id", "integer", "账户回测批次 id", nullable=False),
    f("trade_date", "date", "成交日", nullable=False),
    f("code", "string", "股票代码", nullable=False),
    f("name", "string", "股票名称"),
    f("side", "string", "买/卖方向", nullable=False),
    f("price", "number", "成交价", unit=_PRICE),
    f("qty", "integer", "成交数量", unit="股"),
    f("amount", "number", "成交金额", unit=_PRICE),
    f("fee", "number", "费用", unit=_PRICE),
    f("realized_pnl", "number", "已实现盈亏", unit=_PRICE),
    f("cash_after", "number", "成交后现金", unit=_PRICE),
    f("position_after", "integer", "成交后持仓", unit="股"),
    f("reason_code", "string", "原因码"),
    f("reason_text", "string", "原因说明"),
    f("signal", "object", "成交时信号快照"),
)

_POSITION_FIELDS = (
    f("run_id", "integer", "账户回测批次 id", nullable=False),
    f("code", "string", "股票代码", nullable=False),
    f("name", "string", "股票名称"),
    f("entry_date", "date", "买入日"),
    f("entry_price", "number", "买入价", unit=_PRICE),
    f("qty", "integer", "数量", unit="股"),
    f("entry_reason_code", "string", "买入原因码"),
    f("entry_signal", "object", "买入信号快照"),
    f("exit_date", "date", "卖出日"),
    f("exit_price", "number", "卖出价", unit=_PRICE),
    f("exit_reason_code", "string", "卖出原因码"),
    f("exit_signal", "object", "卖出信号快照"),
    f("realized_return", "number", "已实现收益", unit=_PCT_POINT),
    f("hold_days", "integer", "持有日", unit="交易日"),
    f("status", "string", "持仓状态"),
    f("post_exit_max_return", "number", "清仓后最大收益", unit=_PCT),
    f("post_exit_max_drawdown", "number", "清仓后最大回撤", unit=_PCT),
    f("post_exit_window_days", "integer", "清仓后观察窗口", unit="交易日"),
    f("initial_cost", "number", "初始成本", unit=_PRICE),
    f("realized_pnl", "number", "已实现盈亏", unit=_PRICE),
    f("period_pnl", "number", "本段盈亏", unit=_PRICE),
    f("period_start_value", "number", "本段起始价值", unit=_PRICE),
    f("inherited", "boolean", "是否继承自基准批次"),
)

_EVENT_FIELDS = (
    f("run_id", "integer", "账户回测批次 id", nullable=False),
    f("event_date", "date", "事件日", nullable=False),
    f("event_type", "string", "事件类型", nullable=False),
    f("code", "string", "股票代码"),
    f("name", "string", "股票名称"),
    f("title", "string", "事件标题"),
    f("detail", "string", "事件详情"),
    f("reason_code", "string", "原因码"),
    f("cash", "number", "事件后现金", unit=_PRICE),
    f("total_equity", "number", "事件后权益", unit=_PRICE),
    f("signal", "object", "事件信号快照"),
)

_SIGNAL_FIELDS = (
    f("signal_id", "integer", "买入信号记录 id", nullable=False),
    f("code", "string", "股票代码", nullable=False),
    f("name", "string", "股票名称"),
    f("source", "string", "候选来源"),
    f("entry_date", "date", "买入信号日", nullable=False),
    f("entry_price", "number", "买入信号价", unit=_PRICE),
    f("entry_reason_code", "string", "买入原因码"),
    f("entry_reason_text", "string", "买入原因说明"),
    f("entry_signal", "object", "买入信号快照"),
    f("highest_return", "number", "持有期最高收益", unit=_PCT_POINT),
    f("highest_price", "number", "持有期最高价", unit=_PRICE),
    f("took_profit_half", "boolean", "是否已半仓止盈"),
    f("status", "string", "open / closed"),
    f("exit_date", "date", "平仓日"),
    f("exit_price", "number", "平仓价", unit=_PRICE),
    f("exit_reason_code", "string", "平仓原因码"),
    f("exit_reason_text", "string", "平仓原因说明"),
    f("exit_signal", "object", "平仓信号快照"),
    f("hold_days", "integer", "持有日", unit="交易日"),
    f("qty", "integer", "数量", unit="股"),
    f("half_exit_date", "date", "半仓腿平仓日"),
    f("half_exit_price", "number", "半仓腿平仓价", unit=_PRICE),
    f("half_exit_qty", "integer", "半仓腿数量", unit="股"),
    f("half_exit_reason_code", "string", "半仓腿原因码"),
    f("half_exit_reason_text", "string", "半仓腿原因说明"),
    f("half_exit_signal", "object", "半仓腿信号快照"),
    f("created_at", "datetime", "创建时间"),
    f("updated_at", "datetime", "更新时间"),
)

_PREP_FIELDS = (
    f("trade_date", "date", "复盘日", nullable=False),
    f("source", "string", "口径来源"),
    f("created_at", "datetime", "写入时间"),
    f("result", "object", "完整复盘结果 payload（大字段，需显式选择）", large=True),
)

_AUCTION_FIELDS = (
    f("trade_date", "date", "竞价日", nullable=False),
    f("code", "string", "股票代码", nullable=False),
    f("source", "string", "口径来源"),
    f("created_at", "datetime", "写入时间"),
    f("analysis", "object", "完整竞价强度分析 payload（大字段，需显式选择）",
      large=True),
)

_FACTOR_VALUE_FIELDS = (
    f("trade_date", "date", "交易日", nullable=False),
    f("code", "string", "股票代码", nullable=False),
    f("factor_name", "string", "因子名", nullable=False),
    f("factor_version", "string", "因子版本", nullable=False),
    f("value", "number", "因子值", nullable=False),
    f("available_at", "datetime", "可用时点（PIT）", nullable=False),
    f("source", "string", "来源", nullable=False),
    f("source_scan_id", "string", "扫描血缘"),
    f("source_meta", "object", "来源诊断血缘"),
    f("computed_at", "datetime", "计算时间"),
)

_FACTOR_REGISTRY_FIELDS = (
    f("factor_name", "string", "因子名", nullable=False),
    f("factor_version", "string", "版本", nullable=False),
    f("label", "string", "展示名"),
    f("frequency", "string", "频率", nullable=False),
    f("source", "string", "来源", nullable=False),
    f("depends_on", "array", "依赖项"),
    f("lookback", "integer", "回看长度"),
    f("direction", "string", "方向", nullable=False),
    f("unit", "string", "单位"),
    f("available_phase", "string", "可用阶段", nullable=False),
    f("pit_rule", "string", "PIT 规则", nullable=False),
    f("scope", "object", "作用域"),
    f("params", "object", "参数"),
    f("default_enabled", "boolean", "默认启用（deprecated，不控制构建/模型）"),
    f("valid_from", "date", "生效日"),
    f("deprecated_at", "date", "废弃日"),
    f("owner", "string", "维护者"),
    f("description", "string", "说明"),
    f("spec_hash", "string", "规格哈希", nullable=False),
    f("created_at", "datetime", "写入时间"),
)

_FACTOR_BUILD_FIELDS = (
    f("build_id", "integer", "构建审计 id", nullable=False),
    f("trade_date", "date", "交易日", nullable=False),
    f("started_at", "datetime", "开始时间"),
    f("finished_at", "datetime", "结束时间"),
    f("created_at", "datetime", "写入时间"),
    f("report", "object", "逐因子构建报告（大字段，需显式选择）", large=True),
)

_FACTOR_MODEL_FIELDS = (
    f("model_name", "string", "模型名", nullable=False),
    f("model_version", "string", "模型版本", nullable=False),
    f("params", "object", "模型参数", nullable=False),
    f("factor_versions", "object", "因子版本快照", nullable=False),
    f("created_at", "datetime", "写入时间"),
)

_EXPERIMENT_FIELDS = (
    f("experiment_id", "integer", "实验 id", nullable=False),
    f("run_id", "integer", "基准批次 id", nullable=False),
    f("created_at", "datetime", "创建时间"),
    f("experiment_version", "string", "实验口径版本", nullable=False),
    f("status", "string", "实验结论状态"),
    f("request", "object", "实验请求"),
    f("report", "object", "实验报告（含冻结输入指纹；大字段，需显式选择）", large=True),
)


DATASETS: dict[str, DatasetSpec] = {
    item.name: item for item in (
        DatasetSpec(
            "scan_runs", "扫描 run", "每次扫描的元信息（固定 v2）",
            "dragon", "scans_v2", "scan_runs", _SCAN_RUN_FIELDS,
            filters=("date_from", "date_to", "scan_id"),
            sort=("scan_date DESC", "scan_id DESC"),
            caveats=("只含已落库扫描；运行中或失败扫描不在此表",)),
        DatasetSpec(
            "scan_candidates", "扫描原始候选", "scan_stocks ⋈ scans 的原始候选与维度分",
            "dragon", "scan_stocks_v2", "scan_candidates", _CANDIDATE_FIELDS,
            filters=("date_from", "date_to", "code", "scan_id"),
            sort=("scan_date DESC", "scan_id DESC", "rank ASC", "code ASC"),
            caveats=("同一 code 同日可出现在多次扫描；去重是研究方的选择，不在本表",
                     "未入库/被拦截的候选不可见")),
        DatasetSpec(
            "dragons", "真龙入选池", "5 日去重后的 dragons_v2 入选记录",
            "dragon", "dragons_v2", "dragons", _DRAGON_FIELDS,
            filters=("date_from", "date_to", "code"),
            sort=("trade_date DESC", "code ASC"),
            caveats=("日级行在区间内可重复（同一 code 多日入选），不是全市场覆盖",)),
        DatasetSpec(
            "review_strict", "严格回测口径", "dragon_review_strict 的严格可成交结论",
            "dragon", "dragon_review_strict", "review_strict", _STRICT_FIELDS,
            filters=("date_from", "date_to", "code"),
            sort=("trade_date DESC", "code ASC"),
            caveats=("仅历史结论，不重算；未回测的入选日没有行",)),
        DatasetSpec(
            "account_runs", "账户回测批次", "review_account_runs 元信息与期初状态",
            "dragon", "review_account_runs", "account_runs", _RUN_FIELDS,
            filters=("date_from", "date_to", "run_id"),
            sort=("run_id DESC",),
            caveats=("total_return / win_rate 是既有 evaluation 口径，不是券商对账单",)),
        DatasetSpec(
            "account_snapshots", "账户权益曲线", "review_account_snapshots 逐日快照",
            "dragon", "review_account_snapshots", "account_snapshots", _SNAPSHOT_FIELDS,
            filters=("date_from", "date_to", "code", "run_id"),
            sort=("trade_date ASC", "run_id ASC"),
            caveats=("跨 run 读取非原子；单 run 读取才构成该批次序列",)),
        DatasetSpec(
            "account_trades", "账户成交明细", "review_account_trades 成交与费用",
            "dragon", "review_account_trades", "account_trades", _TRADE_FIELDS,
            filters=("date_from", "date_to", "code", "run_id"),
            sort=("trade_date ASC", "run_id ASC"),
            caveats=("是回测撮合结果，不是实际券商成交",)),
        DatasetSpec(
            "account_positions", "账户持仓周期", "review_account_positions 持仓与清仓后窗口",
            "dragon", "review_account_positions", "account_positions", _POSITION_FIELDS,
            filters=("date_from", "date_to", "code", "run_id"),
            sort=("entry_date ASC", "run_id ASC"),
            caveats=("半仓腿不单独成行；inherited=1 表示继承自基准批次",)),
        DatasetSpec(
            "account_events", "账户事件", "review_account_events 风控/资金事件",
            "dragon", "review_account_events", "account_events", _EVENT_FIELDS,
            filters=("date_from", "date_to", "code", "run_id"),
            sort=("event_date ASC", "run_id ASC"),
            caveats=("事件只记录已发生的回测内动作",)),
        DatasetSpec(
            "signals", "买卖信号", "buy_sell_signals 历史信号与半仓腿",
            "dragon", "buy_sell_signals", "signals", _SIGNAL_FIELDS,
            filters=("date_from", "date_to", "code", "status"),
            statuses=("open", "closed", "all"),
            sort=("entry_date DESC", "signal_id DESC"),
            caveats=("不是实际券商成交；没有落库的诊断字段不可查询（unavailable）",)),
        DatasetSpec(
            "prep_snapshots", "盘后复盘快照", "已存 prep_snapshots（不采集、不重算）",
            "assets", "prep_snapshots", "prep_snapshots", _PREP_FIELDS,
            filters=("date_from", "date_to", "code"),
            sort=("trade_date DESC", "source ASC"),
            caveats=("result 是大字段，默认不返回；--code 只能做 payload 文本包含判定",)),
        DatasetSpec(
            "auction_snapshots", "集合竞价快照", "已存 auction_snapshots（不采集、不重算）",
            "assets", "auction_snapshots", "auction_snapshots", _AUCTION_FIELDS,
            filters=("date_from", "date_to", "code"),
            sort=("trade_date DESC", "code ASC", "source ASC"),
            caveats=("analysis 是大字段，默认不返回；缺失不补采",)),
        DatasetSpec(
            "factor_values", "因子值长表", "factor_values 逐日逐股因子值",
            "assets", "factor_values", "factor_values", _FACTOR_VALUE_FIELDS,
            filters=("date_from", "date_to", "code"),
            sort=("trade_date DESC", "code ASC", "factor_name ASC", "factor_version ASC"),
            caveats=("缺失 = 无行，绝不写 0；版本不可比时按版本分组",)),
        DatasetSpec(
            "factor_registry", "因子注册表", "factor_registry 元数据快照",
            "assets", "factor_registry", "factor_registry", _FACTOR_REGISTRY_FIELDS,
            sort=("factor_name ASC", "factor_version ASC"),
            caveats=("同一 (name, version) 语义不可变",)),
        DatasetSpec(
            "factor_build_runs", "因子构建审计", "factor_build_runs 逐日构建报告",
            "assets", "factor_build_runs", "factor_build_runs", _FACTOR_BUILD_FIELDS,
            filters=("date_from", "date_to"),
            sort=("trade_date DESC", "build_id DESC"),
            caveats=("report 是大字段，默认不返回",)),
        DatasetSpec(
            "factor_models", "因子模型快照", "factor_model_snapshots 可复现输入",
            "assets", "factor_model_snapshots", "factor_models", _FACTOR_MODEL_FIELDS,
            sort=("model_name ASC", "model_version ASC"),
            caveats=("只读已写快照；不重新训练/校准",)),
        DatasetSpec(
            "experiments", "账户对照实验", "review_account_experiments 已存报告",
            "dragon", "review_account_experiments", "experiments", _EXPERIMENT_FIELDS,
            filters=("run_id", "experiment_id"),
            sort=("experiment_id DESC",),
            caveats=("只读已存报告，不重算；run 必须显式 confirm",)),
    )
}


def get_dataset(name: str) -> DatasetSpec:
    try:
        return DATASETS[name]
    except KeyError as exc:
        raise KeyError(f"未知数据集：{name}") from exc


def dataset_names() -> tuple[str, ...]:
    return tuple(DATASETS)


def load_rows(spec: DatasetSpec, **kwargs):
    module_path, attribute = {
        "scan_runs": ("dragon_quant.scan.storage.research_reads", "scan_runs"),
        "scan_candidates": ("dragon_quant.scan.storage.research_reads", "scan_candidates"),
        "dragons": ("dragon_quant.scan.storage.research_reads", "dragons"),
        "review_strict": ("dragon_quant.scan.storage.research_reads", "review_strict"),
        "account_runs": ("dragon_quant.review_account.research_reads", "account_runs"),
        "account_snapshots": ("dragon_quant.review_account.research_reads", "account_snapshots"),
        "account_trades": ("dragon_quant.review_account.research_reads", "account_trades"),
        "account_positions": ("dragon_quant.review_account.research_reads", "account_positions"),
        "account_events": ("dragon_quant.review_account.research_reads", "account_events"),
        "signals": ("dragon_quant.review_account.research_reads", "signals"),
        "prep_snapshots": ("dragon_quant.infra.assets.research_reads", "prep_snapshots"),
        "auction_snapshots": ("dragon_quant.infra.assets.research_reads", "auction_snapshots"),
        "factor_values": ("dragon_quant.infra.factor.research_reads", "factor_values"),
        "factor_registry": ("dragon_quant.infra.factor.research_reads", "factor_registry"),
        "factor_build_runs": ("dragon_quant.infra.factor.research_reads", "factor_build_runs"),
        "factor_models": ("dragon_quant.infra.factor.research_reads", "factor_models"),
        "experiments": ("dragon_quant.review_account.research_reads", "experiments"),
    }[spec.loader]
    return getattr(import_module(module_path), attribute)(**kwargs)


def table_present(spec: DatasetSpec):
    module_path, attribute = {
        "dragon": ("dragon_quant.scan.storage.research_reads", "table_present"),
        "assets": ("dragon_quant.infra.assets.research_reads", "table_present"),
    }[spec.db]
    return getattr(import_module(module_path), attribute)(spec.table)


def db_path(spec: DatasetSpec):
    """Resolve the physical DB path lazily (import must stay side-effect free)."""
    if spec.db == "dragon":
        from dragon_quant.infra.storage import paths
        return paths.DB_PATH
    from dragon_quant.infra.assets import db as assets_db
    return assets_db.ASSETS_DB_PATH