"""
RateLimiter — 按 provider 分组的并发调度器

核心规则：同一 provider 的任务排队串行，
          不同 provider 的任务自由并发。

设计意图：不同 provider 打的是不同公司 / CDN 服务器，
        并发互不影响；同一 provider 打到同一 CDN 基础设施，
        必须串行防止触发反爬 / WAF 限流。
"""

import queue
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional, Union

# delay 可以是固定秒数，也可以是 (min, max) 随机区间
DelaySpec = Union[float, tuple]


def _resolve_delay(delay: DelaySpec) -> float:
    """将 delay 规格解析为本次实际休眠秒数。

    - float → 原值
    - (min, max) → [min, max] 内随机（用于降低请求频率特征，防封）
    """
    if isinstance(delay, (tuple, list)) and len(delay) == 2:
        return random.uniform(delay[0], delay[1])
    return delay


class _SerialQueue:
    """单 key 任务队列 — 串行消费"""

    def __init__(self, executor: ThreadPoolExecutor, key: str = "", logger=None, delay: DelaySpec = 0.3):
        self._executor = executor
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._key = key
        self._logger = logger
        self._delay = delay

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        future: Future = Future()
        ep = kwargs.pop("_endpoint", "")
        self._queue.put((fn, args, kwargs, future, ep))
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
                fn, args, kwargs, future, endpoint = self._queue.get_nowait()
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
            sleep_s = _resolve_delay(self._delay)
            if sleep_s > 0:
                time.sleep(sleep_s)


class RateLimiter:
    """并发调度器

    用法:
        limiter = RateLimiter(max_workers=8)
        limiter.submit("eastmoney", "components", fn, arg1, arg2)
        limiter.submit("xueqiu", "kline", fn2, arg1)  # 同时执行
        limiter.wait_all()
    """

    def __init__(self, max_workers: int = 8, logger=None, delay: DelaySpec = 0.3,
                 provider_delays: Optional[dict] = None):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._queues: dict[str, _SerialQueue] = {}
        self._lock = threading.Lock()
        self._futures: list[Future] = []
        self._logger = logger
        self._delay = delay
        # 按 provider 覆盖延迟规格，如东财用 (0.6, 1.0) 随机区间降低封禁风险
        self._provider_delays = provider_delays or {}

    def _key(self, provider: str, endpoint: str) -> str:
        return provider

    def submit(self, provider: str, endpoint: str, fn: Callable, *args, **kwargs) -> Future:
        """提交一个带分组的任务"""
        k = self._key(provider, endpoint)
        with self._lock:
            if k not in self._queues:
                delay = self._provider_delays.get(provider, self._delay)
                self._queues[k] = _SerialQueue(self._executor, key=k, logger=self._logger, delay=delay)
            q = self._queues[k]
        future = q.submit(fn, *args, _endpoint=endpoint, **kwargs)
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
