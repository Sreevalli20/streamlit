"""
main.py  -  HFT Engine Unified CLI Launcher
============================================
All three phases accessible from one entry point.

Commands
--------
  python main.py struct-test               # Phase 1: binary layout smoke test
  python main.py ring-test                 # Phase 1: ring buffer smoke test
  python main.py firehose --duration 5     # Phase 1: async firehose standalone
  python main.py verify                    # Phase 3: matching engine tests
  python main.py audit --orders 1000000    # Phase 3: transport/serialization benchmark
  python main.py dashboard --duration 30   # Phase 2: live terminal dashboard
"""

import argparse
import os
import sys

# Python version check - require 3.14.x for local, allow 3.12.x or 3.14.x for cloud deployment
MIN_PYTHON_VERSION = (3, 14, 0)

# Allow running on Render/other cloud platforms with 3.12.x or 3.14.x
is_cloud_deployment = os.getenv("RENDER") == "true" or os.getenv("CLOUD_DEPLOYMENT") == "true"

if is_cloud_deployment:
    # Cloud deployment - allow 3.12.x or 3.14.x
    if sys.version_info[:2] not in [(3, 12), (3, 14)]:
        print(f"WARNING: Cloud deployment detected. ChronosMatch is tested on Python 3.14.x")
        print(f"Current version: {sys.version}")
        print("Proceeding with available version, but behavior may differ.")
else:
    # Local deployment - require 3.14.x
    if sys.version_info[:3] < MIN_PYTHON_VERSION:
        print(f"ERROR: ChronosMatch requires Python 3.14.x or higher")
        print(f"Current version: {sys.version}")
        print("Please install the required Python version or use a virtual environment with it.")
        print("For cloud deployment, set RENDER=true or CLOUD_DEPLOYMENT=true environment variable.")
        sys.exit(1)

sys.path.insert(0, os.path.dirname(__file__))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass


def cmd_struct_test(args):
    from core.constants import ORDER_SIZE, SIDE_BUY, SIDE_SELL
    from core.order_struct import Order, pack_order, unpack_order
    print(f"ORDER_SIZE = {ORDER_SIZE} bytes  (format =QdIb)")
    for side, label in [(SIDE_BUY, "BUY"), (SIDE_SELL, "SELL")]:
        o   = Order(order_id=1, price=100.0, quantity=500, side=side)
        raw = pack_order(o)
        o2  = unpack_order(raw)
        assert o == o2, f"Round-trip failed for {label}"
        print(f"  {label:<5}  hex={raw.hex()}  round-trip OK")
    print("PASS struct self-test")


def cmd_ring_test(args):
    import os
    import tempfile

    from core.constants import SIDE_BUY, SIDE_SELL
    from core.order_struct import Order
    from core.ring_buffer import RingBuffer
    with tempfile.NamedTemporaryFile(delete=False, suffix=".rb") as f:
        tmp = f.name
    rb = None
    try:
        rb = RingBuffer(tmp, capacity=16, create=True)
        print(repr(rb))
        for i in range(16):
            rb.put(Order(order_id=i, price=100.0+i, quantity=10,
                         side=SIDE_BUY if i%2==0 else SIDE_SELL))
        assert rb.is_full()
        print(f"  full : size={rb.size()}")
        for i in range(16):
            o = rb.get()
            assert o.order_id == i
        assert rb.is_empty()
        print(f"  empty: size={rb.size()}")
        print("PASS ring-buffer self-test")
    finally:
        try:
            if rb is not None:
                rb.close()
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def cmd_firehose(args):
    import asyncio
    import os
    import tempfile

    from core.firehose import Firehose
    from core.metrics import EngineMetrics
    from core.ring_buffer import RingBuffer
    with tempfile.NamedTemporaryFile(delete=False, suffix=".rb") as f:
        tmp = f.name
    rb = None
    try:
        rb      = RingBuffer(tmp, create=True)
        metrics = EngineMetrics()
        fh      = Firehose(rb, metrics, target_ops=args.target_ops)
        print(f"Firehose: target={args.target_ops:,} ord/sec  duration={args.duration}s")
        asyncio.run(fh.run(duration=args.duration))
        s = metrics.snapshot()
        print(f"  Sent    : {s['orders_sent']:>10,}")
        print(f"  Dropped : {s['orders_dropped']:>10,}")
        print(f"  Rate    : {s['enqueue_rate']:>10,.0f} ord/sec")
    finally:
        try:
            if rb is not None:
                rb.close()
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def cmd_verify(args):
    from bench.engine_verify import main as verify_main
    verify_main()


def cmd_audit(args):
    from bench.ipc_audit import run_audit
    run_audit(n=args.orders)


def cmd_dashboard(args):
    from ui.dashboard import Dashboard
    Dashboard(duration=args.duration, target_ops=args.target_ops).run()

def cmd_web(args):
    from ui.web_server import run_server
    run_server(args.host, args.port, args.target_ops)


def build_parser():
    p   = argparse.ArgumentParser(prog="hft_engine",
              description="ChronosMatch simulated order-processing engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("struct-test", help="Binary struct layout smoke test")
    sub.add_parser("ring-test",   help="Ring buffer smoke test")
    sub.add_parser("verify",      help="Matching engine verification suite")

    fh = sub.add_parser("firehose", help="Async firehose standalone")
    fh.add_argument("--duration",   type=float, default=5.0)
    fh.add_argument("--target-ops", type=int,   default=100_000)

    au = sub.add_parser("audit", help="Transport and serialization benchmark")
    au.add_argument("--orders", type=int, default=1_000_000)

    ds = sub.add_parser("dashboard", help="Live ANSI terminal dashboard")
    ds.add_argument("--duration",   type=float, default=30.0)
    ds.add_argument("--target-ops", type=int,   default=100_000)

    web = sub.add_parser("web", help="Local live browser dashboard")
    web.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="Host to bind to (0.0.0.0 for cloud deployment, 127.0.0.1 for local)")
    web.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    web.add_argument("--target-ops", type=int, default=int(os.getenv("TARGET_OPS", "50000")))

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    {
        "struct-test": cmd_struct_test,
        "ring-test"  : cmd_ring_test,
        "firehose"   : cmd_firehose,
        "verify"     : cmd_verify,
        "audit"      : cmd_audit,
        "dashboard"  : cmd_dashboard,
        "web"        : cmd_web,
    }[args.cmd](args)
