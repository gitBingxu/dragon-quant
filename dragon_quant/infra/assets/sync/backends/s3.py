"""S3 兼容对象存储后端（Cloudflare R2 / Backblaze B2）。

- 默认 path-style；virtual-host 由配置切换。
- LIST 内部分页，超过 10 万 key 直接报错，避免静默截断。
- 5xx / 429 / 网络错误指数退避，每次重试重新签名。
"""

from __future__ import annotations

import base64
import random
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from dragon_quant.infra.assets.sync.backends.signer import encode_path, encode_query_pairs, sign_request
from dragon_quant.infra.assets.sync.credentials import AssetsConfig
from dragon_quant.infra.assets.sync.errors import NotFoundError, StorageError
from dragon_quant.infra.assets.sync.models import ObjectMeta

_RETRYABLE = {429, 500, 502, 503, 504}
_MAX_LIST_KEYS = 100_000


def content_md5_b64(md5_hex: str) -> str:
    """Content-MD5 请求头要求 base64(md5 原始字节)。"""
    return base64.b64encode(bytes.fromhex(md5_hex)).decode("ascii")


class S3Backend:
    def __init__(self, config: AssetsConfig, *, timeout: float = 60.0,
                 max_attempts: int = 5, sleep=time.sleep):
        self.config = config
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._clock_checked = False

    # ─── URL 构造 ────────────────────────────────────────────────────────

    def _endpoint(self) -> str:
        endpoint = self.config.endpoint.rstrip("/")
        if self.config.addressing == "virtual":
            scheme, rest = endpoint.split("://", 1)
            return f"{scheme}://{self.config.bucket}.{rest}"
        return endpoint

    def _bucket_path(self) -> str:
        """LIST 必须落在桶根；不能把 config.prefix 当成 path segment。"""
        return f"/{self.config.bucket}" if self.config.addressing == "path" else "/"

    def _raw_path(self, key: str) -> str:
        key = key.lstrip("/")
        if self.config.prefix:
            key = f"{self.config.prefix}/{key}" if key else self.config.prefix
        if self.config.addressing == "path":
            return f"/{self.config.bucket}/{key}" if key else f"/{self.config.bucket}"
        return f"/{key}" if key else "/"

    def _url(self, key: str, query_pairs: list[tuple[str, str]] | None = None,
             path_override: str | None = None) -> str:
        raw_path = path_override if path_override is not None else self._raw_path(key)
        url = self._endpoint() + encode_path(raw_path)
        if query_pairs:
            url += "?" + encode_query_pairs(query_pairs)
        return url

    # ─── 请求与重试 ──────────────────────────────────────────────────────

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                self._sleep(min(float(retry_after), 30.0))
                return
            except ValueError:
                pass
        self._sleep(min(2 ** attempt + random.random(), 30.0))

    def _request(self, method: str, key: str, *, query_pairs=None, body: bytes = b"",
                 headers: dict[str, str] | None = None, ok=(200,), allow_404: bool = False,
                 path_override: str | None = None):
        url = self._url(key, query_pairs, path_override)
        last_error = ""
        for attempt in range(self.max_attempts):
            signed = sign_request(
                method, url, headers, body,
                access_key=self.config.access_key_id,
                secret_key=self.config.secret_access_key,
                region=self.config.region,
            )
            request = urllib.request.Request(
                url,
                data=body if method in ("PUT", "POST") else None,
                method=method,
                headers=signed,
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    content = response.read()
                    response_headers = dict(response.headers)
                    self._check_clock(response_headers)
                    if response.status not in ok:
                        raise StorageError(f"{method} {url} -> {response.status}")
                    return response.status, response_headers, content
            except urllib.error.HTTPError as exc:
                payload = exc.read()
                error_headers = dict(exc.headers or {})
                retry_after = error_headers.get("Retry-After")
                exc.close()
                error_code = self._error_code(payload)
                if exc.code == 400 and error_code == "BadDigest" and attempt == 0:
                    # 完整性失败只重传一次（方案 §9）
                    last_error = "BadDigest"
                    continue
                if exc.code == 404 and allow_404:
                    return 404, error_headers, payload
                if exc.code in _RETRYABLE and attempt + 1 < self.max_attempts:
                    self._backoff(attempt, retry_after)
                    last_error = f"{exc.code}: {payload[:200]!r}"
                    continue
                raise StorageError(f"{method} {url} -> {exc.code}: {payload[:300]!r}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt + 1 < self.max_attempts:
                    self._backoff(attempt, None)
                    last_error = str(exc)
                    continue
                raise StorageError(f"{method} {url} 失败: {exc}") from exc
        raise StorageError(f"{method} {url} 重试耗尽: {last_error}")

    def _check_clock(self, headers) -> None:
        """首次成功响应后检查服务器时钟偏移（方案 §8）。"""
        if self._clock_checked:
            return
        self._clock_checked = True
        value = {str(name).lower(): value for name, value in headers.items()}.get("date")
        if not value:
            return
        try:
            server_time = parsedate_to_datetime(value)
            if server_time.tzinfo is None:
                server_time = server_time.replace(tzinfo=timezone.utc)
            skew = abs((datetime.now(timezone.utc) - server_time).total_seconds())
            if skew > 300:
                print(f"⚠️ 对象存储时钟偏移 {skew:.0f}s，签名可能失败", file=sys.stderr)
        except (TypeError, ValueError):
            return

    @staticmethod
    def _error_code(payload: bytes) -> str:
        if not payload:
            return ""
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return ""
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] == "Code":
                return node.text or ""
        return ""

    # ─── 对象操作 ────────────────────────────────────────────────────────

    def put(self, key: str, data: bytes, metadata: dict[str, str] | None = None,
            content_md5: str | None = None) -> None:
        headers: dict[str, str] = {}
        if content_md5:
            headers["content-md5"] = content_md5
        for name, value in (metadata or {}).items():
            headers[f"x-amz-meta-{name.lower()}"] = str(value)
        self._request("PUT", key, body=data, headers=headers, ok=(200, 201))

    def get(self, key: str) -> bytes:
        status, _, body = self._request("GET", key, allow_404=True)
        if status == 404:
            raise NotFoundError(key)
        return body

    def head(self, key: str) -> ObjectMeta | None:
        status, headers, _ = self._request("HEAD", key, allow_404=True)
        if status == 404:
            return None
        return self._meta(key, headers)

    def list(self, prefix: str) -> list[ObjectMeta]:
        logical_prefix = prefix.lstrip("/")
        physical_prefix = "/".join(part for part in (self.config.prefix, logical_prefix) if part)
        strip = f"{self.config.prefix}/" if self.config.prefix else ""
        results: list[ObjectMeta] = []
        token: str | None = None
        while True:
            pairs = [("list-type", "2"), ("prefix", physical_prefix)]
            if token:
                pairs.append(("continuation-token", token))
            _, _, body = self._request("GET", "", query_pairs=pairs,
                                       path_override=self._bucket_path())
            page, truncated, token = self._parse_list(body)
            for item in page:
                if strip and not item.key.startswith(strip):
                    # prefix 是纯字符串前缀：list("") 可能返回 prod2/... 这类兄弟命名空间
                    continue
                if strip:
                    item = replace(item, key=item.key[len(strip):])
                results.append(item)
            if len(results) > _MAX_LIST_KEYS:
                raise StorageError(f"LIST 结果超过 {_MAX_LIST_KEYS}；请用 --from/--to 缩小范围")
            if not truncated:
                return results

    def delete(self, key: str) -> None:
        status, _, _ = self._request("DELETE", key, ok=(200, 204), allow_404=True)
        if status == 404:
            return

    # ─── 解析 ────────────────────────────────────────────────────────────

    @staticmethod
    def _meta(key: str, headers) -> ObjectMeta:
        lower = {str(name).lower(): value for name, value in headers.items()}
        metadata = {
            name[len("x-amz-meta-"):]: value
            for name, value in lower.items()
            if name.startswith("x-amz-meta-")
        }
        try:
            size = int(lower.get("content-length", 0) or 0)
        except ValueError:
            size = 0
        return ObjectMeta(
            key=key,
            size=size,
            etag=lower.get("etag"),
            last_modified=lower.get("last-modified"),
            metadata=metadata,
        )

    @staticmethod
    def _parse_list(data: bytes) -> tuple[list[ObjectMeta], bool, str | None]:
        if not data:
            return [], False, None
        root = ET.fromstring(data)
        items: list[ObjectMeta] = []
        truncated = False
        token: str | None = None
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "Contents":
                values = {child.tag.rsplit("}", 1)[-1]: (child.text or "") for child in node}
                try:
                    size = int(values.get("Size", 0) or 0)
                except ValueError:
                    size = 0
                items.append(ObjectMeta(
                    key=values.get("Key", ""),
                    size=size,
                    etag=values.get("ETag"),
                    last_modified=values.get("LastModified"),
                ))
            elif tag == "IsTruncated":
                truncated = (node.text or "").strip().lower() == "true"
            elif tag == "NextContinuationToken":
                token = node.text
        return items, truncated, token
