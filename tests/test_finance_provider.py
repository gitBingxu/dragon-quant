"""TDX 财务方法适配测试：DataFrame → dict、字段改名、异常契约（方案 §4.9.1、§8.3）。

零网络：fake client + fake easy_tdx 模块（只提供 Market 枚举）。
"""

import enum
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.infra.provider.tdx import TdxProvider, _finance_record


class FakeMarket(enum.IntEnum):
    SZ = 0
    SH = 1
    BJ = 2


FAKE_ET = SimpleNamespace(Market=FakeMarket)


class FakeDF:
    def __init__(self, records):
        self._records = records
        self.empty = not records

    def to_dict(self, orient):
        assert orient == "records"
        return list(self._records)


class FakeClient:
    def __init__(self, *, fail=None):
        self.fail = fail
        self.calls = []

    def _maybe_fail(self, name):
        if self.fail == name:
            raise ConnectionError(f"{name} transport down")

    def get_finance_info(self, market, code):
        self.calls.append(("finance_info", code))
        self._maybe_fail("finance_info")
        return FakeDF([{
            "market": FakeMarket.SH, "code": code,
            "zong_guben": 10000.0, "meigujing_zichan": 5.5,
            "jing_zichan": 55000.0, "zhuying_shouru": 30000.0,
            "jing_lirun": 3000.0, "updated_date": 20260815,
            "_raw": b"ignored",
        }])

    def get_financial_file_list(self, host=None):
        self.calls.append(("file_list", host))
        self._maybe_fail("file_list")
        return FakeDF([{
            "filename": "tdxfin/gpcw20260630.zip",
            "hash": "ABCDEF0123", "filesize": 12345,
        }, {"filename": None, "hash": "x", "filesize": 1}])

    def get_financial_records(self, filename, host=None):
        self.calls.append(("records", filename))
        self._maybe_fail("records")
        return FakeDF([{
            "code": "600001", "market": FakeMarket.SH,
            "report_date": 20260630, "fields": [1.0, 2.5],
        }])

    def get_financial_file(self, filename, host=None):
        self.calls.append(("file", filename))
        self._maybe_fail("file")
        return b"zip-bytes"

    def get_xdxr_info(self, market, code):
        self.calls.append(("xdxr", code))
        self._maybe_fail("xdxr")
        return FakeDF([{
            "market": FakeMarket.SH, "code": code, "year": 2026, "month": 6,
            "day": 30, "category": 1, "name": "除权除息", "fenhong": 0.1,
            "_raw": b"ignored",
        }])


def _provider(client):
    provider = TdxProvider()
    provider._fin_client = client
    return provider


class TestProviderAdapter(unittest.TestCase):
    def setUp(self):
        self._patch = patch("dragon_quant.infra.provider.tdx._import_easytdx",
                            return_value=FAKE_ET)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_finance_info_normalizes_scalars_and_drops_raw(self):
        provider = _provider(FakeClient())
        info = provider.get_finance_info("600001")
        self.assertEqual(info["code"], "600001")
        self.assertEqual(info["market"], 1)
        self.assertEqual(info["meigujing_zichan"], 5.5)
        self.assertEqual(info["updated_date"], 20260815)
        self.assertNotIn("_raw", info)

    def test_financial_files_rename_hash_and_size(self):
        provider = _provider(FakeClient())
        files = provider.get_financial_files()
        self.assertEqual(len(files), 1, "缺 filename/md5 的行被丢弃")
        self.assertEqual(files[0]["md5"], "abcdef0123")
        self.assertEqual(files[0]["size"], 12345)

    def test_financial_records_preserve_fields(self):
        provider = _provider(FakeClient())
        records = provider.get_financial_records("tdxfin/gpcw20260630.zip")
        self.assertEqual(records[0]["fields"], [1.0, 2.5])
        self.assertEqual(records[0]["market"], 1)
        self.assertEqual(records[0]["report_date"], 20260630)

    def test_xdxr_records(self):
        provider = _provider(FakeClient())
        rows = provider.get_xdxr_info("600001")
        self.assertEqual(rows[0]["category"], 1)
        self.assertNotIn("_raw", rows[0])

    def test_transport_failures_raise(self):
        for method, args in (
                ("finance_info", ("600001",)),
                ("file_list", ()),
                ("records", ("tdxfin/gpcw20260630.zip",)),
                ("xdxr", ("600001",))):
            client = FakeClient(fail=method)
            provider = _provider(client)
            with self.subTest(method=method):
                with self.assertRaises(ConnectionError):
                    if method == "finance_info":
                        provider.get_finance_info(*args)
                    elif method == "file_list":
                        provider.get_financial_files()
                    elif method == "records":
                        provider.get_financial_records(*args)
                    else:
                        provider.get_xdxr_info(*args)

    def test_missing_client_raises(self):
        provider = TdxProvider()
        provider._fin_client = False
        with patch("dragon_quant.infra.provider.tdx._import_easytdx",
                   return_value=FAKE_ET):
            with self.assertRaises(RuntimeError):
                provider.get_financial_files()


class TestFinanceRecordHelper(unittest.TestCase):
    def test_nan_and_numpy_like_values(self):
        class NumpyLike:
            def item(self):
                return 42

        row = _finance_record({
            "a": 1.5, "b": float("nan"), "c": NumpyLike(),
            "market": FakeMarket.SZ, "_raw": b"x",
        })
        self.assertEqual(row, {"a": 1.5, "b": None, "c": 42, "market": 0})


if __name__ == "__main__":
    unittest.main()
