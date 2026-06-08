"""
tests for dragon_quant.rate_limit — 并发调度器
"""
import time
import threading
import unittest
from dragon_quant.rate_limit import RateLimiter


class TestRateLimiter(unittest.TestCase):

    def test_same_key_serialized(self):
        """同 provider 的任务必须串行执行"""
        limiter = RateLimiter(max_workers=4)
        order = []
        lock = threading.Lock()

        def task(name, delay=0.05):
            time.sleep(delay)
            with lock:
                order.append(name)
            return name

        limiter.submit("xueqiu", "kline", task, "A")
        limiter.submit("xueqiu", "kline", task, "B")
        limiter.submit("xueqiu", "kline", task, "C")
        results = limiter.wait_all(timeout=5)

        self.assertEqual(results, ["A", "B", "C"])

    def test_same_provider_diff_endpoint_serialized(self):
        """同 provider 不同 endpoint 的任务也必须串行执行"""
        limiter = RateLimiter(max_workers=4)
        order = []
        lock = threading.Lock()

        def task(name, delay=0.05):
            time.sleep(delay)
            with lock:
                order.append(name)
            return name

        limiter.submit("xueqiu", "kline", task, "A", delay=0.05)
        limiter.submit("xueqiu", "minute_kline", task, "B", delay=0.05)
        limiter.submit("xueqiu", "quote", task, "C", delay=0.05)
        results = limiter.wait_all(timeout=5)

        self.assertEqual(results, ["A", "B", "C"])

    def test_different_keys_concurrent(self):
        """不同 key 可以并发执行"""
        limiter = RateLimiter(max_workers=4)
        started = []
        lock = threading.Lock()
        running_count = []
        rlock = threading.Lock()

        def task(name, delay=0.1):
            with rlock:
                running_count.append(name)
            with lock:
                started.append(name)
            time.sleep(delay)
            with rlock:
                running_count.remove(name)
            return name

        limiter.submit("xueqiu", "kline", task, "XQ")
        limiter.submit("eastmoney", "components", task, "EM")
        limiter.wait_all(timeout=5)

        self.assertIn("XQ", started)
        self.assertIn("EM", started)

    def test_exception_propagation(self):
        limiter = RateLimiter(max_workers=2)

        def bad_task():
            raise ValueError("test error")

        limiter.submit("test", "fail", bad_task)
        results = limiter.wait_all(timeout=5)

        self.assertIsNone(results[0])

    def test_multiple_futures(self):
        limiter = RateLimiter(max_workers=2)

        results = []
        for i in range(5):
            limiter.submit("p", "e", lambda x=i: x * 2)
        results = limiter.wait_all(timeout=5)

        self.assertEqual(len(results), 5)
        self.assertIn(0, results)
        self.assertIn(8, results)

    def test_shutdown(self):
        limiter = RateLimiter(max_workers=2)
        limiter.submit("p", "e", lambda: 42)
        limiter.wait_all()
        limiter.shutdown()
        # No exception means success


if __name__ == "__main__":
    unittest.main()
