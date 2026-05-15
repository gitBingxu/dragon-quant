"""
tests for dragon_quant.providers.cookie — Cookie 管理
"""
import unittest
from dragon_quant.providers.cookie import get_em, get_xq, COOKIE_DIR


class TestCookiePaths(unittest.TestCase):

    def test_cookie_dir_exists(self):
        self.assertIsNotNone(COOKIE_DIR)

    def test_get_em_returns_string_or_none(self):
        result = get_em()
        self.assertTrue(result is None or isinstance(result, str))

    def test_get_xq_returns_string_or_none(self):
        result = get_xq()
        self.assertTrue(result is None or isinstance(result, str))


if __name__ == "__main__":
    unittest.main()
