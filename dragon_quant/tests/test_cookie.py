"""Cookie 库测试（``dragon_quant.infra.provider.cookie``）。

Cookie 的命令行入口已统一为正式 ``data cookie-status|cookie-set|cookie-fetch``；
本模块只覆盖库职责：Cookie 目录常量与读取入口，不再涉及任何独立 ``python -m``
入口或 CLI 编排。
"""

import unittest

from dragon_quant.infra.provider.cookie import COOKIE_DIR, get_xq


class TestCookiePaths(unittest.TestCase):

    def test_cookie_dir_exists(self):
        self.assertIsNotNone(COOKIE_DIR)

    def test_get_xq_returns_string_or_none(self):
        result = get_xq()
        self.assertTrue(result is None or isinstance(result, str))
