"""
tests for provider pure functions:
  xueqiu:     _symbol, _parse_kline
  tencent:    _gtimg_codes, _parse_gtimg_quote
"""
import unittest
from dragon_quant.providers.xueqiu import _symbol, _parse_kline
from dragon_quant.providers.tencent import _gtimg_codes, _parse_gtimg_quote


class TestXueqiuSymbol(unittest.TestCase):

    def test_shanghai(self):
        self.assertEqual(_symbol("600519"), "SH600519")

    def test_shenzhen(self):
        self.assertEqual(_symbol("000001"), "SZ000001")

    def test_small_board(self):
        self.assertEqual(_symbol("002031"), "SZ002031")

    def test_star_board(self):
        self.assertEqual(_symbol("688981"), "SH688981")


class TestXueqiuParseKline(unittest.TestCase):

    def test_valid(self):
        items = [[1700000000000, 1000000, 10.0, 11.0, 9.5, 10.5, 0.5, 5.0, 3.2, 10000000]]
        result = _parse_kline(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].timestamp, 1700000000000)
        self.assertEqual(result[0].pct, 5.0)
        self.assertEqual(result[0].turnover, 3.2)

    def test_invalid_skipped(self):
        items = [[1700000000000, 1000000, 10.0, "bad", 9.5, 10.5, 0.5, 5.0, 3.2, 10000000]]
        result = _parse_kline(items)
        self.assertEqual(len(result), 0)

    def test_short_item_skipped(self):
        items = [[1700000000000, 1000000, 10.0]]  # 只有3个元素
        result = _parse_kline(items)
        self.assertEqual(len(result), 0)

    def test_multiple_items(self):
        items = [
            [1700000000000, 1e6, 10, 11, 9, 10.5, 0.5, 5.0, 3, 1e7],
            [1700100000000, 2e6, 11, 12, 10, 11.5, 0.5, 4.5, 4, 2e7],
        ]
        result = _parse_kline(items)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].timestamp, 1700000000000)
        self.assertEqual(result[1].timestamp, 1700100000000)


class TestGtimgCodes(unittest.TestCase):

    def test_shanghai(self):
        self.assertEqual(_gtimg_codes(["600519"]), "sh600519")

    def test_shenzhen(self):
        self.assertEqual(_gtimg_codes(["000001"]), "sz000001")

    def test_mixed(self):
        result = _gtimg_codes(["600519", "000001", "002031"])
        self.assertEqual(result, "sh600519,sz000001,sz002031")

    def test_star_board(self):
        result = _gtimg_codes(["688981"])
        self.assertEqual(result, "sh688981")


class TestParseGtimgQuote(unittest.TestCase):

    def test_valid_line(self):
        fields = ["0"] * 52
        fields[1] = "茅台"
        fields[2] = "600519"
        fields[3] = "1800.0"
        fields[4] = "1790.0"
        fields[5] = "1795.0"
        fields[31] = "10.5"
        fields[32] = "0.56"
        fields[33] = "1810.0"
        fields[34] = "1785.0"
        fields[36] = "1000000"
        fields[37] = "1800000000"
        fields[38] = "0.5"
        fields[39] = "35.5"
        fields[43] = "1.4"
        fields[44] = "20.0"
        fields[45] = "22.0"
        fields[47] = "1970.0"
        fields[48] = "1610.0"
        fields[49] = "0.8"
        fields[51] = "1798.0"
        line = "~".join(fields)
        q = _parse_gtimg_quote(line)
        self.assertIsNotNone(q)
        self.assertEqual(q.code, "600519")
        self.assertEqual(q.price, 1800.0)

    def test_short_line(self):
        q = _parse_gtimg_quote("~0~")
        self.assertIsNone(q)

    def test_bad_float(self):
        fields = ["0"] * 52
        fields[3] = "bad"
        line = "~".join(fields)
        q = _parse_gtimg_quote(line)
        self.assertIsNone(q)


if __name__ == "__main__":
    unittest.main()
