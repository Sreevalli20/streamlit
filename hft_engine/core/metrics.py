"""
core/metrics.py  -  Latency histogram and throughput counters
=============================================================
Thread-safe metrics containers for latency and throughput reporting.
Latency samples use a fixed-size rolling window; throughput tracks events in
the configured recent-time window.
"""

import math
import threading
import time
from collections import deque

from core.constants import LATENCY_WINDOW


class LatencyHistogram:
    """
    Fixed-size rolling window of latency samples (nanoseconds).
    Thread-safe via a threading.Lock.
    """

    def __init__(self, window: int = LATENCY_WINDOW):
        if isinstance(window, bool) or not isinstance(window, int):
            raise TypeError("window must be a positive integer")
        if window <= 0:
            raise ValueError("window must be positive")
        self._window  = window
        self._samples : deque[float] = deque(maxlen=window)
        self._lock    = threading.Lock()
        self._total   = 0
        self._count   = 0

    def record_ns(self, nanoseconds: float) -> None:
        if not math.isfinite(nanoseconds) or nanoseconds < 0:
            raise ValueError("nanoseconds must be finite and non-negative")
        us = nanoseconds / 1_000.0
        with self._lock:
            self._samples.append(us)
            self._total += us
            self._count += 1

    def record_us(self, microseconds: float) -> None:
        if not math.isfinite(microseconds) or microseconds < 0:
            raise ValueError("microseconds must be finite and non-negative")
        with self._lock:
            self._samples.append(microseconds)
            self._total += microseconds
            self._count += 1

    def percentile(self, p: float) -> float:
        """Return the p-th percentile in microseconds (0 < p < 100)."""
        if not math.isfinite(p) or not 0 < p < 100:
            raise ValueError("percentile must be between 0 and 100, exclusive")
        with self._lock:
            if not self._samples:
                return 0.0
            s = sorted(self._samples)
            idx = min(len(s) - 1, max(0, math.ceil(len(s) * p / 100) - 1))
            return s[idx]

    @property
    def avg(self) -> float:
        with self._lock:
            return (sum(self._samples) / len(self._samples)
                    if self._samples else 0.0)

    @property
    def min(self) -> float:
        with self._lock:
            return min(self._samples) if self._samples else 0.0

    @property
    def max(self) -> float:
        with self._lock:
            return max(self._samples) if self._samples else 0.0

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def snapshot(self) -> dict:
        return {
            "avg"    : self.avg,
            "min"    : self.min,
            "max"    : self.max,
            "p50"    : self.percentile(50),
            "p90"    : self.percentile(90),
            "p99"    : self.percentile(99),
            "p999"   : self.percentile(99.9),
            "count"  : self.count,
        }

    def recent(self, count: int = 60) -> list[float]:
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("count must be a positive integer")
        with self._lock:
            return list(self._samples)[-count:]


class Throughput:
    """
    Rolling 1-second throughput counter updated on the hot path.
    Records timestamps in a deque and counts events in the last N seconds.
    """

    def __init__(self, window_s: float = 1.0):
        if not math.isfinite(window_s) or window_s <= 0:
            raise ValueError("window_s must be a positive finite number")
        self._window  = window_s
        self._times   : deque[float] = deque()
        self._lock    = threading.Lock()
        self._total   = 0

    def tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._times.append(now)
            self._total += 1
            cutoff = now - self._window
            while self._times and self._times[0] < cutoff:
                self._times.popleft()

    @property
    def rate(self) -> float:
        """Events per second over the rolling window."""
        with self._lock:
            if not self._times:
                return 0.0
            now    = time.monotonic()
            cutoff = now - self._window
            while self._times and self._times[0] < cutoff:
                self._times.popleft()
            return len(self._times) / self._window

    @property
    def total(self) -> int:
        with self._lock:
            return self._total


class EngineMetrics:
    """
    Aggregated in-process metrics object with synchronized counters.

    Metrics are local to the process; separate producer and consumer
    processes maintain independent instances.
    """

    def __init__(self):
        self.orders_sent    = 0
        self.orders_dropped = 0
        self.orders_matched = 0
        self.trades_fired   = 0
        self.volume_traded  = 0
        self.start_time     = 0.0
        self._lock          = threading.Lock()

        self.match_latency  = LatencyHistogram()
        self.enqueue_tp     = Throughput()
        self.dequeue_tp     = Throughput()

    def sent(self) -> None:
        with self._lock:
            self.orders_sent += 1
        self.enqueue_tp.tick()

    def dropped(self) -> None:
        with self._lock:
            self.orders_dropped += 1

    def matched(self, trades: int, volume: int, latency_ns: float) -> None:
        with self._lock:
            self.orders_matched += 1
            self.trades_fired   += trades
            self.volume_traded  += volume
        self.dequeue_tp.tick()
        self.match_latency.record_ns(latency_ns)

    def reset(self) -> None:
        """Reset aggregate counters and rolling measurements."""
        with self._lock:
            self.orders_sent = 0
            self.orders_dropped = 0
            self.orders_matched = 0
            self.trades_fired = 0
            self.volume_traded = 0
            self.start_time = time.monotonic()
            self.match_latency = LatencyHistogram()
            self.enqueue_tp = Throughput()
            self.dequeue_tp = Throughput()

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = time.monotonic() - self.start_time if self.start_time else 0.0
            return {
                "orders_sent"    : self.orders_sent,
                "orders_dropped" : self.orders_dropped,
                "orders_matched" : self.orders_matched,
                "trades_fired"   : self.trades_fired,
                "volume_traded"  : self.volume_traded,
                "elapsed_s"      : elapsed,
                "enqueue_rate"   : self.enqueue_tp.rate,
                "dequeue_rate"   : self.dequeue_tp.rate,
                "latency"        : self.match_latency.snapshot(),
            }
