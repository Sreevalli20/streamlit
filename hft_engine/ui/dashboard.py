"""
ui/dashboard.py  -  Phase 2: Live Terminal Dashboard
=====================================================
ANSI-based live dashboard. Refreshes every 50 ms (20 FPS).
Works in Windows Terminal, PowerShell 7+, and any ANSI-capable emulator.

Integrates:
  - core.firehose  (Firehose class)      - async order generator
  - core.ring_buffer (RingBuffer)        - shared mmap buffer
  - core.metrics (EngineMetrics)         - shared live stats
  - engine.limit_order_book              - matching engine

Usage:
    python main.py dashboard --duration 30 --target-ops 100000
    python -m ui.dashboard --duration 10
"""

import argparse
import gc
import os
import sys
import tempfile
import threading
import time

# Reconfigure stdout to UTF-8 so box-drawing chars render in Windows Terminal
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

from core.constants import (
    BOOK_LEVELS,
    DASHBOARD_REFRESH_MS,
    DEFAULT_DURATION,
    DEFAULT_TARGET_OPS,
)
from core.firehose import Firehose
from core.metrics import EngineMetrics
from core.ring_buffer import RingBuffer
from engine.limit_order_book import LimitOrderBook

# ---- ANSI helpers -----------------------------------------------------------
_R = "\033["
def _c(code, s): return f"{_R}{code}m{s}{_R}0m"

def bold(s):    return _c("1",  s)
def dim(s):     return _c("2",  s)
def green(s):   return _c("92", s)
def red(s):     return _c("91", s)
def cyan(s):    return _c("96", s)
def yellow(s):  return _c("93", s)
def magenta(s): return _c("95", s)
def white(s):   return _c("97", s)

CLEAR  = "\033[2J\033[H"
HOME   = "\033[H"


# ---- Dashboard --------------------------------------------------------------

class Dashboard:
    """
    Wires producer (Firehose), shared RingBuffer, and consumer (LOB) together
    and renders a live ASCII dashboard to the terminal every REFRESH_MS ms.
    """

    def __init__(self, duration: float = DEFAULT_DURATION,
                 target_ops: int = DEFAULT_TARGET_OPS):
        self.duration   = duration
        self.target_ops = target_ops
        self._stop      = threading.Event()
        self._lob       = LimitOrderBook()
        self._metrics   = EngineMetrics()
        self._producer_error = None
        self._consumer_error = None

        # Ring buffer backed by a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".rb") as tmp:
            self._tmp_path = tmp.name

        rb = None
        try:
            rb = RingBuffer(self._tmp_path, create=True)
            self._rb = rb
            self._fh = Firehose(self._rb, self._metrics, target_ops=target_ops)
        except BaseException:
            try:
                if rb is not None:
                    rb.close()
            finally:
                try:
                    os.unlink(self._tmp_path)
                except FileNotFoundError:
                    pass
            raise

    # ---- threads ------------------------------------------------------------

    def _producer_thread(self):
        import asyncio
        try:
            asyncio.run(self._fh.run(duration=self.duration))
        except Exception as exc:  # noqa: BLE001 - report producer failures after shutdown.
            self._producer_error = exc
            self._stop.set()

    def _consumer_thread(self):
        lob     = self._lob
        rb      = self._rb
        metrics = self._metrics
        stop    = self._stop
        try:
            while not stop.is_set() or not rb.is_empty():
                order = rb.get()
                if order is None:
                    time.sleep(0.0001)
                    continue
                t0     = time.perf_counter_ns()
                trades = lob.submit(order)
                lat_ns = time.perf_counter_ns() - t0
                metrics.matched(
                    trades=len(trades),
                    volume=sum(t.quantity for t in trades),
                    latency_ns=lat_ns,
                )
        except Exception as exc:  # noqa: BLE001 - report consumer failures after shutdown.
            self._consumer_error = exc
            stop.set()
            self._fh.stop()

    # ---- rendering ----------------------------------------------------------

    def _render(self, first: bool = False):
        lob = self._lob
        m   = self._metrics
        s   = m.snapshot()
        lat = s["latency"]
        tob = lob.top_of_book(BOOK_LEVELS)
        bids = tob["bids"]
        asks = tob["asks"]

        best_bid = lob.best_bid or 0.0
        best_ask = lob.best_ask or 0.0
        spread   = best_ask - best_bid if (best_bid and best_ask) else 0.0
        if s["elapsed_s"] > 0:
            elapsed = s["elapsed_s"]
        elif m.start_time:
            elapsed = max(0.0, time.monotonic() - m.start_time)
        else:
            elapsed = 0.0

        W = 72
        lines = []
        if first:
            lines.append(CLEAR)
        else:
            lines.append(HOME)

        lines.append(bold(cyan("=" * W)))
        lines.append(bold(cyan(
            f"  [HFT ENGINE]  Elapsed: {elapsed:.1f}s / {self.duration:g}s"
            f"  Buffer: {self._rb.size():>6,}"
        )))
        lines.append(bold(cyan("=" * W)))
        lines.append("")

        # -- Firehose stats ---------------------------------------------------
        lines.append(bold("  MARKET FIREHOSE"))
        lines.append(
            f"    Target  : {yellow(f'{self.target_ops:>10,}')} ord/sec   "
            f"Sent     : {green(f'{s["orders_sent"]:>10,}')}"
        )
        lines.append(
            f"    Dropped : {red(f'{s["orders_dropped"]:>10,}')}          "
            f"Live rate: {green(f'{s["enqueue_rate"]:>10,.0f}')} ord/sec"
        )
        lines.append("")

        # -- Engine stats -----------------------------------------------------
        lines.append(bold("  MATCHING ENGINE"))
        lines.append(
            f"    Orders  : {cyan(f'{s["orders_matched"]:>10,}')}     "
            f"Trades   : {cyan(f'{s["trades_fired"]:>8,}')}"
        )
        lines.append(
            f"    Volume  : {cyan(f'{s["volume_traded"]:>10,}')}     "
            f"Consume  : {cyan(f'{s["dequeue_rate"]:>8,.0f}')} ord/sec"
        )
        lines.append("")

        # -- Latency panel ----------------------------------------------------
        avg = lat["avg"]
        p99 = lat["p99"]
        col = green if p99 < 50 else yellow
        lines.append(bold("  LATENCY TELEMETRY  (match engine, GC disabled)"))
        lines.append(
            f"    avg   : {col(f'{avg:>8.2f} us')}    "
            f"p50 : {col(f'{lat["p50"]:>7.2f} us')}"
        )
        lines.append(
            f"    p99   : {col(f'{p99:>8.2f} us')}    "
            f"p999: {col(f'{lat["p999"]:>7.2f} us')}    "
            f"n={lat['count']:,}"
        )
        lines.append("")

        # -- Order book -------------------------------------------------------
        lines.append(bold("  ORDER BOOK  (top-of-book)"))
        hdr = f"  {'Level':<8}  {'Bid Qty':>10}  {'Bid Px':>10}  {'Ask Px':>10}  {'Ask Qty':>10}"
        lines.append(dim(hdr))
        lines.append(dim("  " + "-" * 56))
        for i in range(max(len(bids), len(asks))):
            b_qty = f"{bids[i][1]:>10,}"  if i < len(bids) else " " * 10
            b_px  = f"{bids[i][0]:>10.4f}" if i < len(bids) else " " * 10
            a_px  = f"{asks[i][0]:>10.4f}" if i < len(asks) else " " * 10
            a_qty = f"{asks[i][1]:>10,}"  if i < len(asks) else " " * 10
            lines.append(
                f"  {'L'+str(i+1):<8}  "
                f"{green(b_qty)}  {green(b_px)}  {red(a_px)}  {red(a_qty)}"
            )
        lines.append(dim("  " + "-" * 56))
        lines.append(
            f"    Spread : {yellow(f'{spread:.4f}')}   "
            f"Best Bid: {green(f'{best_bid:.4f}')}   "
            f"Best Ask: {red(f'{best_ask:.4f}')}"
        )
        lines.append("")
        lines.append(dim(
            f"  Refreshing every {DASHBOARD_REFRESH_MS} ms  |  "
            f"Bid depth: {lob.bid_depth:,}  Ask depth: {lob.ask_depth:,}  |  "
            f"Ctrl+C to stop"
        ))
        lines.append(bold(cyan("=" * W)))

        # Pad to fixed height to avoid flicker
        while len(lines) < 36:
            lines.append("")

        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()

    # ---- main loop ----------------------------------------------------------

    def run(self):
        gc_was_enabled = gc.isenabled()
        gc.disable()
        prod = threading.Thread(target=self._producer_thread, name="producer")
        cons = threading.Thread(target=self._consumer_thread, name="consumer")
        prod_started = False
        cons_started = False

        try:
            prod.start()
            prod_started = True
            cons.start()
            cons_started = True
            first = True
            while prod_started and prod.is_alive():
                self._render(first=first)
                first = False
                time.sleep(DASHBOARD_REFRESH_MS / 1_000)
        except KeyboardInterrupt:
            self._fh.stop()
        finally:
            self._fh.stop()
            if prod_started:
                prod.join()
            self._stop.set()
            if cons_started:
                cons.join()
            if gc_was_enabled:
                gc.enable()
            try:
                self._rb.close()
            finally:
                try:
                    os.unlink(self._tmp_path)
                except FileNotFoundError:
                    pass

        print("\n  Dashboard stopped.")
        if self._producer_error is not None:
            raise RuntimeError("producer thread failed") from self._producer_error
        if self._consumer_error is not None:
            raise RuntimeError("consumer thread failed") from self._consumer_error


def main():
    ap = argparse.ArgumentParser(description="HFT Engine Live Dashboard")
    ap.add_argument("--duration",   type=float, default=DEFAULT_DURATION)
    ap.add_argument("--target-ops", type=int,   default=DEFAULT_TARGET_OPS)
    args = ap.parse_args()
    Dashboard(duration=args.duration, target_ops=args.target_ops).run()


if __name__ == "__main__":
    main()
