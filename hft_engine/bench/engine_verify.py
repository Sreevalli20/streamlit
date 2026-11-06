"""
bench/engine_verify.py  -  Phase 3: Matching Engine Verification
================================================================
Deterministic correctness and latency tests for the LOB engine.

Tests:
  1. Basic exact-price match
  2. No match (spread)
  3. Partial fill
  4. Price-Time priority (FIFO at same price)
  5. Price priority (best price fills first)
  6. Multi-level aggressive sweep
  7. Latency sample report (100k completed submissions; no fixed SLA threshold)

Usage:
    python -m bench.engine_verify
"""

import gc
import math
import platform
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from core.constants import SIDE_BUY, SIDE_SELL
from core.order_struct import Order
from engine.limit_order_book import LimitOrderBook

# ---- helpers ----------------------------------------------------------------
_failures = []

def check(label: str, cond: bool, detail: str = ""):
    tag = "  PASS" if cond else "  FAIL"
    col = "\033[92m" if cond else "\033[91m"
    msg = f"{col}{tag}\033[0m  {label}"
    if detail: msg += f"  [{detail}]"
    print(msg)
    if not cond:
        _failures.append(label)


# ---- test cases -------------------------------------------------------------

def test_basic_match():
    lob = LimitOrderBook()
    lob.submit(Order(1, 100.00, 100, SIDE_SELL))
    t = lob.submit(Order(2, 100.00, 100, SIDE_BUY))
    check("basic_match: one trade",        len(t) == 1)
    check("basic_match: price == 100.00",  t[0].price == 100.00)
    check("basic_match: qty == 100",       t[0].quantity == 100)
    check("basic_match: book empty",       lob.bid_depth == 0 and lob.ask_depth == 0)


def test_no_match():
    lob = LimitOrderBook()
    lob.submit(Order(1, 100.50, 100, SIDE_SELL))
    t = lob.submit(Order(2,  99.00, 100, SIDE_BUY))
    check("no_match: zero trades",         len(t) == 0)
    check("no_match: both sides resting",  lob.bid_depth == 1 and lob.ask_depth == 1)


def test_partial_fill():
    lob = LimitOrderBook()
    lob.submit(Order(1, 100.00, 100, SIDE_SELL))
    t = lob.submit(Order(2, 100.00, 150, SIDE_BUY))
    check("partial: one trade",       len(t) == 1)
    check("partial: fill qty=100",    t[0].quantity == 100)
    check("partial: ask empty",       lob.ask_depth == 0)
    check("partial: 50 resting bid",  lob.bid_depth == 1)


def test_price_time_priority():
    lob = LimitOrderBook()
    for i in range(3):
        lob.submit(Order(i, 100.00, 50, SIDE_SELL))
    t = lob.submit(Order(99, 100.00, 150, SIDE_BUY))
    check("ptp: 3 trades",      len(t) == 3)
    check("ptp: FIFO order",    [tr.resting_id for tr in t] == [0, 1, 2])


def test_price_priority():
    lob = LimitOrderBook()
    lob.submit(Order(1, 100.50, 100, SIDE_SELL))   # higher
    lob.submit(Order(2, 100.00, 100, SIDE_SELL))   # lower -> fills first
    t = lob.submit(Order(99, 101.00, 100, SIDE_BUY))
    check("price_prio: fills cheapest",   t[0].resting_id == 2)
    check("price_prio: trade @ 100.00",   t[0].price == 100.00)


def test_multi_sweep():
    lob = LimitOrderBook()
    for i, px in enumerate([100.00, 100.25, 100.50, 100.75, 101.00]):
        lob.submit(Order(i, px, 20, SIDE_SELL))
    t = lob.submit(Order(99, 101.00, 100, SIDE_BUY))
    check("sweep: 5 trades",      len(t) == 5)
    check("sweep: total qty=100", sum(tr.quantity for tr in t) == 100)


def test_latency_hot_path(n: int = 100_000):
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("n must be a positive integer")
    if n <= 0:
        raise ValueError("n must be positive")
    lob = LimitOrderBook()
    # Seed book so every order matches
    for i in range(500):
        lob.submit(Order(i,     100.00, 1, SIDE_BUY))
        lob.submit(Order(i+500, 100.00, 1, SIDE_SELL))

    latencies = []

    # Use gc.callbacks to detect actual collection events (Python 3.12+)
    gc_events = []
    gc_cb = lambda phase, info: gc_events.append(phase)
    gc.callbacks.append(gc_cb)
    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    run_start = time.perf_counter()
    try:
        for i in range(n):
            price = 100.0 + (i % 10) * 0.01
            side  = SIDE_BUY if i % 2 == 0 else SIDE_SELL
            o     = Order(order_id=10_000 + i, price=price, quantity=1, side=side)
            t0    = time.perf_counter_ns()
            lob.submit(o)
            latencies.append((time.perf_counter_ns() - t0) / 1_000)  # us
    finally:
        run_elapsed = time.perf_counter() - run_start
        gc.callbacks.remove(gc_cb)
        if gc_was_enabled:
            gc.enable()
    gc_pauses = sum(1 for p in gc_events if p == "start")

    s   = sorted(latencies)
    avg = sum(latencies) / len(latencies)
    p50 = s[max(0, math.ceil(n * 0.50) - 1)]
    p99 = s[max(0, math.ceil(n * 0.99) - 1)]
    p999= s[max(0, math.ceil(n * 0.999) - 1)]

    print(f"\n  Latency benchmark ({n:,} completed submissions):")
    print(f"    Python: {platform.python_version()}  |  {platform.platform()}")
    rate = n / run_elapsed if run_elapsed > 0 else 0.0
    print(f"    elapsed: {run_elapsed:.3f}s  |  rate: {rate:,.0f} submissions/s")
    print(f"    avg  : {avg:.3f} us")
    print(f"    p50  : {p50:.3f} us")
    print(f"    p99  : {p99:.3f} us")
    print(f"    p99.9: {p999:.3f} us")
    print(f"    GC collection events observed: {gc_pauses}")

    check("latency sample count", len(latencies) == n)


# ---- runner -----------------------------------------------------------------

def main():
    _failures.clear()
    suite_start = time.perf_counter()
    print(f"\n{'='*60}")
    print("  HFT ENGINE  -  Matching Engine Verification")
    print(f"  Python {platform.python_version()}  |  {platform.platform()}")
    print(f"{'='*60}\n")

    test_basic_match()
    test_no_match()
    test_partial_fill()
    test_price_time_priority()
    test_price_priority()
    test_multi_sweep()
    test_latency_hot_path()
    suite_elapsed = time.perf_counter() - suite_start

    print(f"\n{'='*60}")
    if _failures:
        print(f"\033[91m  {len(_failures)} FAILED: {_failures}\033[0m")
    else:
        print("\033[92m  All correctness checks PASSED\033[0m")
    print(f"  Total elapsed: {suite_elapsed:.3f}s")
    print(f"{'='*60}\n")
    if _failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
