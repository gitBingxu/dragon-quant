"""
CLI 入口 — dragon-quant 命令行工具

命令:
  dragon-quant -v | --version
  dragon-quant scan [--top 5] [--candidates 5] [--workers 2]
  dragon-quant logs {tail,query,clear,list} [options]
  dragon-quant data {sector,components,kline,minute,quote,batch-quote} [options]
  dragon-quant review [--date DATE] [--top N] [--force]
  dragon-quant dragons [--date DATE] [--true-only]
  dragon-quant storage {status,size,clear} [options]
"""

import argparse
import sys

from dragon_quant import commands
# 既有调用方与测试仍从 cli 取这些 helper：单一实现在 _shared，此处仅重导出。
from dragon_quant.commands._shared import (  # noqa: F401
    kbar_to_dict as _kbar_to_dict,
    normalize_cli_date as _normalize_cli_date,
    resolve_trade_date as _resolve_trade_date,
    to_dict as _to_dict,
)
from dragon_quant.providers.tdx import warn_if_unavailable



class DragonHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Keep curated help text layout while using argparse parsing behavior."""

    def _format_action_invocation(self, action):
        if not action.option_strings:
            return super()._format_action_invocation(action)

        parts = []
        if action.nargs == 0:
            parts.extend(action.option_strings)
        else:
            default = action.dest.upper()
            args_string = self._format_args(action, default)
            parts.extend(action.option_strings[:-1])
            parts.append(f"{action.option_strings[-1]} {args_string}")
        return ", ".join(parts)


class DragonArgumentParser(argparse.ArgumentParser):
    """Project parser with Linux-style help section names."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", DragonHelpFormatter)
        super().__init__(*args, **kwargs)
        self._optionals.title = "Options"
        self._positionals.title = "Arguments"

    def format_help(self):
        text = super().format_help()
        return text.replace("usage:", "Usage:", 1)


def _parser(*args, **kwargs):
    return DragonArgumentParser(*args, **kwargs)


def main():
    from dragon_quant._version import __version__

    shared = _parser(add_help=False)
    shared._optionals.title = "Scan Options"
    shared.add_argument("--top", type=int, default=25, help="最多展示和入选的真龙数量 (默认25)")
    shared.add_argument("--candidates", type=int, default=5, help="每板块取前N只 (默认5)")
    shared.add_argument("--workers", type=int, default=2, help="并发线程数 (默认2)")
    shared.add_argument("--force", action="store_true",
                        help="强制执行 (跳过交易时段拦截 + DB 结果缓存；仍复用 provider 磁盘缓存)")
    shared.add_argument("--no-cache", action="store_true",
                        help="跳过 provider 按交易日磁盘缓存，强制各数据源重新拉取并刷新缓存")

    parser = _parser(
        prog="dragon-quant",
        description="龙头战法量化筛选系统",
        epilog="""Examples:
  dragon-quant -h
  dragon-quant scan --top 25 --candidates 5 --workers 2
  dragon-quant data kline --code 600172 --days 20
  dragon-quant review --ui-only --source v2
  dragon-quant review-account --from 20260501 --to 20260601 --ui

Use \"dragon-quant <command> -h\" for command-specific help.
""",
    )
    parser.add_argument("-v", "--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.set_defaults(command="scan")
    sub = parser.add_subparsers(dest="command", title="Commands", metavar="<command>")

    # scan 子命令（五维「识别真龙」评分器）
    scan_p = sub.add_parser(
        "scan",
        help="批量扫描龙头股（五维识别真龙）",
        parents=[shared],
        usage="dragon-quant scan [options]",
        description="批量扫描龙头股（五维「识别真龙」评分器）。",
        epilog="""Examples:
  dragon-quant scan
  dragon-quant scan --top 25 --candidates 5 --workers 2
  dragon-quant scan --date 20260519 --top 5
""",
    )
    scan_p.add_argument("--date", default=None,
                        help="查询历史扫描记录 (YYYYMMDD)，指定后不执行实时扫描")

    # logs 子命令
    logs_p = sub.add_parser(
        "logs",
        help="日志查询与管理",
        usage="dragon-quant logs [options] <action> [action-options]",
        description="查询、汇总或清理扫描日志。",
        epilog="""Examples:
  dragon-quant logs tail -n 20
  dragon-quant logs --source v2 query --date 20260519 --level error
  dragon-quant logs summary
""",
    )
    logs_p.add_argument("--source", default="v2", choices=["v1", "v2"],
                        help="日志来源体系 (默认 v2；v1 仅用于历史记录查询)")
    logs_subs = logs_p.add_subparsers(dest="logs_action")
    logs_subs.title = "Actions"  # type: ignore[assignment]
    logs_subs.metavar = "<action>"

    tail_p = logs_subs.add_parser("tail", help="查看最新日志", usage="dragon-quant logs tail [options]")
    tail_p.add_argument("-n", "--lines", type=int, default=20, help="返回行数 (默认20)")

    query_p = logs_subs.add_parser("query", help="按条件查询日志", usage="dragon-quant logs query [options]")
    query_p.add_argument("--date", help="日期 (YYYYMMDD)")
    query_p.add_argument("--category", help="类别过滤，如 phase、api、scorer:drive")
    query_p.add_argument("--level", help="级别过滤，如 info、warn、error")
    query_p.add_argument("--code", help="股票代码过滤")
    query_p.add_argument("--tail", type=int, default=200, help="最多返回条数 (默认200)")

    clear_logs_p = logs_subs.add_parser("clear", help="清除旧日志", usage="dragon-quant logs clear [options]")
    clear_logs_p.add_argument("--days", type=int, default=7, help="保留最近N天 (默认7)")

    logs_subs.add_parser("list", help="列出所有日志文件", usage="dragon-quant logs list")
    sum_p = logs_subs.add_parser("summary", help="最新扫描摘要", usage="dragon-quant logs summary [options]")
    sum_p.add_argument("--date", help="日期过滤")

    # data 子命令
    data_p = sub.add_parser(
        "data",
        help="原子数据查询",
        usage="dragon-quant data <action> [action-options]",
        description="查询板块、成分股、K线、实时行情和 Cookie 状态。",
        epilog="""Examples:
  dragon-quant data sector
  dragon-quant data components --sector 881167
  dragon-quant data kline --code 600172 --days 20
  dragon-quant data cookie-status
""",
    )
    data_subs = data_p.add_subparsers(dest="data_action")
    data_subs.title = "Actions"  # type: ignore[assignment]
    data_subs.metavar = "<action>"

    sector_p = data_subs.add_parser("sector", help="板块排行榜", usage="dragon-quant data sector [options]")
    sector_p.add_argument("--asc", action="store_true", help="跌幅榜（默认涨幅榜）")

    comp_p = data_subs.add_parser("components", help="板块成分股", usage="dragon-quant data components --sector SECTOR")
    comp_p.add_argument("--sector", required=True, help="同花顺行业板块 6 位代码，如 881167")

    kline_p = data_subs.add_parser("kline", help="个股日K线", usage="dragon-quant data kline --code CODE [options]")
    kline_p.add_argument("--code", required=True, help="股票代码")
    kline_p.add_argument("--source", default="xueqiu", choices=["xueqiu", "tencent"],
                         help="数据源 (默认 xueqiu)")
    kline_p.add_argument("--days", type=int, default=20, help="拉取日K线根数 (默认20)")

    min_p = data_subs.add_parser("minute", help="个股1分钟K线（分时）",
                                 usage="dragon-quant data minute --code CODE [options]")
    min_p.add_argument("--code", required=True, help="股票代码")
    min_p.add_argument("--source", default="xueqiu", choices=["xueqiu", "tencent"],
                       help="数据源 (默认 xueqiu)")

    quote_p = data_subs.add_parser("quote", help="个股实时行情",
                                   usage="dragon-quant data quote --code CODE [options]")
    quote_p.add_argument("--code", required=True, help="股票代码")
    quote_p.add_argument("--source", default="tencent", choices=["tencent", "xueqiu"],
                         help="数据源 (默认 tencent)")

    bq_p = data_subs.add_parser("batch-quote", help="批量实时行情",
                                usage="dragon-quant data batch-quote --codes CODES [options]")
    bq_p.add_argument("--codes", required=True, help="股票代码，逗号分隔")
    bq_p.add_argument("--source", default="tencent", choices=["tencent", "xueqiu"],
                      help="数据源 (默认 tencent)")

    data_subs.add_parser("cookie-status", help="查看 Cookie 状态",
                         usage="dragon-quant data cookie-status")
    data_subs.add_parser("cookie-fetch", help="刷新 Cookie（雪球）",
                         usage="dragon-quant data cookie-fetch")

    cs_p = data_subs.add_parser("cookie-set", help="手动设置 Cookie",
                                usage="dragon-quant data cookie-set --cookie COOKIE")
    cs_p.add_argument("--cookie", "-c", required=True, help="完整 Cookie 字符串")

    # blacklist 子命令（板块黑名单，拉取领涨/领跌板块时过滤）
    bl_p = sub.add_parser(
        "blacklist",
        help="板块黑名单管理",
        usage="dragon-quant blacklist <action> [action-options]",
        description="管理拉取领涨/领跌板块时使用的板块黑名单。",
        epilog="""Examples:
  dragon-quant blacklist list
  dragon-quant blacklist add "次新股"
  dragon-quant blacklist remove "次新股"
""",
    )
    bl_subs = bl_p.add_subparsers(dest="blacklist_action")
    bl_subs.title = "Actions"  # type: ignore[assignment]
    bl_subs.metavar = "<action>"
    bl_subs.add_parser("list", help="列出黑名单", usage="dragon-quant blacklist list")
    bl_add_p = bl_subs.add_parser("add", help="新增黑名单板块",
                                  usage="dragon-quant blacklist add NAME")
    bl_add_p.add_argument("name", help="概念名称（子串匹配，如 次新股）")
    bl_rm_p = bl_subs.add_parser("remove", help="移除黑名单板块",
                                 usage="dragon-quant blacklist remove NAME")
    bl_rm_p.add_argument("name", help="概念名称")

    # review 子命令
    rev_p = sub.add_parser(
        "review",
        help="龙头回测验证",
        usage="dragon-quant review [options]",
        description="回测已入选龙头，或启动回测结果 Web UI。",
        epilog="""Examples:
  dragon-quant review
  dragon-quant review --source v2 --date 20260519
  dragon-quant review --ui-only --source v2
""",
    )
    rev_p.add_argument("--date", default=None, help="只回测指定日期 (YYYYMMDD)")
    rev_p.add_argument("--top", type=int, default=None, help="只回测 top N")
    rev_p.add_argument("--force", action="store_true", help="无视 review_status 全部重算")
    rev_p.add_argument("--source", default="v2", choices=["v1", "v2"],
                       help="回测数据来源体系：v2=dragons_v2 (默认 v2；v1 仅用于历史记录查询)")
    rev_p.add_argument("--ui", action="store_true", help="回测后启动 Web UI")
    rev_p.add_argument("--ui-only", action="store_true", help="仅启动 Web UI（不执行回测）")
    rev_p.add_argument("--port", type=int, default=8765, help="Web UI 端口 (默认 8765)")
    rev_p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    # review-account 子命令
    acct_p = sub.add_parser(
        "review-account",
        help="账户级模拟交易回测",
        usage="dragon-quant review-account [options]",
        description="按真实账户、现金、持仓和买卖策略模拟交易员操作。",
        epilog="""Examples:
  dragon-quant review-account --from 20260501 --to 20260601
  dragon-quant review-account --from 20260501 --to 20260601 --capital 200000 --ui
  dragon-quant review-account --ui-only --source v2
""",
    )
    acct_p.add_argument("--from", dest="date_from", default=None,
                        help="回测开始日期 (YYYYMMDD 或 YYYY-MM-DD)")
    acct_p.add_argument("--to", dest="date_to", default=None,
                        help="回测结束日期 (YYYYMMDD 或 YYYY-MM-DD)")
    acct_p.add_argument("--capital", type=float, default=100000.0,
                        help="初始资金 (默认 100000)")
    acct_p.add_argument("--strategy", default="dragon_pullback_daily",
                        help="账户策略名 (默认 dragon_pullback_daily)")
    acct_p.add_argument("--source", default="v2", choices=["v1", "v2"],
                        help="候选数据来源体系 (默认 v2)")
    acct_p.add_argument("--ui", action="store_true", help="回测后启动账户 Web UI")
    acct_p.add_argument("--ui-only", action="store_true", help="仅启动账户 Web UI（不执行回测）")
    acct_p.add_argument("--port", type=int, default=8765, help="Web UI 端口 (默认 8765)")
    acct_p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    acct_p.add_argument("--config", help="共享策略参数 JSON 文件")

    # dragons 子命令（导出扫描物化的龙头，JSON）
    dragons_p = sub.add_parser(
        "dragons",
        help="导出扫描出的龙头（JSON）",
        usage="dragon-quant dragons [--date YYYYMMDD] [--true-only]",
        description="导出某交易日扫描物化的龙头股列表（JSON）。默认取最新有数据的交易日。",
        epilog="""Examples:
  dragon-quant dragons
  dragon-quant dragons --date 20260519
  dragon-quant dragons --date 20260519 --true-only
""",
    )
    dragons_p.add_argument("--date", default=None,
                           help="交易日，支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD（默认最新有龙头数据的交易日）")
    dragons_p.add_argument("--true-only", action="store_true",
                           help="只输出真龙 (is_true_dragon=1)")

    # prep 子命令（盘后复盘：主线 + 情绪温度 + 候选池快照；固定 v2）
    prep_p = sub.add_parser(
        "prep",
        help="盘后复盘，生成候选池+情绪温度快照（默认增量采集）",
        usage="dragon-quant prep [--date YYYYMMDD] [--recompute | --force] [--json]",
        description="盘后执行复盘，采集原始数据资产（assets.db prep_assets）并计算候选池快照"
                    "（prep_snapshots），review-account 消费。默认增量：资产完好则跳过采集，"
                    "仅补采缺失/不健康项；--json 输出完整复盘 JSON。",
        epilog="""Examples:
  dragon-quant prep
  dragon-quant prep --date 20260519
  dragon-quant prep --date 20260519 --recompute   # 用已存资产重算快照（算法升级重放）
  dragon-quant prep --date 20260519 --force       # 强制重采全部可采资产（不绕过 16:40 门禁）
  dragon-quant prep --json                        # 输出完整复盘 JSON
""",
    )
    prep_p.add_argument("--date", default=None,
                        help="复盘日 (YYYYMMDD / YYYY-MM-DD，默认最新有龙头数据的交易日)")
    prep_mode = prep_p.add_mutually_exclusive_group()
    prep_mode.add_argument("--recompute", action="store_true",
                           help="跳过检测与采集，用已存数据资产重算快照（无资产时报错）")
    prep_mode.add_argument("--force", action="store_true",
                           help="强制重采全部可采资产（历史日 same_day_only 仍不采）；"
                                "与 scan --force 不同：不绕过 16:40 与交易日校验")
    prep_p.add_argument("--json", dest="json", action="store_true",
                        help="输出完整复盘 JSON（默认只输出买入建议摘要）")

    # auction 子命令（采集最近 N 日龙头的当日集合竞价盘口并落库；或重算已存快照）
    auction_p = sub.add_parser(
        "auction",
        help="采集最近 N 日龙头的当日集合竞价盘口（TDX），或重算已存快照",
        usage="dragon-quant auction [--pool prep|legacy] [--days N] [--json] | "
              "dragon-quant auction --recompute DATE [--json]",
        description="默认读取严格 T-1 的 prep 快照，采集 verdict∈{strong,light} 的推荐标的；"
                    "推荐集为空/快照缺失时回退最近 N 个交易日的龙头并集。落库原始资产"
                    "（assets.db/auction_assets）并计算强度快照（auction_snapshots）。"
                    "默认输出人读摘要（参与标的强弱 + 汇总）；--json 输出完整 JSON。"
                    "使用 --recompute 时不触网，用已存原始资产重算指定交易日快照。",
        epilog="""Examples:
  dragon-quant auction                        # 优先 prep 推荐，空则回退 legacy
  dragon-quant auction --pool legacy          # 强制旧口径
  dragon-quant auction --days 5               # 仅 legacy/回退路径生效
  dragon-quant auction --recompute 20261020   # 用已存原始资产重算（不触网）
  dragon-quant auction --json                 # 输出完整 JSON
""",
    )
    auction_p.add_argument("--pool", choices=["prep", "legacy"], default="prep",
                           help="候选池来源：prep=盘后复盘推荐（默认），legacy=最近 N 日龙头并集")
    auction_p.add_argument("--days", type=int, default=None,
                           help="legacy/回退路径取最近 N 个有龙头数据的交易日（默认 5）")
    auction_p.add_argument("--recompute", metavar="DATE", default=None,
                           help="不触网，用已存原始资产重算该日竞价快照 (YYYYMMDD / YYYY-MM-DD)")
    auction_p.add_argument("--json", dest="json", action="store_true",
                           help="输出完整 JSON（默认只展示参与竞价判断的标的强弱与汇总）")

    # assets 子命令（数据资产云端同步）
    assets_p = sub.add_parser(
        "assets",
        help="数据资产云端同步（备份/恢复）",
        usage="dragon-quant assets <action> [action-options]",
        description="把 assets.db 全部 15 张表的行级数据资产备份到 S3 兼容对象存储，"
                    "或从云端恢复（prep/auction 原始资产 + 快照/因子/财务表 + dragons 镜像）。",
        epilog="""Examples:
  dragon-quant assets push --profile r2
  dragon-quant assets pull --from 20261020 --to 20261022 --apply
  dragon-quant assets status --orphans
  dragon-quant assets verify --from 20261020 --to 20261022 --deep
  dragon-quant assets doctor --profile r2 --samples 50
  dragon-quant assets backfill-dragons --dry-run
""",
    )
    assets_subs = assets_p.add_subparsers(dest="assets_action")
    assets_subs.title = "Actions"  # type: ignore[assignment]
    assets_subs.metavar = "<action>"

    def _assets_common(parser, *, timeout=True):
        parser.add_argument("--profile", default=None, help="配置 profile（如 r2 / b2）")
        if timeout:
            parser.add_argument("--timeout", type=float, default=60.0,
                                help="单请求超时秒数（默认 60）")

    assets_doctor = assets_subs.add_parser(
        "doctor",
        help="连通性与元数据回环检查（门槛 A）",
        usage="dragon-quant assets doctor [--profile NAME] [--samples N]",
        description="对选定 S3 兼容后端执行 PUT/HEAD/GET/LIST/DELETE，验证元数据回环与成功率。",
    )
    _assets_common(assets_doctor)
    assets_doctor.add_argument("--samples", type=int, default=50,
                               help="PUT/HEAD/GET 样本数（默认 50）")

    assets_push = assets_subs.add_parser(
        "push",
        help="上传本地原始数据资产",
        usage="dragon-quant assets push [--from D] [--to D] [--all] [--dry-run]",
        description="增量上传 assets.db 全部表；按分区 + 来源判水位，volatile 单元强制重读。\n"
                    "--from/--to 对 fin 表按报告期末过滤，static 分区始终参与。",
    )
    _assets_common(assets_push)
    assets_push.add_argument("--from", dest="from_date", default=None,
                             help="起始日期 (YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD)；fin 表按报告期末比较")
    assets_push.add_argument("--to", dest="to_date", default=None,
                             help="结束日期 (同 --from 格式)；static 分区不受区间影响")
    assets_push.add_argument("--all", dest="all_dates", action="store_true",
                             help="忽略 state 水位，重算全部本地日期")
    assets_push.add_argument("--dry-run", dest="dry_run", action="store_true",
                             help="只输出计划，不写任何远端对象/state")
    assets_push.add_argument("--allow-shrink", dest="allow_shrink", action="store_true",
                             help="允许新 manifest 成为远端严格子集（默认拒绝）")

    assets_pull = assets_subs.add_parser(
        "pull",
        help="从云端恢复原始数据资产",
        usage="dragon-quant assets pull --from D --to D [--apply] [--force]",
        description="默认 dry-run；--apply 下载、逐片校验后写入 assets.db。"
                    "新表按行级合并（缺则补、冲突需 --force），prep/auction 保持分片级门禁。",
    )
    _assets_common(assets_pull)
    assets_pull.add_argument("--from", dest="from_date", required=False, default=None,
                             help="起始日期（必填）；fin 表按报告期末比较")
    assets_pull.add_argument("--to", dest="to_date", required=False, default=None,
                             help="结束日期（必填）；static 分区始终参与")
    assets_pull.add_argument("--apply", action="store_true",
                             help="执行下载与写库（默认只输出计划）")
    assets_pull.add_argument("--force", action="store_true",
                             help="覆盖本地内容不一致的资产（默认只报告冲突）")
    assets_pull.add_argument("--pick", choices=["latest"], default=None,
                             help="manifest 重建遇同日多候选时按 LastModified 取最新")
    assets_pull.add_argument("--target-dir", dest="target_dir", default=None,
                             help="恢复到隔离目录（<DIR>/assets.db）")

    assets_status = assets_subs.add_parser(
        "status",
        help="展示本地/云端差异",
        usage="dragon-quant assets status [--all] [--orphans]",
        description="逐分区比较本地与云端 manifest，输出缺失/不一致/drift/体积与同步覆盖图。"
                    "not_synced 已恒为空对象（factor_*/fin_* 已纳入同步）。",
    )
    _assets_common(assets_status)
    assets_status.add_argument("--all", dest="all_dates", action="store_true",
                               help="检查全部分区（默认最近 30 个 date + 全部 period/static）")
    assets_status.add_argument("--orphans", action="store_true",
                               help="额外统计未被 manifest 引用的孤儿 blob")

    assets_verify = assets_subs.add_parser(
        "verify",
        help="校验 manifest / blob 完整性",
        usage="dragon-quant assets verify [--from D --to D | --samples N] [--deep]",
        description="默认校验 manifest 哈希；--deep 下载并解压校验分片内容。"
                    "不做 drift 豁免：字节级差异一律报告。",
    )
    _assets_common(assets_verify)
    assets_verify.add_argument("--from", dest="from_date", default=None,
                               help="起始交易日")
    assets_verify.add_argument("--to", dest="to_date", default=None,
                               help="结束交易日")
    assets_verify.add_argument("--samples", type=int, default=None,
                               help="深度校验抽样条数（未给日期范围时必填）")
    assets_verify.add_argument("--deep", action="store_true",
                               help="下载并解压校验 blob 内容")
    assets_verify.add_argument("--md5", dest="show_md5", action="store_true",
                               help="展示校验分片的 md5")

    assets_backfill = assets_subs.add_parser(
        "backfill-dragons",
        help="把 dragon.db.dragons_v2 幂等回填到 assets.db.dragons（本地，不触网）",
        usage="dragon-quant assets backfill-dragons [--from D] [--to D] "
              "[--dry-run] [--prune] [--json]",
        description="按交易日区间把源库 dragons_v2 逐行 UPSERT 到镜像表。"
                    "--dry-run 只体检并区分 missing_rows（源有镜像无）/ "
                    "orphan_rows（源无镜像有）；--prune 删除源库已不存在的镜像行"
                    "（就地 pull 后的收敛命令）。源库是权威，镜像行可由源库重建。",
        epilog="""Examples:
  dragon-quant assets backfill-dragons --dry-run
  dragon-quant assets backfill-dragons
  dragon-quant assets backfill-dragons --from 20261001 --to 20261031
  dragon-quant assets backfill-dragons --prune
""",
    )
    assets_backfill.add_argument("--from", dest="from_date", default=None,
                                 help="起始交易日（默认全部）")
    assets_backfill.add_argument("--to", dest="to_date", default=None,
                                 help="结束交易日（默认全部）")
    assets_backfill.add_argument("--dry-run", dest="dry_run", action="store_true",
                                 help="只体检不写库（区分 missing_rows / orphan_rows）")
    assets_backfill.add_argument("--prune", action="store_true",
                                 help="删除源库不存在的镜像行（源库为权威）")
    assets_backfill.add_argument("--json", dest="json", action="store_true",
                                 help="输出完整 JSON（默认人读摘要）")

    # factor 子命令（因子层：只读迁移 scan/vpa 产物，落 assets.db）
    factor_p = sub.add_parser(
        "factor",
        help="因子层：构建 / 查询 / 状态",
        usage="dragon-quant factor <action> [action-options]",
        description="因子层（长表 + 注册表 + PIT 时点）。P0 只读迁移既有扫描/量价结果，"
                    "不重算、不接入决策（默认零影响）。",
        epilog="""Examples:
  dragon-quant factor list
  dragon-quant factor build --date 20260519
  dragon-quant factor build --from 20260501 --to 20260531
  dragon-quant factor query --date 20260519 --name five_dim_composite
  dragon-quant factor status
""",
    )
    factor_subs = factor_p.add_subparsers(dest="factor_action")
    factor_subs.title = "Actions"  # type: ignore[assignment]
    factor_subs.metavar = "<action>"

    factor_build = factor_subs.add_parser(
        "build",
        help="构建因子值（只读迁移现有 scan / vpa 产物）",
        usage="dragon-quant factor build --date D | --from D --to D [options]",
        description="单日或区间构建。区间按「区间内有扫描记录的交易日」逐日展开，"
                    "每日独立事务与审计报告；缺失源不删除既有因子行。",
    )
    factor_build.add_argument("--date", default=None,
                              help="交易日 (YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD)")
    factor_build.add_argument("--from", dest="from_date", default=None,
                              help="区间起始日（与 --to 成对）")
    factor_build.add_argument("--to", dest="to_date", default=None,
                              help="区间结束日（与 --from 成对）")
    factor_build.add_argument("--only", default=None,
                              help="只构建指定因子（逗号分隔，如 five_dim_drive,vpa_breakout）")
    factor_build.add_argument("--all", dest="all_factors", action="store_true",
                              help="构建全部注册因子（含默认关；与 --only 互斥）")
    factor_build.add_argument("--json", dest="json", action="store_true",
                              help="输出完整 JSON（默认人读摘要）")

    factor_list = factor_subs.add_parser(
        "list", help="列出注册表中的因子", usage="dragon-quant factor list [--json]")
    factor_list.add_argument("--json", dest="json", action="store_true",
                             help="输出完整 JSON")

    factor_query = factor_subs.add_parser(
        "query", help="查询因子值（点查或单因子时序）",
        usage="dragon-quant factor query --date D [--name N] [--code C] | "
              "dragon-quant factor query --from D --to D --name N [options]",
        description="按日点查（可按因子/代码过滤）或查询单因子区间时序；"
                    "输出始终带 resolved_versions（版本=None 绝不静默）。",
    )
    factor_query.add_argument("--date", default=None, help="交易日（点查）")
    factor_query.add_argument("--from", dest="from_date", default=None,
                              help="区间起始日（与 --to + --name 配合）")
    factor_query.add_argument("--to", dest="to_date", default=None,
                              help="区间结束日（与 --from + --name 配合）")
    factor_query.add_argument("--name", default=None, help="因子名（区间查询必填）")
    factor_query.add_argument("--code", default=None, help="股票代码过滤")
    factor_query.add_argument("--version", default=None,
                              help="因子版本（默认按注册表最新版本）")
    factor_query.add_argument("--json", dest="json", action="store_true",
                              help="输出完整 JSON（默认人读表格）")

    factor_status = factor_subs.add_parser(
        "status", help="查看因子存储/注册表/build 状态",
        usage="dragon-quant factor status [--json]")
    factor_status.add_argument("--json", dest="json", action="store_true",
                               help="输出完整 JSON")

    # 矩阵导出（P1）
    factor_export = factor_subs.add_parser(
        "export", help="导出 date × code 因子矩阵（CSV/JSON + sidecar）",
        usage="dragon-quant factor export --from D --to D --name N [--name N2] "
              "[--format csv|json] --out FILE [options]",
        description="长表 → 矩阵派生视图（不落主存储）。默认导出原始值；--clean 才按 "
                    "CleanConfig 在当日截面内缩尾/归一（MAD=0 跳过并记 mad_zero）。",
        epilog="""Examples:
  dragon-quant factor export --from 20260501 --to 20260531 \\
      --name five_dim_composite --out /tmp/matrix.csv
  dragon-quant factor export --from 20260501 --to 20260531 \\
      --name five_dim_composite --name vpa_breakout --clean --normalize rank \\
      --format json --out /tmp/matrix.json
""",
    )
    factor_export.add_argument("--from", dest="from_date", required=True,
                               help="区间起始日")
    factor_export.add_argument("--to", dest="to_date", required=True,
                               help="区间结束日")
    factor_export.add_argument("--name", dest="names", action="append", default=None,
                               help="因子名（可重复；逗号分隔也支持）")
    factor_export.add_argument("--code", dest="codes", action="append", default=None,
                               help="股票代码过滤（可重复）")
    factor_export.add_argument("--version", default=None,
                               help="统一版本（默认按注册表逐因子解析）")
    factor_export.add_argument("--format", dest="fmt", choices=("csv", "json"),
                               default="csv", help="输出格式（默认 csv）")
    factor_export.add_argument("--out", dest="out_path", required=True,
                               help="输出文件路径（sidecar 为 <out>.meta.json）")
    factor_export.add_argument("--clean", action="store_true",
                               help="启用清洗（默认 --winsorize mad + --normalize rank）")
    factor_export.add_argument("--missing", choices=("nan", "drop"), default=None,
                               help="缺失处理（drop = complete-case 丢行）")
    factor_export.add_argument("--winsorize", choices=("none", "mad", "quantile"),
                               default=None, help="缩尾方式")
    factor_export.add_argument("--normalize", choices=("none", "rank", "zscore"),
                               default=None, help="归一方式")
    factor_export.add_argument("--limits", default="0.05,0.95",
                               help="quantile 缩尾分位（默认 0.05,0.95）")
    factor_export.add_argument("--json", dest="json", action="store_true",
                               help="输出完整 JSON（默认人读摘要）")

    # 因子评估（P1）
    factor_eval = factor_subs.add_parser(
        "eval", help="单因子评估（RankIC / ICIR / NW-t / 分组 / 覆盖率）",
        usage="dragon-quant factor eval --name N --from D --to D [options]",
        description="按 available_at 之后第一个可成交日开盘起算前向收益；样本不足返回 "
                    "insufficient_data。财务因子额外输出 PIT 口径与 1q 修正。",
        epilog="""Examples:
  dragon-quant factor eval --name five_dim_composite --from 20260501 --to 20260831
  dragon-quant factor eval --name fin_roe --from 20260101 --to 20260930 \\
      --pit-policy research --groups 3 --json
""",
    )
    factor_eval.add_argument("--name", required=True, help="因子名")
    factor_eval.add_argument("--from", dest="from_date", required=True,
                             help="区间起始日")
    factor_eval.add_argument("--to", dest="to_date", required=True,
                             help="区间结束日")
    factor_eval.add_argument("--horizon", type=int, default=1,
                             help="前向收益交易日数（默认 1；nw_lag=horizon-1）")
    factor_eval.add_argument("--groups", type=int, default=2,
                             help="分组数（只允许 2 / 3）")
    factor_eval.add_argument("--version", default=None,
                             help="因子版本（默认按注册表最新版本）")
    factor_eval.add_argument("--min-days", dest="min_days", type=int, default=20,
                             help="最少有效交易日（默认 20）")
    factor_eval.add_argument("--min-cross-section", dest="min_cross_section",
                             type=int, default=10, help="单日最少有效截面（默认 10）")
    factor_eval.add_argument("--pit-policy", dest="pit_policy",
                             choices=("strict", "research"), default="strict",
                             help="PIT 口径（strict 默认剔除 statutory_backfilled）")
    factor_eval.add_argument("--json", dest="json", action="store_true",
                             help="输出完整 JSON（默认人读摘要）")

    # 财务链接（P0.5）：观测 / 回填 / 校准 / 状态
    factor_fin = factor_subs.add_parser(
        "fin", help="财务原始链路：观测/回填/校准/状态",
        usage="dragon-quant factor fin <sync|calibrate|status> [options]",
        description="财务原始数据（fin_* 五表，落 assets.db）。观测仅当日可采；"
                    "回填按报告期追加式 vintage；字段映射未校准即财务因子 fail-closed。",
        epilog="""Examples:
  dragon-quant factor fin sync --snapshot
  dragon-quant factor fin sync --backfill --from-period 20250331
  dragon-quant factor fin calibrate --sample 20
  dragon-quant factor fin status
""",
    )
    fin_subs = factor_fin.add_subparsers(dest="fin_action")
    fin_subs.title = "Actions"  # type: ignore[assignment]
    fin_subs.metavar = "<action>"

    fin_sync = fin_subs.add_parser(
        "sync", help="同步财务数据（当日观测或按报告期回填）",
        usage="dragon-quant factor fin sync --snapshot [--date D] | "
              "dragon-quant factor fin sync --backfill [options]")
    fin_sync.add_argument("--snapshot", action="store_true",
                          help="当日观测（same_day_only，历史日直接拒绝）")
    fin_sync.add_argument("--backfill", action="store_true",
                          help="按 gpcw 报告期回填（可历史、可重复）")
    fin_sync.add_argument("--date", default=None,
                          help="观测日（默认今天；仅当日可采）")
    fin_sync.add_argument("--from-period", dest="from_period", default=None,
                          help="回填起始报告期（YYYYMMDD / YYYY-MM-DD）")
    fin_sync.add_argument("--to-period", dest="to_period", default=None,
                          help="回填结束报告期")
    fin_sync.add_argument("--force", action="store_true",
                          help="同 md5 也重新下载解析")
    fin_sync.add_argument("--host", default=None,
                          help="覆盖 gpcw 计算服务器地址")
    fin_sync.add_argument("--json", dest="json", action="store_true",
                          help="输出完整 JSON")

    fin_calibrate = fin_subs.add_parser(
        "calibrate", help="字段映射校准（恒等式 + 层级不等式锚定）",
        usage="dragon-quant factor fin calibrate [--sample N] [--period D] [--json]")
    fin_calibrate.add_argument("--sample", type=int, default=20,
                               help="校准样本数（默认 20）")
    fin_calibrate.add_argument("--period", default=None,
                               help="指定报告期对应的 gpcw 文件（默认最新）")
    fin_calibrate.add_argument("--host", default=None, help="覆盖计算服务器地址")
    fin_calibrate.add_argument("--json", dest="json", action="store_true",
                               help="输出完整 JSON")

    fin_status = fin_subs.add_parser(
        "status", help="财务账本/校准/观测状态",
        usage="dragon-quant factor fin status [--json]")
    fin_status.add_argument("--json", dest="json", action="store_true",
                            help="输出完整 JSON")

    # buy 子命令
    buy_p = sub.add_parser(
        "buy",
        help="实盘辅助：给出今日买入建议并记账（纯信号）",
        usage="dragon-quant buy [--config FILE] [--date YYYYMMDD --at HH:MM] [--force] [--json]",
        description="回放当日分时，复用 review-account 共享买点，候选池内所有触发买点的标的均记入买入信号。"
                    "默认输出四类买点逐条诊断；--json 输出完整结果（details[].checks）。",
        epilog="""Examples:
  dragon-quant buy
  dragon-quant buy --date 20260907 --at 10:00
  dragon-quant buy --force
  dragon-quant buy --json
""",
    )
    buy_p.add_argument("--date", default=None, help="交易日 (YYYYMMDD/YYYY-MM-DD，默认今日)")
    buy_p.add_argument("--source", default="v2", choices=["v1", "v2"],
                       help="候选数据来源体系 (默认 v2)")
    buy_p.add_argument("--json", dest="json", action="store_true",
                       help="输出完整 JSON（details[].checks 含四类买点诊断）")

    # sell 子命令
    sell_p = sub.add_parser(
        "sell",
        help="实盘辅助：给出今日卖出建议并记账（纯信号）",
        usage="dragon-quant sell [--config FILE] [--date YYYYMMDD --at HH:MM] [--force]",
        description="对已买入未卖出的信号回放当日分时，复用 review-account 共享卖出策略，触发即平。",
        epilog="""Examples:
  dragon-quant sell
  dragon-quant sell --date 20260908 --at 14:55
  dragon-quant sell --force
""",
    )
    sell_p.add_argument("--date", default=None, help="交易日 (YYYYMMDD/YYYY-MM-DD，默认今日)")
    sell_p.add_argument("--source", default="v2", choices=["v1", "v2"],
                        help="候选数据来源体系 (默认 v2)")

    for trade_parser in (buy_p, sell_p):
        trade_parser.add_argument("--at", help="历史回放截止时刻 HH:MM，仅与 --date 配合")
        trade_parser.add_argument("--config", help="共享策略参数 JSON 文件")
        trade_parser.add_argument(
            "--force", action="store_true",
            help="非交易时段仍强制跑当日回放（09:30 前仍拒绝；15:00–15:06 宽限窗口无需 --force）")

    # vpa 子命令
    vpa_p = sub.add_parser(
        "vpa",
        help="个股量价分析",
        usage="dragon-quant vpa --code CODE [options]",
        description="执行独立的个股量价健康度分析。",
        epilog="""Examples:
  dragon-quant vpa --code 600519
  dragon-quant vpa --code 600519 --source xueqiu --days 60
  dragon-quant vpa --code 600519 --no-save
""",
    )
    vpa_p.add_argument("--code", required=True, help="股票代码，如 600519")
    vpa_p.add_argument("--source", default="xueqiu", choices=["xueqiu", "tencent"])
    vpa_p.add_argument("--days", type=int, default=60, help="拉取日K线根数 (默认60)")
    vpa_p.add_argument("--no-save", action="store_true", help="不写入数据库")

    # storage 子命令
    st_p = sub.add_parser(
        "storage",
        help="持久化数据管理",
        usage="dragon-quant storage <action> [action-options]",
        description="查看磁盘占用或清理本地缓存、结果和日志。",
        epilog="""Examples:
  dragon-quant storage status
  dragon-quant storage size
  dragon-quant storage clear --cache --days 7
  dragon-quant storage clear --all
""",
    )
    st_subs = st_p.add_subparsers(dest="storage_action")
    st_subs.title = "Actions"  # type: ignore[assignment]
    st_subs.metavar = "<action>"

    st_subs.add_parser("status", help="查看存储状态", usage="dragon-quant storage status")
    st_subs.add_parser("size", help="查看磁盘占用", usage="dragon-quant storage size")

    clear_p = st_subs.add_parser("clear", help="清理数据",
                                 usage="dragon-quant storage clear [options]")
    clear_p.add_argument("--all", action="store_true",
                         help="清理全部(cache+results+logs)；数据库文件不清理")
    clear_p.add_argument("--cache", action="store_true", help="清理缓存")
    clear_p.add_argument("--results", action="store_true", help="清理结果")
    clear_p.add_argument("--logs", action="store_true", help="清理日志")
    clear_p.add_argument("--days", type=int, default=None, help="保留最近N天")

    argv = sys.argv[1:]
    if argv and argv[0] == "scan_v2":
        argv[0] = "scan"
    args = parser.parse_args(argv)

    # tmdx 缺失时提示补装（-h/--help/-v/--version 在 parse_args 内已 exit，不会到达这里）
    warn_if_unavailable()

    # 惰性命令注册表分发：所有命令按需加载 `handle(args, services)`；
    # 无法识别的命令（含无参数）打印帮助。
    if not commands.dispatch(args.command, args):
        parser.print_help()


if __name__ == "__main__":
    main()

