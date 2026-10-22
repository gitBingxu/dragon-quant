"""AWS Signature V4（S3 兼容端点）。

契约见技术方案 §8：
  - path-style（默认）/ virtual-host 由调用方拼接 URL；
  - canonical URI 逐段百分号编码；
  - PUT/DELETE 用真实 body 的 sha256，GET/HEAD 用空体 sha256；
  - host / x-amz-content-sha256 / x-amz-date 必签；content-md5 与 x-amz-meta-* 一并入签；
  - 每次重试必须重算 x-amz-date 与签名。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlsplit

ALGORITHM = "AWS4-HMAC-SHA256"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# 不参与签名的头：由传输层管理，或本身就是签名结果。
_UNSIGNED_HEADERS = {
    "authorization",
    "user-agent",
    "content-length",
    "expect",
    "connection",
    "transfer-encoding",
}


def quote_segment(value: str) -> str:
    """RFC 3986 编码（保留 unreserved 字符）。"""
    return quote(value, safe="-_.~", encoding="utf-8")


def encode_path(path: str) -> str:
    """逐段编码路径，保留 `/` 分隔符。"""
    if not path:
        return "/"
    return "/".join(quote_segment(segment) for segment in path.split("/"))


def encode_query_pairs(pairs: list[tuple[str, str]]) -> str:
    """按 SigV4 口径编码 query（空格为 %20，不是 +）。"""
    return "&".join(f"{quote_segment(str(k))}={quote_segment(str(v))}" for k, v in pairs)


def canonical_query(query: str) -> str:
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    encoded = sorted((quote_segment(k), quote_segment(v)) for k, v in pairs)
    return "&".join(f"{k}={v}" for k, v in encoded)


def _canonical_headers(headers: dict[str, str]) -> tuple[str, str]:
    items: list[tuple[str, str]] = []
    for name, value in headers.items():
        lower = name.lower()
        if lower in _UNSIGNED_HEADERS:
            continue
        normalized = " ".join(str(value).strip().split())
        items.append((lower, normalized))
    items.sort()
    canonical = "".join(f"{name}:{value}\n" for name, value in items)
    signed = ";".join(name for name, _ in items)
    return canonical, signed


def signing_key(secret_key: str, date: str, region: str, service: str = "s3") -> bytes:
    key = hmac.new(("AWS4" + secret_key).encode("utf-8"), date.encode("utf-8"), hashlib.sha256).digest()
    key = hmac.new(key, region.encode("utf-8"), hashlib.sha256).digest()
    key = hmac.new(key, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(key, b"aws4_request", hashlib.sha256).digest()


def sign_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    *,
    access_key: str,
    secret_key: str,
    region: str,
    service: str = "s3",
    amz_date: str | None = None,
) -> dict[str, str]:
    """返回包含 Authorization 的完整请求头（键统一小写）。"""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"

    out = {name.lower(): str(value) for name, value in (headers or {}).items()}
    out["host"] = host
    payload_hash = hashlib.sha256(body).hexdigest()
    out["x-amz-content-sha256"] = payload_hash
    amz_date = amz_date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out["x-amz-date"] = amz_date

    canonical_headers, signed_headers = _canonical_headers(out)
    canonical_request = "\n".join([
        method.upper(),
        parts.path or "/",   # 调用方已用 encode_path() 编码；这里不重复编码
        canonical_query(parts.query),
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    date = amz_date[:8]
    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        ALGORITHM,
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        signing_key(secret_key, date, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    out["authorization"] = (
        f"{ALGORITHM} Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return out
