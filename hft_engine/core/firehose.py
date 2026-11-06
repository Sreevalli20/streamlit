"""
core/firehose.py  -  Phase 1: Async Market Firehose
====================================================
Asynchronous order generator with a configurable target rate.
Packs mock trade orders into binary bytes and writes them into the
shared mmap ring buffer.

The firehose exposes a shared EngineMetrics object so the dashboard
can read live stats without any extra synchronisation overhead.

Usage (standalone):
    python -m core.firehose --duration 5 --target-ops 100000

Usage (embedded):
    from core.firehose import Firehose
    fh = Firehose(ring_buffer, metrics, target_ops=100_000)
    asyncio.run(fh.run(duration=10.0))
"""

import argparse
import asyncio
import math
import random
import threading
import time

from core.constants import DEFAULT_DURATION, DEFAULT_TARGET_OPS, SIDE_BUY, SIDE_SELL
from core.metrics import EngineMetrics
from core.order_struct import Order

# Simulated price levels
_PRICES = [round(99.0 + i * 0.25, 2) for i in range(9)]


class Firehose:
    """Async producer that blasts orders into a RingBuffer."""

    BATCH = 64   # orders per asyncio iteration

    def __init__(self, ring_buffer, metrics: EngineMetrics,
                 target_ops: int = DEFAULT_TARGET_OPS):
        if isinstance(target_ops, bool) or not isinstance(target_ops, int):
            raise TypeError("target_ops must be a positive integer")
        if target_ops <= 0:
            raise ValueError("target_ops must be positive")
        self._rb      = ring_buffer
        self._metrics = metrics
        self._target  = target_ops
        self._rng     = random.Random()
        self._seq     = 0
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._control_lock = threading.Lock()

    def stop(self) -> None:
        """Request that a running producer stop at the next batch boundary."""
        self._stop_event.set()
        self.resume()

    def pause(self) -> None:
        """Pause order generation without stopping the producer."""
        with self._control_lock:
            self._pause_event.clear()

    def resume(self) -> None:
        """Resume order generation after a pause."""
        with self._control_lock:
            self._pause_event.set()

    @property
    def paused(self) -> bool:
        return not self._pause_event.is_set()

    @property
    def target_ops(self) -> int:
        return self._target

    def set_target_ops(self, target_ops: int) -> None:
        if isinstance(target_ops, bool) or not isinstance(target_ops, int):
            raise TypeError("target_ops must be a positive integer")
        if target_ops <= 0:
            raise ValueError("target_ops must be positive")
        with self._control_lock:
            self._target = target_ops

    async def run(self, duration: float = DEFAULT_DURATION) -> None:
        """Run the firehose for `duration` seconds."""
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise TypeError("duration must be a positive finite number")
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration must be a positive finite number")
        m         = self._metrics
        rb        = self._rb
        rng       = self._rng
        prices    = _PRICES
        batch     = self.BATCH


        m.start_time = time.monotonic()
        deadline     = m.start_time + duration

        while not self._stop_event.is_set() and time.monotonic() < deadline:
            with self._control_lock:
                if not self._pause_event.is_set():
                    sleep = 0.01
                else:
                    t0 = time.perf_counter()
                    for _ in range(batch):
                        price = rng.choice(prices) + rng.uniform(-0.005, 0.005)
                        qty = rng.randint(1, 1000)
                        side = SIDE_BUY if rng.random() < 0.5 else SIDE_SELL
                        order = Order(
                            order_id=self._seq,
                            price=price,
                            quantity=qty,
                            side=side,
                        )
                        self._seq += 1

                        if rb.put(order):
                            m.sent()
                        else:
                            m.dropped()

                    elapsed = time.perf_counter() - t0
                    sleep = batch / self._target - elapsed
            if sleep > 0.0005:
                await asyncio.sleep(sleep)
            else:
                await asyncio.sleep(0)   # yield to event loop


if __name__ == "__main__":
    import os
    import sys
    import tempfile
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    from core.ring_buffer import RingBuffer

    ap = argparse.ArgumentParser()
    ap.add_argument("--duration",   type=float, default=5.0)
    ap.add_argument("--target-ops", type=int,   default=DEFAULT_TARGET_OPS)
    args = ap.parse_args()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".rb") as f:
        tmp = f.name
    rb = None
    try:
        rb      = RingBuffer(tmp, create=True)
        metrics = EngineMetrics()
        fh      = Firehose(rb, metrics, target_ops=args.target_ops)
        print(f"Firehose: target={args.target_ops:,}/sec  duration={args.duration}s")
        asyncio.run(fh.run(duration=args.duration))
        s = metrics.snapshot()
        print(f"  Sent    : {s['orders_sent']:>10,}")
        print(f"  Dropped : {s['orders_dropped']:>10,}")
        print(f"  Rate    : {s['enqueue_rate']:>10,.0f} orders/sec")
    finally:
        try:
            if rb is not None:
                rb.close()
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
