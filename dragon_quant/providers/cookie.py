"""``python -m dragon_quant.providers.cookie`` — 保留的独立 Cookie 入口。

本文件只做入口委托，库实现与 CLI 编排分别在
:mod:`dragon_quant.infra.provider.cookie` / ``.cookie_cli``；导入本模块不提供
``get_xq`` / ``set_xq`` 等库函数。
"""

if __name__ == "__main__":
    from dragon_quant.infra.provider.cookie_cli import main

    raise SystemExit(main())
