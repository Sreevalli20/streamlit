"""
scripts/run_demo.py  -  Integrated Demo
========================================
Single-process demo that runs producer + consumer + prints a quick
summary.  Great for CI or a rapid smoke-test.

Usage:
    python scripts/run_demo.py --duration 5 --target-ops 50000
"""

import asyncio
import gc
import os
import platform
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

import argparse

from core.firehose import Firehose
from core.metrics import EngineMetrics
from core.ring_buffer import RingBuffer
from engine.limit_order_book import LimitOrderBook


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration",   type=float, default=5.0)
    ap.add_argument("--target-ops", type=int,   default=50_000)
    args = ap.parse_args()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".rb") as f:
        tmp = f.name

    gc_was_enabled = gc.isenabled()
    try:
        rb      = RingBuffer(tmp, create=True)
        metrics = EngineMetrics()
        fh      = Firehose(rb, metrics, target_ops=args.target_ops)
        lob     = LimitOrderBook()
        stop    = threading.Event()
        worker_errors = []

        def consumer():
            try:
                while not stop.is_set() or not rb.is_empty():
                    order = rb.get()
                    if order is None:
                        time.sleep(0.0001)
                        continue
                    t0 = time.perf_counter_ns()
                    trades = lob.submit(order)
                    lat_ns = time.perf_counter_ns() - t0
                    metrics.matched(
                        len(trades), sum(t.quantity for t in trades), lat_ns
                    )
            except Exception as exc:  # noqa: BLE001 - report worker failures after shutdown.
                worker_errors.append(exc)
                stop.set()
                fh.stop()

        gc.disable()
        cons = threading.Thread(target=consumer, name="consumer")
        cons.start()

        try:
            print(
                f"Demo: Python {platform.python_version()} on {platform.platform()}\n"
                f"Workload: target {args.target_ops:,} ord/sec for "
                f"{args.duration}s (actual counts/timing follow)"
            )
            asyncio.run(fh.run(duration=args.duration))
        finally:
            fh.stop()
            stop.set()
            cons.join()

        if worker_errors:
            raise RuntimeError("consumer thread failed") from worker_errors[0]

        s  = metrics.snapshot()
        st = lob.stats()
        latency = s["latency"]

        print(f"\n{'='*55}")
        print(f"  Orders sent   : {s['orders_sent']:>12,}")
        print(f"  Orders processed: {s['orders_matched']:>10,}")
        print(f"  Trades fired  : {s['trades_fired']:>12,}")
        print(f"  Volume traded : {s['volume_traded']:>12,}")
        print(f"  Dropped       : {s['orders_dropped']:>12,}")
        print(f"  Lat avg       : {latency['avg']:>12.2f} us")
        print(f"  Lat p50       : {latency['p50']:>12.2f} us")
        print(f"  Lat p99       : {latency['p99']:>12.2f} us")
        print(f"  Best Bid      : {st['best_bid']}")
        print(f"  Best Ask      : {st['best_ask']}")
        print(f"{'='*55}")
    finally:
        try:
            if "rb" in locals():
                rb.close()
        finally:
            if gc_was_enabled:
                gc.enable()
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
