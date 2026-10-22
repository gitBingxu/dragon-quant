"""对象存储后端、凭据与 doctor 的单元/集成测试（全部本地，不访问外网）。"""

import base64
import hashlib
import hmac
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from dragon_quant.assets_sync.backends.local import LocalDirBackend
from dragon_quant.assets_sync.backends.s3 import S3Backend
from dragon_quant.assets_sync.backends.signer import sign_request
from dragon_quant.assets_sync.credentials import AssetsConfig, load_config
from dragon_quant.assets_sync.doctor import run_doctor
from dragon_quant.assets_sync.errors import ConfigError, NotFoundError, StorageError


class TestLocalDirBackend(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.backend = LocalDirBackend(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_and_metadata(self):
        self.backend.put("blobs/2026-10-19/a.json.gz", b"data", {"sha256": "abc"})
        self.assertEqual(self.backend.get("blobs/2026-10-19/a.json.gz"), b"data")
        meta = self.backend.head("blobs/2026-10-19/a.json.gz")
        self.assertEqual(meta.size, 4)
        self.assertEqual(meta.metadata["sha256"], "abc")

    def test_missing(self):
        self.assertIsNone(self.backend.head("nope"))
        with self.assertRaises(NotFoundError):
            self.backend.get("nope")

    def test_list_prefix_excludes_meta(self):
        for key in ("a/1", "a/2", "b/3"):
            self.backend.put(key, key.encode(), {"k": "v"})
        keys = [item.key for item in self.backend.list("a/")]
        self.assertEqual(keys, ["a/1", "a/2"])

    def test_delete(self):
        self.backend.put("x", b"1")
        self.backend.delete("x")
        self.assertIsNone(self.backend.head("x"))

    def test_meta_key_rejected(self):
        with self.assertRaises(ValueError):
            self.backend.put(".meta/x", b"data")

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.backend.put("../escape", b"data")


class FakeS3State:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.put_calls = 0
        self.request_calls = 0
        self.fail_put_once = False
        self.bad_digest_once = False
        self.rate_limit_once = False
        self.page_size = 1000


class FakeS3Handler(BaseHTTPRequestHandler):
    state: FakeS3State
    bucket = "bucket"
    access_key = "ak"
    secret_key = "sk"
    region = "auto"

    def log_message(self, *args):
        return

    def _key(self) -> str:
        path = unquote(urlsplit(self.path).path)
        prefix = f"/{self.bucket}/"
        return path[len(prefix):] if path.startswith(prefix) else ""

    def _send(self, code, body=b"", headers=None, content_length=None):
        self.send_response(code)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body) if content_length is None else content_length))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _verify_auth(self, body: bytes) -> bool:
        authorization = self.headers.get("Authorization")
        if not authorization or "SignedHeaders=" not in authorization:
            return False
        signed = authorization.split("SignedHeaders=", 1)[1].split(",", 1)[0].split(";")
        # urllib 会把请求头名首字母大写（X-Amz-Date），这里统一小写，
        # 否则 amz_date 查不到、验签会退化成「用当前时间重算」而偶发失败。
        incoming = {
            name.lower(): value
            for name, value in self.headers.items()
            if name.lower() in signed
        }
        if "x-amz-date" not in incoming:
            return False
        url = f"http://{self.headers.get('Host', '127.0.0.1')}{self.path}"
        expected = sign_request(
            self.command, url, incoming, body,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region=self.region,
            amz_date=incoming.get("x-amz-date"),
        )
        return hmac.compare_digest(authorization, expected["authorization"])

    def _reject_auth(self):
        self._send(403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>")

    def do_PUT(self):
        self.state.request_calls += 1
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if not self._verify_auth(body):
            self._reject_auth()
            return
        if "Content-MD5" in self.headers:
            expected_md5 = base64.b64encode(hashlib.md5(body).digest()).decode()
            if self.headers.get("Content-MD5") != expected_md5:
                self._send(400, b"<Error><Code>BadDigest</Code></Error>")
                return
        self.state.put_calls += 1
        if self.state.rate_limit_once:
            self.state.rate_limit_once = False
            self._send(429, b"slow down", headers={"Retry-After": "0"})
            return
        if self.state.bad_digest_once:
            self.state.bad_digest_once = False
            self._send(400, b"<Error><Code>BadDigest</Code></Error>")
            return
        if self.state.fail_put_once and self.state.put_calls == 1:
            self._send(500, b"retry")
            return
        metadata = {
            name.lower(): value
            for name, value in self.headers.items()
            if name.lower().startswith("x-amz-meta-")
        }
        self.state.objects[self._key()] = (body, metadata)
        self._send(200)

    def do_HEAD(self):
        self.state.request_calls += 1
        if not self._verify_auth(b""):
            self._reject_auth()
            return
        key = self._key()
        if key not in self.state.objects:
            self._send(404)
            return
        body, metadata = self.state.objects[key]
        self._send(200, headers=metadata, content_length=len(body))

    def do_GET(self):
        self.state.request_calls += 1
        parsed = urlsplit(self.path)
        if parsed.path.rstrip("/") == f"/{self.bucket}":
            if not self._verify_auth(b""):
                self._reject_auth()
                return
            self._list(parsed)
            return
        if not self._verify_auth(b""):
            self._reject_auth()
            return
        key = self._key()
        if key not in self.state.objects:
            self._send(404)
            return
        body, metadata = self.state.objects[key]
        self._send(200, body=body, headers=metadata)

    def do_DELETE(self):
        self.state.request_calls += 1
        if not self._verify_auth(b""):
            self._reject_auth()
            return
        self.state.objects.pop(self._key(), None)
        self._send(204)

    def _list(self, parsed):
        query = parse_qs(parsed.query)
        prefix = query.get("prefix", [""])[0]
        token = query.get("continuation-token", [""])[0]
        keys = sorted(key for key in self.state.objects if key.startswith(prefix))
        start = int(token) if token else 0
        page = keys[start:start + self.state.page_size]
        next_start = start + self.state.page_size
        truncated = next_start < len(keys)
        contents = "".join(
            f"<Contents><Key>{key}</Key><Size>{len(self.state.objects[key][0])}</Size>"
            f"<ETag>&quot;x&quot;</ETag><LastModified>2026-10-19T00:00:00Z</LastModified></Contents>"
            for key in page
        )
        token_xml = f"<NextContinuationToken>{next_start}</NextContinuationToken>" if truncated else ""
        xml = (
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f'<IsTruncated>{"true" if truncated else "false"}</IsTruncated>'
            f"{token_xml}{contents}</ListBucketResult>"
        ).encode("utf-8")
        self._send(200, body=xml)


class TestS3Backend(unittest.TestCase):
    def setUp(self):
        self.state = FakeS3State()
        handler = type("Handler", (FakeS3Handler,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        self.config = AssetsConfig(
            endpoint=f"http://127.0.0.1:{port}",
            region="auto",
            bucket="bucket",
            access_key_id="ak",
            secret_access_key="sk",
        )
        self.backend = S3Backend(self.config, timeout=5, sleep=lambda _: None)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_put_head_get_delete(self):
        self.backend.put("blobs/2026-10-19/a.json.gz", b"data", {"sha256": "abc"})
        meta = self.backend.head("blobs/2026-10-19/a.json.gz")
        self.assertEqual(meta.size, 4)
        self.assertEqual(meta.metadata["sha256"], "abc")
        self.assertEqual(self.backend.get("blobs/2026-10-19/a.json.gz"), b"data")
        self.backend.delete("blobs/2026-10-19/a.json.gz")
        self.assertIsNone(self.backend.head("blobs/2026-10-19/a.json.gz"))

    def test_get_missing(self):
        with self.assertRaises(NotFoundError):
            self.backend.get("missing")
        self.assertIsNone(self.backend.head("missing"))

    def test_list_paginates(self):
        self.state.page_size = 2
        for index in range(5):
            self.backend.put(f"blobs/2026-10-19/{index}", str(index).encode())
        keys = [item.key for item in self.backend.list("blobs/2026-10-19/")]
        self.assertEqual(len(keys), 5)

    def test_retry_on_500(self):
        self.state.fail_put_once = True
        self.backend.put("blobs/2026-10-19/retry", b"data")
        self.assertEqual(self.state.put_calls, 2)
        self.assertEqual(self.backend.get("blobs/2026-10-19/retry"), b"data")

    def test_doctor(self):
        summary = run_doctor(self.backend, samples=3)
        self.assertTrue(summary["ok"], summary)
        self.assertTrue(summary["metadata_roundtrip"])
        self.assertEqual(summary["success_rate"], 1.0)
        self.assertEqual(summary["listed"], 3)

    def test_prefix_isolation(self):
        backend = S3Backend(
            AssetsConfig(
                endpoint=self.config.endpoint, region="auto", bucket="bucket",
                access_key_id="ak", secret_access_key="sk", prefix="prod",
            ),
            timeout=5, sleep=lambda _: None,
        )
        backend.put("blobs/2026-10-19/a.json.gz", b"data", {"sha256": "abc"})
        self.assertIn("prod/blobs/2026-10-19/a.json.gz", self.state.objects)
        self.assertEqual(backend.get("blobs/2026-10-19/a.json.gz"), b"data")
        self.assertEqual([item.key for item in backend.list("blobs/2026-10-19/")],
                         ["blobs/2026-10-19/a.json.gz"])
        self.assertEqual(backend.head("blobs/2026-10-19/a.json.gz").metadata["sha256"], "abc")
        backend.delete("blobs/2026-10-19/a.json.gz")
        self.assertNotIn("prod/blobs/2026-10-19/a.json.gz", self.state.objects)

    def test_prefix_root_list_ignores_sibling_namespace(self):
        backend = S3Backend(
            AssetsConfig(
                endpoint=self.config.endpoint, region="auto", bucket="bucket",
                access_key_id="ak", secret_access_key="sk", prefix="prod",
            ),
            timeout=5, sleep=lambda _: None,
        )
        backend.put("a.json", b"a", {"sha256": "a"})
        self.state.objects["prod2/b.json"] = (b"b", {})
        self.assertEqual([item.key for item in backend.list("")], ["a.json"])

    def test_wrong_secret_rejected(self):
        backend = S3Backend(
            AssetsConfig(
                endpoint=self.config.endpoint, region="auto", bucket="bucket",
                access_key_id="ak", secret_access_key="wrong",
            ),
            timeout=5, sleep=lambda _: None,
        )
        with self.assertRaises(StorageError):
            backend.put("blobs/x", b"data")

    def test_bad_digest_retried_once(self):
        self.state.bad_digest_once = True
        self.backend.put("blobs/x", b"data")
        self.assertEqual(self.state.put_calls, 2)
        self.assertEqual(self.backend.get("blobs/x"), b"data")

    def test_rate_limit_retried(self):
        self.state.rate_limit_once = True
        self.backend.put("blobs/x", b"data")
        self.assertEqual(self.state.put_calls, 2)

    def test_virtual_host_url(self):
        backend = S3Backend(
            AssetsConfig(
                endpoint="https://s3.example.com", region="auto", bucket="b",
                access_key_id="ak", secret_access_key="sk", addressing="virtual",
            )
        )
        self.assertEqual(backend._url("blobs/x"), "https://b.s3.example.com/blobs/x")
        self.assertEqual(backend._bucket_path(), "/")


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "assets_sync.json"
        self.path.write_text(json.dumps({
            "default_profile": "r2",
            "profiles": {
                "r2": {
                    "endpoint": "https://example.r2.cloudflarestorage.com",
                    "region": "auto",
                    "bucket": "bucket",
                    "access_key_id": "ak",
                    "secret_access_key": "sk",
                },
                "b2": {
                    "endpoint": "https://s3.us-west-004.backblazeb2.com",
                    "region": "us-west-004",
                    "bucket": "bucket2",
                    "access_key_id": "ak2",
                    "secret_access_key": "sk2",
                },
            },
        }), encoding="utf-8")
        os.chmod(self.path, 0o600)

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_and_profile(self):
        config = load_config(env={}, config_path=self.path)
        self.assertEqual(config.profile, "r2")
        self.assertEqual(config.region, "auto")
        other = load_config(profile="b2", env={}, config_path=self.path)
        self.assertEqual(other.bucket, "bucket2")

    def test_env_overrides_selected_profile(self):
        config = load_config(
            env={"DQ_ASSETS_BUCKET": "override", "DQ_ASSETS_SECRET_ACCESS_KEY": "env-secret"},
            config_path=self.path,
        )
        self.assertEqual(config.bucket, "override")
        self.assertEqual(config.secret_access_key, "env-secret")

    def test_env_var_expansion(self):
        self.path.write_text(json.dumps({
            "profiles": {"r2": {
                "endpoint": "https://example.r2.cloudflarestorage.com",
                "region": "auto", "bucket": "bucket",
                "access_key_id": "ak", "secret_access_key": "${MY_SECRET}",
            }}
        }), encoding="utf-8")
        os.chmod(self.path, 0o600)
        config = load_config(profile="r2", env={"MY_SECRET": "resolved"}, config_path=self.path)
        self.assertEqual(config.secret_access_key, "resolved")

    def test_permissions_too_open(self):
        os.chmod(self.path, 0o644)
        with self.assertRaises(ConfigError):
            load_config(env={}, config_path=self.path)

    def test_repr_masks_secret(self):
        config = load_config(env={}, config_path=self.path)
        self.assertNotIn("sk", repr(config))

    def test_missing_required(self):
        missing = Path(self._tmp.name) / "does-not-exist.json"
        with self.assertRaises(ConfigError):
            load_config(env={"DQ_ASSETS_ENDPOINT": "https://x"}, config_path=missing)


class TestCliAssetsDoctor(unittest.TestCase):
    def test_help(self):
        from dragon_quant import cli
        buffer = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "assets", "doctor", "-h"]):
            with self.assertRaises(SystemExit) as raised, redirect_stdout(buffer):
                cli.main()
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("Usage: dragon-quant assets doctor", buffer.getvalue())

    def test_config_error_exit_2(self):
        from dragon_quant import cli
        buffer = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "assets", "doctor"]), \
             patch("dragon_quant.assets_sync.credentials.load_config",
                   side_effect=ConfigError("missing")):
            with self.assertRaises(SystemExit) as raised, redirect_stdout(buffer):
                cli.main()
        self.assertEqual(raised.exception.code, 2)
        payload = json.loads(buffer.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["summary"], {})
        self.assertIn("reason", payload["failed"][0])

    def test_invalid_samples_exit_2(self):
        from dragon_quant import cli
        buffer = io.StringIO()
        with patch("sys.argv", ["dragon-quant", "assets", "doctor", "--samples", "0"]):
            with self.assertRaises(SystemExit) as raised, redirect_stdout(buffer):
                cli.main()
        self.assertEqual(raised.exception.code, 2)
        payload = json.loads(buffer.getvalue())
        self.assertIn("--samples", payload["failed"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
