import asyncio
import json
import threading
import urllib.request

import pytest
import ui.dashboard as dashboard_module
from core.constants import SIDE_BUY, SIDE_SELL
from core.firehose import Firehose
from core.metrics import EngineMetrics, LatencyHistogram
from core.ring_buffer import RingBuffer
from engine.limit_order_book import LimitOrderBook
from ui.dashboard import Dashboard
from ui.web_server import WebDashboard, create_server


def test_firehose_buffer_round_trip_and_clean_shutdown(tmp_path):
    path = tmp_path / "pipeline.rb"
    metrics = EngineMetrics()
    with RingBuffer(path, capacity=256) as rb:
        producer = Firehose(rb, metrics, target_ops=6400)
        asyncio.run(producer.run(duration=0.025))
        sent = metrics.snapshot()["orders_sent"]
        book = LimitOrderBook()
        orders = []
        trades = []
        while not rb.is_empty():
            order = rb.get()
            orders.append(order)
            trades.extend(book.submit(order))

    assert sent == len(orders)
    assert sent > 0
    assert [order.order_id for order in orders] == list(range(sent))
    assert all(order.side in (SIDE_BUY, SIDE_SELL) for order in orders)
    assert all(order.quantity > 0 and order.price > 0 for order in orders)
    assert book.total_orders == sent
    assert book.total_trades == len(trades)


def test_firehose_rejects_invalid_parameters():
    metrics = EngineMetrics()
    for target, error in [(0, ValueError), (-1, ValueError), (True, TypeError)]:
        with pytest.raises(error):
            Firehose(None, metrics, target_ops=target)


def test_firehose_stop_ends_a_long_run_promptly(tmp_path):
    async def run_until_stopped(producer):
        task = asyncio.create_task(producer.run(duration=30))
        await asyncio.sleep(0.01)
        producer.stop()
        await asyncio.wait_for(task, timeout=1)

    with RingBuffer(tmp_path / "stoppable.rb", capacity=256) as rb:
        producer = Firehose(rb, EngineMetrics(), target_ops=6400)
        asyncio.run(run_until_stopped(producer))


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_firehose_rejects_invalid_duration(duration):
    with pytest.raises(ValueError):
        asyncio.run(Firehose(None, EngineMetrics()).run(duration=duration))


def test_empty_metrics_are_safe():
    metrics = EngineMetrics()
    snapshot = metrics.snapshot()
    assert snapshot["enqueue_rate"] == 0
    assert snapshot["dequeue_rate"] == 0
    assert snapshot["latency"]["avg"] == 0
    assert snapshot["latency"]["p99"] == 0


def test_latency_histogram_keeps_only_the_configured_rolling_window():
    histogram = LatencyHistogram(window=2)
    histogram.record_ns(1_000)
    histogram.record_ns(2_000)
    histogram.record_ns(3_000)

    snapshot = histogram.snapshot()

    assert snapshot["avg"] == 2.5
    assert snapshot["p50"] == 2.0
    assert snapshot["p99"] == 3.0
    assert snapshot["count"] == 3


def test_short_dashboard_duration_has_nonnegative_elapsed_display(capsys):
    Dashboard(duration=0.1, target_ops=1000).run()

    output = capsys.readouterr().out
    assert "Elapsed: -0.0s" not in output
    assert "/ 0.1s" in output
    assert "Dashboard stopped." in output


def test_dashboard_constructor_removes_temp_file_when_buffer_setup_fails(
    monkeypatch, tmp_path
):
    original_named_temp_file = dashboard_module.tempfile.NamedTemporaryFile

    def create_temp_file(*args, **kwargs):
        return original_named_temp_file(*args, dir=tmp_path, **kwargs)

    def fail_buffer_setup(*args, **kwargs):
        raise OSError("buffer setup failed")

    monkeypatch.setattr(
        dashboard_module.tempfile, "NamedTemporaryFile", create_temp_file
    )
    monkeypatch.setattr(dashboard_module, "RingBuffer", fail_buffer_setup)

    with pytest.raises(OSError, match="buffer setup failed"):
        Dashboard()

    assert list(tmp_path.iterdir()) == []


def test_web_dashboard_serves_live_state_and_controls_engine():
    dashboard = WebDashboard(target_ops=10_000)
    with pytest.raises(ValueError, match="loopback"):
        create_server(dashboard, "0.0.0.0", 0)
    server = create_server(dashboard, "127.0.0.1", 0)
    server_thread = threading.Thread(target=server.serve_forever)
    try:
        server_thread.start()
        dashboard.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        with urllib.request.urlopen(base_url, timeout=2) as response:
            page = response.read().decode("utf-8")
        assert "Live local simulation" in page
        assert "Visual prototype only" not in page
        assert '<script src="/ui/workspace.js"></script>' in page

        with urllib.request.urlopen(
            f"{base_url}/ui/workspace.js", timeout=2
        ) as response:
            assert response.headers.get_content_type() == "text/javascript"
            assert "Paper trading" in response.read().decode("utf-8")

        with urllib.request.urlopen(f"{base_url}/api/state", timeout=2) as response:
            state = json.loads(response.read())
        assert state["status"] == "running"
        assert state["buffer_capacity"] > 0
        assert "latency_history" in state

        request = urllib.request.Request(
            f"{base_url}/api/control",
            data=json.dumps({"action": "pause"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert json.loads(response.read())["status"] == "paused"

        request = urllib.request.Request(
            f"{base_url}/api/control",
            data=json.dumps({"action": "target", "target_ops": 20_000}).encode(
                "utf-8"
            ),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert json.loads(response.read())["target_ops"] == 20_000

        request = urllib.request.Request(
            f"{base_url}/api/control",
            data=json.dumps({"action": "reset"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            state = json.loads(response.read())
        assert state["status"] == "paused"
        assert state["metrics"]["orders_sent"] == 0
    finally:
        server.shutdown()
        server.server_close()
        if server_thread.ident is not None:
            server_thread.join(timeout=2)
        dashboard.close()


def test_web_response_logs_expected_client_disconnect(caplog):
    dashboard = WebDashboard(target_ops=10_000)
    server = create_server(dashboard, "127.0.0.1", 0)
    handler = object.__new__(server.RequestHandlerClass)
    handler.send_response = lambda status: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None

    class DisconnectedWriter:
        def write(self, body):
            raise ConnectionAbortedError("client closed the connection")

    handler.wfile = DisconnectedWriter()
    try:
        with caplog.at_level("DEBUG", logger="ui.web_server"):
            handler._send(200, "text/plain", b"response")
        assert "Client disconnected while receiving a response" in caplog.text
    finally:
        server.server_close()
        dashboard.close()


def test_web_api_exposes_simulated_stocks_and_paper_order_flow():
    dashboard = WebDashboard(target_ops=10_000)
    server = create_server(dashboard, "127.0.0.1", 0)
    server_thread = threading.Thread(target=server.serve_forever)
    try:
        server_thread.start()
        dashboard.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        with urllib.request.urlopen(
            f"{base_url}/api/market/instruments?q=Reliance", timeout=2
        ) as response:
            results = json.loads(response.read())
        assert len(results) == 1
        assert results[0]["symbol"] == "RELIANCE"
        assert "not exchange" in results[0]["data_source"]

        with urllib.request.urlopen(
            f"{base_url}/api/market/instrument/TCS?range=1Y", timeout=2
        ) as response:
            details = json.loads(response.read())
        assert details["quote"]["symbol"] == "TCS"
        assert details["history"]
        assert "not historical exchange data" in details["history_note"]

        with urllib.request.urlopen(
            f"{base_url}/api/paper/preview?symbol=TCS&side=BUY", timeout=2
        ) as response:
            preview = json.loads(response.read())
        assert preview["symbol"] == "TCS"
        assert preview["side"] == "BUY"
        assert preview["estimated_price"] > 0
        assert preview["max_quantity"] > 0

        order_request = urllib.request.Request(
            f"{base_url}/api/paper/order",
            data=json.dumps({
                "symbol": "TCS", "side": "BUY", "type": "MARKET",
                "quantity": 5, "price": None,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(order_request, timeout=2) as response:
            order = json.loads(response.read())
        assert order["status"] == "filled"

        with urllib.request.urlopen(
            f"{base_url}/api/paper/state", timeout=2
        ) as response:
            state = json.loads(response.read())
        assert state["holdings"][0]["symbol"] == "TCS"
        assert state["holdings"][0]["quantity"] == 5
        assert "no real orders" in state["mode"]
    finally:
        server.shutdown()
        server.server_close()
        if server_thread.ident is not None:
            server_thread.join(timeout=2)
        dashboard.close()


@pytest.mark.parametrize("sample", [-1, float("inf"), float("nan")])
def test_latency_metrics_reject_invalid_samples(sample):
    metrics = EngineMetrics()
    with pytest.raises(ValueError):
        metrics.match_latency.record_ns(sample)
