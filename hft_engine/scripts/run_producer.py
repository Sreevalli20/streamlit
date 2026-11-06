r"""
scripts/run_producer.py  -  Multi-process producer
===================================================
Run this as a SEPARATE PROCESS alongside run_consumer.py to demonstrate
true inter-process shared memory via the mmap ring buffer.

Usage (two terminals):
    Terminal 1: python scripts/run_producer.py --path /tmp/hft.rb --duration 10
    Terminal 2: python scripts/run_consumer.py --path /tmp/hft.rb

On Windows use a local path:
    Terminal 1: python scripts/run_producer.py --path D:\hft.rb --duration 10
    Terminal 2: python scripts/run_consumer.py --path D:\hft.rb
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

from core.constants import DEFAULT_DURATION, DEFAULT_TARGET_OPS
from core.firehose import Firehose
from core.metrics import EngineMetrics
from core.ring_buffer import RingBuffer


def main():
    ap = argparse.ArgumentParser(description="HFT Producer Process")
    ap.add_argument("--path",       default="D:\\hft_ipc.rb")
    ap.add_argument("--duration",   type=float, default=DEFAULT_DURATION)
    ap.add_argument("--target-ops", type=int,   default=DEFAULT_TARGET_OPS)
    args = ap.parse_args()

    rb      = RingBuffer(args.path, create=True)
    try:
        metrics = EngineMetrics()
        fh = Firehose(rb, metrics, target_ops=args.target_ops)
        print(f"[PRODUCER]  path={args.path}  target={args.target_ops:,}/s  "
              f"duration={args.duration}s")
        print(f"[PRODUCER]  ring buffer created ({rb.capacity:,} slots)")
        asyncio.run(fh.run(duration=args.duration))
        s = metrics.snapshot()
        print("[PRODUCER]  Done.")
        print(f"            Sent    : {s['orders_sent']:,}")
        print(f"            Dropped : {s['orders_dropped']:,}")
        print(f"            Rate    : {s['enqueue_rate']:,.0f} ord/sec")
    finally:
        rb.close()


if __name__ == "__main__":
    main()
