"""连通性门槛 A：PUT / HEAD / GET / LIST / DELETE + 元数据回环。"""

from __future__ import annotations

import time
import uuid

from dragon_quant.assets_sync.backends.s3 import content_md5_b64
from dragon_quant.assets_sync.codec import canonical_json, compress, decompress, hashes


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return round(ordered[index] * 1000, 2)


def run_doctor(backend, samples: int = 50, prefix: str = "_doctor") -> dict:
    if samples < 1:
        raise ValueError("samples 必须 >= 1")
    run_id = uuid.uuid4().hex[:12]
    latencies: dict[str, list[float]] = {"put": [], "head": [], "get": []}
    errors: list[dict] = []
    keys: list[str] = []
    metadata_roundtrip = True
    completed = 0

    for index in range(samples):
        key = f"{prefix}/{run_id}/obj-{index}.json.gz"
        keys.append(key)
        payload = canonical_json({"i": index, "run": run_id})
        blob = compress(payload)
        sha256, md5 = hashes(payload)
        _, blob_md5 = hashes(blob)
        metadata = {"sha256": sha256, "md5": md5, "schema": "1"}

        try:
            start = time.perf_counter()
            backend.put(key, blob, metadata, content_md5=content_md5_b64(blob_md5))
            latencies["put"].append(time.perf_counter() - start)
            completed += 1

            start = time.perf_counter()
            head = backend.head(key)
            latencies["head"].append(time.perf_counter() - start)
            if head is None or head.metadata.get("sha256") != sha256:
                metadata_roundtrip = False
                errors.append({"op": "head", "key": key, "error": "metadata roundtrip mismatch"})
            else:
                completed += 1

            start = time.perf_counter()
            fetched = backend.get(key)
            latencies["get"].append(time.perf_counter() - start)
            if decompress(fetched) != payload:
                errors.append({"op": "get", "key": key, "error": "content mismatch"})
            else:
                completed += 1
        except Exception as exc:  # 单个样本失败继续，用于统计成功率
            errors.append({"op": "io", "key": key, "error": f"{type(exc).__name__}: {exc}"})

    list_start = time.perf_counter()
    listed = []
    try:
        listed = backend.list(f"{prefix}/{run_id}/")
        missing = set(keys) - {item.key for item in listed}
        if missing:
            errors.append({"op": "list", "error": f"missing {len(missing)} keys"})
        else:
            completed += 1
    except Exception as exc:
        errors.append({"op": "list", "error": f"{type(exc).__name__}: {exc}"})
    list_ms = round((time.perf_counter() - list_start) * 1000, 2)

    cleanup_errors: list[str] = []
    for key in keys:
        try:
            backend.delete(key)
            completed += 1
        except Exception as exc:
            cleanup_errors.append(f"{key}: {type(exc).__name__}: {exc}")

    total_ops = samples * 4 + 1
    success_rate = round(completed / total_ops, 4) if total_ops else 0.0
    config = getattr(backend, "config", None)
    return {
        "ok": not errors and not cleanup_errors and metadata_roundtrip,
        "profile": getattr(config, "profile", ""),
        "endpoint": getattr(config, "endpoint", ""),
        "bucket": getattr(config, "bucket", ""),
        "samples": samples,
        "operations": total_ops,
        "success_rate": success_rate,
        "p95_ms": {name: _p95(values) for name, values in latencies.items()} | {"list": list_ms},
        "metadata_roundtrip": metadata_roundtrip,
        "listed": len(listed),
        "errors": errors[:20],
        "cleanup_errors": cleanup_errors[:20],
    }
