"""
tests for dragon_quant.providers.ths 纯函数：
  _safe_float / _parse_jsonp / _parse_components_html /
  _parse_ranking_html / _aggregate_5min / THSProvider._get_inner_code
"""
import time
import unittest
from unittest.mock import patch

from dragon_quant.providers.ths import (
    _safe_float, _parse_jsonp, _parse_components_html,
    _aggregate_5min, THSProvider, _INNER_CACHE,
)


class TestSafeFloat(unittest.TestCase):

    def test_normal(self):
        self.assertEqual(_safe_float("3.14"), 3.14)

    def test_none(self):
        self.assertEqual(_safe_float(None), 0.0)

    def test_bad(self):
        self.assertEqual(_safe_float("--"), 0.0)


class TestParseJsonp(unittest.TestCase):

    def test_basic(self):
        raw = 'quotebridge_v6_time_48_885611_last({"48_885611":{"pre":"100"}})'
        d = _parse_jsonp(raw)
        self.assertEqual(d["48_885611"]["pre"], "100")

    def test_invalid(self):
        self.assertIsNone(_parse_jsonp("not jsonp"))


class TestParseComponentsHtml(unittest.TestCase):

    def _row(self, code, name, price, pct):
        return (f"<tr><td>1</td><td><a>{code}</a></td><td><a>{name}</a></td>"
                f"<td>{price}</td><td>{pct}</td><td>x</td></tr>")

    def test_parse(self):
        html = ("<table><tbody>"
                + self._row("301366", "一博科技", "58.20", "20.00")
                + self._row("000001", "平安银行", "12.30", "-1.50")
                + "</tbody></table>")
        comps = _parse_components_html(html, "301558")
        self.assertEqual(len(comps), 2)
        self.assertEqual(comps[0].code, "301366")
        self.assertEqual(comps[0].name, "一博科技")
        self.assertEqual(comps[0].pct, 20.0)
        self.assertEqual(comps[0].price, 58.2)
        self.assertEqual(comps[0].sector_code, "301558")
        self.assertEqual(comps[1].pct, -1.5)

    def test_skip_non_stock_rows(self):
        # 代码非 6 位数字的行应跳过
        html = ("<table><tbody>"
                "<tr><td>1</td><td><a>abc</a></td><td>x</td><td>1</td><td>2</td></tr>"
                + self._row("600519", "贵州茅台", "1700", "3.00")
                + "</tbody></table>")
        comps = _parse_components_html(html, "301558")
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0].code, "600519")

    def test_no_tbody(self):
        self.assertEqual(_parse_components_html("<html></html>", "x"), [])


class TestParseRankingHtml(unittest.TestCase):

    def test_parse(self):
        html = ('<table><tbody>'
                '<tr><td>1</td>'
                '<td><a href="http://q.10jqka.com.cn/gn/detail/code/301558/">阿里巴巴概念</a></td>'
                '<td>1900</td><td>5.20%</td><td>x</td></tr>'
                '<tr><td>2</td>'
                '<td><a href="http://q.10jqka.com.cn/gn/detail/code/300800/">安防</a></td>'
                '<td>800</td><td>-2.10%</td><td>x</td></tr>'
                '</tbody></table>')
        rows = THSProvider._parse_ranking_html(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].code, "301558")
        self.assertEqual(rows[0].name, "阿里巴巴概念")
        self.assertEqual(rows[0].pct, 5.2)
        self.assertEqual(rows[1].pct, -2.1)


class TestAggregate5min(unittest.TestCase):

    def _ts(self, date, hhmm):
        return int(time.mktime(time.strptime(
            f"{date} {hhmm[:2]}:{hhmm[2:]}", "%Y%m%d %H:%M"))) * 1000

    def test_bucket_boundary(self):
        # 6 个 1 分钟点 → 第1桶5个(0930-0934) + 尾桶1个(0935)
        lines = []
        for i, hhmm in enumerate(["0930", "0931", "0932", "0933", "0934", "0935"]):
            price = 100 + i  # 100..105
            lines.append(f"{hhmm},{price},{1000*(i+1)},99,{10*(i+1)}")
        data = ";".join(lines)
        bars = _aggregate_5min(data, "20260617", 100.0)
        self.assertEqual(len(bars), 2)
        # 第1桶
        b0 = bars[0]
        self.assertEqual(b0.open, 100.0)
        self.assertEqual(b0.close, 104.0)
        self.assertEqual(b0.high, 104.0)
        self.assertEqual(b0.low, 100.0)
        self.assertEqual(b0.timestamp, self._ts("20260617", "0934"))
        # amount/volume 桶内求和
        self.assertEqual(b0.amount, 1000 + 2000 + 3000 + 4000 + 5000)
        self.assertEqual(b0.volume, 10 + 20 + 30 + 40 + 50)
        # pct 相对昨收
        self.assertAlmostEqual(b0.pct, 4.0)
        # 尾桶
        self.assertEqual(bars[1].open, 105.0)
        self.assertEqual(bars[1].close, 105.0)

    def test_empty(self):
        self.assertEqual(_aggregate_5min("", "20260617", 100.0), [])

    def test_skip_bad_lines(self):
        data = "0930,100,1000,99,10;badline;0931,101,2000,99,20"
        bars = _aggregate_5min(data, "20260617", 100.0)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].open, 100.0)
        self.assertEqual(bars[0].close, 101.0)

    def test_zero_pre_close(self):
        bars = _aggregate_5min("0930,100,1000,99,10", "20260617", 0.0)
        self.assertEqual(bars[0].pct, 0.0)
        self.assertEqual(bars[0].chg, 0.0)


class TestInnerCode(unittest.TestCase):

    def setUp(self):
        _INNER_CACHE.clear()

    def tearDown(self):
        _INNER_CACHE.clear()

    @patch("dragon_quant.providers.ths._curl")
    def test_industry_code_uses_self_without_request(self, mock_curl):
        p = THSProvider()
        self.assertEqual(p._get_inner_code("881140"), "881140")
        mock_curl.assert_not_called()
        self.assertEqual(_INNER_CACHE["881140"], "881140")

    @patch("dragon_quant.providers.ths._curl")
    def test_extract_and_cache(self, mock_curl):
        mock_curl.return_value = "<input id='clid' value='885611'>"
        p = THSProvider()
        self.assertEqual(p._get_inner_code("301558"), "885611")
        # 第二次走缓存，不再请求
        self.assertEqual(p._get_inner_code("301558"), "885611")
        self.assertEqual(mock_curl.call_count, 1)

    @patch("dragon_quant.providers.ths._curl", return_value="<html>no clid</html>")
    def test_missing(self, _mock):
        self.assertEqual(THSProvider()._get_inner_code("999999"), "")


class TestNotImplemented(unittest.TestCase):

    def test_individual_methods(self):
        p = THSProvider()
        for fn in (lambda: p.get_kline("x"),
                   lambda: p.get_5min_kline("x"),
                   lambda: p.get_quote("x")):
            with self.assertRaises(NotImplementedError):
                fn()


if __name__ == "__main__":
    unittest.main()
