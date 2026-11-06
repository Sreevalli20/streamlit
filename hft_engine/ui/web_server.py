"""Loopback HTTP server for the live ChronosMatch browser dashboard."""

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.constants import BOOK_LEVELS, DEFAULT_TARGET_OPS
from core.firehose import Firehose
from core.metrics import EngineMetrics
from core.paper_market import PaperTrading
from core.ring_buffer import RingBuffer
from engine.limit_order_book import LimitOrderBook

_LOG = logging.getLogger(__name__)
_WEB_PAGE = Path(__file__).with_name("web_dashboard.html")
_PRODUCER_DURATION = 10 * 365 * 24 * 60 * 60
_TAPE_LIMIT = 25
_MAX_REQUEST_BYTES = 4096


class WebDashboard:
    """Run the existing producer, ring buffer, and matching engine for HTTP UI."""

    def __init__(self, target_ops: int = DEFAULT_TARGET_OPS):
        self._metrics = EngineMetrics()
        self._lob = LimitOrderBook()
        self._paper = PaperTrading()
        self._book_lock = threading.Lock()
        self._control_lock = threading.Lock()
        self._tape = deque(maxlen=_TAPE_LIMIT)
        self._stop = threading.Event()
        self._error = None
        self._started = False

        with tempfile.NamedTemporaryFile(delete=False, suffix=".rb") as temp_file:
            self._buffer_path = temp_file.name

        try:
            self._rb = RingBuffer(self._buffer_path, create=True)
            self._fh = Firehose(self._rb, self._metrics, target_ops=target_ops)
        except BaseException:
            try:
                if hasattr(self, "_rb"):
                    self._rb.close()
            finally:
                os.unlink(self._buffer_path)
            raise

        self._producer = threading.Thread(
            target=self._produce, name="chronosmatch-web-producer"
        )
        self._consumer = threading.Thread(
            target=self._consume, name="chronosmatch-web-consumer"
        )

    def start(self) -> None:
        if self._started:
            raise RuntimeError("web dashboard has already been started")
        self._started = True
        try:
            self._consumer.start()
            self._producer.start()
        except BaseException:
            self.close()
            raise

    def _produce(self) -> None:
        try:
            asyncio.run(self._fh.run(duration=_PRODUCER_DURATION))
        except Exception as exc:  # noqa: BLE001 - expose worker failures to the UI.
            self._error = str(exc)
            _LOG.exception("Web dashboard producer failed")
            self._stop.set()
            self._fh.stop()

    def _consume(self) -> None:
        try:
            while not self._stop.is_set() or not self._rb.is_empty():
                with self._book_lock:
                    order = self._rb.get()
                    if order is not None:
                        started_ns = time.perf_counter_ns()
                        trades = self._lob.submit(order)
                        latency_ns = time.perf_counter_ns() - started_ns
                        self._metrics.matched(
                            trades=len(trades),
                            volume=sum(trade.quantity for trade in trades),
                            latency_ns=latency_ns,
                        )
                        now = datetime.now().astimezone().strftime(
                            "%H:%M:%S.%f"
                        )[:-3]
                        side = "BUY" if order.is_buy else "SELL"
                        for trade in trades:
                            self._tape.appendleft({
                                "time": now,
                                "side": side,
                                "price": trade.price,
                                "quantity": trade.quantity,
                            })
                if order is None:
                    self._stop.wait(0.0001)
        except Exception as exc:  # noqa: BLE001 - expose worker failures to the UI.
            self._error = str(exc)
            _LOG.exception("Web dashboard consumer failed")
            self._stop.set()
            self._fh.stop()

    def snapshot(self) -> dict:
        with self._book_lock:
            metrics = self._metrics.snapshot()
            levels = self._lob.top_of_book(BOOK_LEVELS)
            best_bid = self._lob.best_bid
            best_ask = self._lob.best_ask
            return {
                "status": (
                    "error" if self._error
                    else "paused" if self._fh.paused
                    else "running"
                ),
                "error": self._error,
                "target_ops": self._fh.target_ops,
                "buffer_size": self._rb.size(),
                "buffer_capacity": self._rb.capacity,
                "metrics": metrics,
                "latency_history": self._metrics.match_latency.recent(60),
                "book": {
                    "bids": levels["bids"],
                    "asks": levels["asks"],
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread": (
                        best_ask - best_bid
                        if best_bid is not None and best_ask is not None
                        else None
                    ),
                },
                "tape": list(self._tape),
            }

    def pause(self) -> None:
        with self._control_lock:
            self._fh.pause()

    def resume(self) -> None:
        with self._control_lock:
            self._fh.resume()

    def set_target(self, target_ops: int) -> None:
        if isinstance(target_ops, bool) or not isinstance(target_ops, int):
            raise TypeError("target_ops must be an integer")
        if not 1_000 <= target_ops <= 1_000_000:
            raise ValueError("target_ops must be between 1,000 and 1,000,000")
        with self._control_lock:
            self._fh.set_target_ops(target_ops)

    def reset(self) -> None:
        with self._control_lock:
            was_running = not self._fh.paused
            self._fh.pause()
            deadline = time.monotonic() + 5.0
            while not self._rb.is_empty():
                if time.monotonic() >= deadline:
                    if was_running:
                        self._fh.resume()
                    raise TimeoutError("timed out draining orders before reset")
                time.sleep(0.001)
            with self._book_lock:
                self._metrics.reset()
                self._tape.clear()
            if was_running:
                self._fh.resume()

    def close(self) -> None:
        self._stop.set()
        self._fh.stop()
        for thread in (self._producer, self._consumer):
            if thread.ident is not None:
                thread.join(timeout=10)
                if thread.is_alive():
                    raise TimeoutError(f"{thread.name} did not stop")
        try:
            self._rb.close()
        finally:
            try:
                os.unlink(self._buffer_path)
            except FileNotFoundError:
                pass


def create_server(dashboard: WebDashboard, host: str, port: int):
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("host must be a valid IP address") from exc
    # Allow loopback addresses and 0.0.0.0 for cloud deployment (e.g., Render.com)
    if not (address.is_loopback or str(address) == "0.0.0.0"):
        raise ValueError("web dashboard must bind to a loopback IP address or 0.0.0.0")

    class DashboardRequestHandler(BaseHTTPRequestHandler):
        server_version = "ChronosMatchLocal/1.0"

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
                _LOG.debug("Client disconnected while receiving a response: %s", exc)

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            if path in (
                "/", "/ui/web_dashboard.html", "/ui/workspace.js"
            ):
                try:
                    page_path = (
                        _WEB_PAGE.with_name("workspace.js")
                        if path == "/ui/workspace.js"
                        else _WEB_PAGE
                    )
                    page = page_path.read_bytes()
                except OSError:
                    _LOG.exception("Could not read the dashboard page")
                    self._send(
                        500, "text/plain; charset=utf-8",
                        b"Dashboard page could not be read.",
                    )
                    return
                content_type = (
                    "text/javascript; charset=utf-8"
                    if path == "/ui/workspace.js"
                    else "text/html; charset=utf-8"
                )
                self._send(200, content_type, page)
            elif path == "/api/state":
                self._json(200, dashboard.snapshot())
            elif path == "/api/market/state":
                self._json(200, dashboard._paper.market_state())
            elif path == "/api/market/instruments":
                query = parse_qs(parsed.query)
                self._json(200, dashboard._paper.instruments(
                    query.get("q", [""])[0],
                    query.get("exchange", [""])[0],
                    query.get("sector", [""])[0],
                ))
            elif path.startswith("/api/market/instrument/"):
                symbol = path.removeprefix("/api/market/instrument/")
                query = parse_qs(parsed.query)
                try:
                    self._json(200, dashboard._paper.instrument(
                        symbol, query.get("range", ["1D"])[0]
                    ))
                except KeyError as exc:
                    self._json(404, {"error": str(exc).strip("'")})
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
            elif path == "/api/paper/state":
                self._json(200, dashboard._paper.state())
            elif path == "/api/paper/preview":
                query = parse_qs(parsed.query)
                try:
                    self._json(200, dashboard._paper.preview_market_order(
                        query.get("symbol", [""])[0],
                        query.get("side", [""])[0],
                    ))
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
            elif path.startswith("/api/paper/book/"):
                symbol = path.removeprefix("/api/paper/book/")
                try:
                    self._json(200, dashboard._paper.book(symbol))
                except KeyError as exc:
                    self._json(404, {"error": str(exc).strip("'")})
            else:
                self._send(404, "text/plain; charset=utf-8", b"Not found.")

        def _json(self, status: int, payload) -> None:
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
            self._send(status, "application/json; charset=utf-8", body)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in (
                "/api/control",
                "/api/paper/order",
                "/api/paper/cancel",
                "/api/paper/reset",
            ):
                self._send(404, "text/plain; charset=utf-8", b"Not found.")
                return
            if self.headers.get_content_type() != "application/json":
                self._send(
                    415, "application/json; charset=utf-8",
                    b'{"error":"Content-Type must be application/json"}',
                )
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= _MAX_REQUEST_BYTES:
                    raise ValueError("request body size is invalid")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise TypeError("request body must be a JSON object")
                if path == "/api/paper/order":
                    result = dashboard._paper.submit_order(
                        payload.get("symbol"),
                        payload.get("side"),
                        payload.get("type"),
                        payload.get("quantity"),
                        payload.get("price"),
                    )
                elif path == "/api/paper/cancel":
                    result = dashboard._paper.cancel_order(
                        payload.get("order_id")
                    )
                elif path == "/api/paper/reset":
                    result = dashboard._paper.reset()
                else:
                    action = payload.get("action")
                    if action == "pause":
                        dashboard.pause()
                    elif action == "resume":
                        dashboard.resume()
                    elif action == "reset":
                        dashboard.reset()
                    elif action == "target":
                        dashboard.set_target(payload.get("target_ops"))
                    else:
                        raise ValueError("unknown dashboard action")
                    result = dashboard.snapshot()
                self._json(200, result)
            except json.JSONDecodeError:
                self._json(400, {"error": "Request body must be valid JSON"})
            except KeyError as exc:
                self._json(404, {"error": str(exc).strip("'")})
            except (TypeError, ValueError, TimeoutError) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, format_string, *args):
            _LOG.info(
                "%s - %s",
                self.address_string(),
                format_string % args,
            )

    server = ThreadingHTTPServer((str(address), port), DashboardRequestHandler)
    return server


def run_server(host: str, port: int, target_ops: int) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    if isinstance(target_ops, bool) or not isinstance(target_ops, int):
        raise TypeError("target_ops must be an integer")
    if not 1_000 <= target_ops <= 1_000_000:
        raise ValueError("target_ops must be between 1,000 and 1,000,000")
    dashboard = WebDashboard(target_ops=target_ops)
    server = None
    try:
        server = create_server(dashboard, host, port)
        dashboard.start()
        print(f"ChronosMatch web dashboard: http://{host}:{server.server_port}/")
        print("Press Ctrl+C to stop the server.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ChronosMatch web dashboard...")
    finally:
        if server is not None:
            server.server_close()
        dashboard.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local web dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--target-ops", type=int, default=DEFAULT_TARGET_OPS)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not 1_000 <= args.target_ops <= 1_000_000:
        parser.error("--target-ops must be between 1,000 and 1,000,000")
    run_server(args.host, args.port, args.target_ops)


if __name__ == "__main__":
    main()
