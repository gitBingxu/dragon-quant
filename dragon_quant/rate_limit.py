"""
RateLimiter — 按 (provider, endpoint) 分组的并发调度器

核心规则：同一 provider + 同一 endpoint 的任务排队串行，
          不同 key 的任务自由并发。
"""

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional


class _SerialQueue:
    """单 key 任务队列 — 串行消费"""

    def __init__(self, executor: ThreadPoolExecutor, key: str = "", logger=None):
        self._executor = executor
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._key = key
        self._logger = logger

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        future: Future = Future()
        self._queue.put((fn, args, kwargs, future))
        self._maybe_consume()
        return future

    def _maybe_consume(self):
        with self._lock:
            if self._running:
                return
            if self._queue.empty():
                return
            self._running = True
            self._executor.submit(self._consume_loop)

    def _consume_loop(self):
        while True:
            try:
                fn, args, kwargs, future = self._queue.get_nowait()
            except queue.Empty:
                with self._lock:
                    self._running = False
                    if not self._queue.empty():
                        self._executor.submit(self._consume_loop)
                    return
            try:
                result = fn(*args, **kwargs)
                future.set_result(result)
            except BaseException as e:
                future.set_exception(e)


class RateLimiter:
    """并发调度器

    用法:
        limiter = RateLimiter(max_workers=8)
        limiter.submit("eastmoney", "components", fn, arg1, arg2)
        limiter.submit("xueqiu", "kline", fn2, arg1)  # 同时执行
        limiter.wait_all()
    """

    def __init__(self, max_workers: int = 8, logger=None):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._queues: dict[str, _SerialQueue] = {}
        self._lock = threading.Lock()
        self._futures: list[Future] = []
        self._logger = logger

    def _key(self, provider: str, endpoint: str) -> str:
        return f"{provider}:{endpoint}"

    def submit(self, provider: str, endpoint: str, fn: Callable, *args, **kwargs) -> Future:
        """提交一个带分组的任务"""
        k = self._key(provider, endpoint)
        with self._lock:
            if k not in self._queues:
                self._queues[k] = _SerialQueue(self._executor, key=k, logger=self._logger)
            q = self._queues[k]
        future = q.submit(fn, *args, **kwargs)
        self._futures.append(future)
        return future

    def wait_all(self, timeout: Optional[float] = None) -> list[Any]:
        """等待所有已提交任务完成，返回结果列表"""
        results = []
        for f in self._futures:
            try:
                results.append(f.result(timeout=timeout))
            except Exception as e:
                results.append(None)
                # 不直接抛异常，让调用方处理
                import sys
                print(f"  ⚠️ RateLimit 任务失败: {e}", file=sys.stderr)
        self._futures.clear()
        return results

    def shutdown(self):
        self._executor.shutdown(wait=True)
