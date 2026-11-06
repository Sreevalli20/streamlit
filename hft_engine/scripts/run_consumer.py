"""
scripts/run_consumer.py  -  Multi-process consumer
===================================================
Run alongside run_producer.py.  Opens the same mmap backing file and
drains orders into the matching engine.
"""

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

from core.metrics import EngineMetrics
from core.ring_buffer import RingBuffer
from engine.limit_order_book import LimitOrderBook


def main():
    ap = argparse.ArgumentParser(description="HFT Consumer Process")
    ap.add_argument("--path",     default="D:\\hft_ipc.rb")
    ap.add_argument("--timeout",  type=float, default=60.0,
                    help="Stop after this many idle seconds")
    args = ap.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        ap.error("--timeout must be a positive finite number")

    # Wait for producer to create the file
    deadline = time.monotonic() + 10.0
    while not os.path.exists(args.path):
        if time.monotonic() > deadline:
            sys.exit(f"[CONSUMER] Timed out waiting for {args.path}")
        time.sleep(0.1)

    rb      = RingBuffer(args.path, create=False)
    lob     = LimitOrderBook()
    metrics = EngineMetrics()
    metrics.start_time = time.monotonic()

    print(f"[CONSUMER]  path={args.path}  timeout={args.timeout}s")

    last_recv = time.monotonic()
    try:
        while True:
            order = rb.get()
            if order is None:
                if time.monotonic() - last_recv > args.timeout:
                    print("[CONSUMER]  Idle timeout - stopping")
                    break
                time.sleep(0.0001)
                continue
            t0     = time.perf_counter_ns()
            trades = lob.submit(order)
            lat_ns = time.perf_counter_ns() - t0
            metrics.matched(len(trades), sum(t.quantity for t in trades), lat_ns)
            last_recv = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        rb.close()
        s  = metrics.snapshot()
        st = lob.stats()
        print("[CONSUMER]  Done.")
        print(f"            Processed: {s['orders_matched']:,}")
        print(f"            Trades  : {s['trades_fired']:,}")
        print(f"            Volume  : {s['volume_traded']:,}")
        print(f"            Lat avg : {s['latency']['avg']:.2f} us")
        print(f"            Lat p99 : {s['latency']['p99']:.2f} us")
        print(f"            Best bid: {st['best_bid']}")
        print(f"            Best ask: {st['best_ask']}")


if __name__ == "__main__":
    main()
