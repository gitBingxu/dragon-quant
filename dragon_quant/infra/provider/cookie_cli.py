"""Cookie 独立入口编排 — 由 ``python -m dragon_quant.providers.cookie`` 薄委托。

库职责（读写/获取）留在 ``cookie.py``；本模块只承载 argparse 与
status/set/fetch 的动作分派，供唯一独立入口薄委托，并允许测试注入 fetch 实现。
"""

import argparse
from typing import Optional, Sequence

from dragon_quant.infra.provider.cookie import fetch_xq, get_xq, set_xq


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["set", "fetch", "status"])
    parser.add_argument("--source", choices=["xq"], default="xq")
    parser.add_argument("--cookie", "-c")
    parser.add_argument("--show", action="store_true")
    return parser


def _print_status(show: bool) -> None:
    items = [("雪球", get_xq())]
    for k, v in items:
        print(f"{k}: {'✅' if v else '❌'} ({len(v)}字符)")
    if show:
        for k, v in items:
            if v:
                print(f"\n{k}: {v[:200]}...")


def main(argv: Optional[Sequence[str]] = None, *, fetch=fetch_xq) -> Optional[int]:
    """独立入口：参数解析 + 动作分派；返回 None（退出码 0）。

    ``fetch`` 是唯一的获取实现注入点（默认绑定库函数 ``cookie.fetch_xq``），
    模块级 monkeypatch 不会改变该默认值；测试请显式传入。
    """
    args = build_parser().parse_args(argv)
    if args.action == "status":
        _print_status(args.show)
    elif args.action == "set" and args.cookie:
        set_xq(args.cookie)
    elif args.action == "fetch":
        fetch()
    return None
