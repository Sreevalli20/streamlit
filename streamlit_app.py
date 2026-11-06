"""
Streamlit version of ChronosMatch Trading Engine
Deploy to Streamlit Cloud: https://share.streamlit.io/deploy
"""

import os
import sys
import time
import threading
import asyncio
import tempfile
from datetime import datetime
from collections import deque

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from core.constants import BOOK_LEVELS, DEFAULT_TARGET_OPS
from core.firehose import Firehose
from core.metrics import EngineMetrics
from core.paper_market import PaperTrading
from core.ring_buffer import RingBuffer
from engine.limit_order_book import LimitOrderBook

# Constants
_TAPE_LIMIT = 25
_PRODUCER_DURATION = 10 * 365 * 24 * 60 * 60  # Run indefinitely until stopped


class StreamlitDashboard:
    """Streamlit-compatible version of the WebDashboard."""

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
            target=self._produce, name="chronosmatch-streamlit-producer", daemon=True
        )
        self._consumer = threading.Thread(
            target=self._consume, name="chronosmatch-streamlit-consumer", daemon=True
        )

    def start(self) -> None:
        if self._started:
            raise RuntimeError("dashboard has already been started")
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
        except Exception as exc:
            self._error = str(exc)
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
                        now = datetime.now().astimezone().strftime("%H:%M:%S.%f")[:-3]
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
        except Exception as exc:
            self._error = str(exc)
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
        try:
            self._rb.close()
        finally:
            try:
                os.unlink(self._buffer_path)
            except FileNotFoundError:
                pass


def render_kpi_card(title: str, value: str, subtitle: str = "", color: str = "cyan"):
    """Render a KPI card with title, value, and subtitle."""
    colors = {
        "cyan": "#00f2fe",
        "green": "#00e676",
        "purple": "#7928ca",
        "yellow": "#ffb703",
        "red": "#ff3366",
    }
    bg_color = colors.get(color, "#00f2fe")

    st.markdown(f"""
    <div style="
        background: #0e131f;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 18px 20px;
        border-top: 2px solid {bg_color};
    ">
        <div style="font-size: 0.75rem; color: #64748b; font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">
            {title}
        </div>
        <div style="font-family: 'JetBrains Mono', monospace; font-size: 1.85rem; font-weight: 700; color: #f8fafc; letter-spacing: -0.03em; margin-bottom: 6px;">
            {value}
        </div>
        <div style="font-size: 0.75rem; color: #94a3b8; font-family: 'JetBrains Mono', monospace;">
            {subtitle}
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_order_book(state: dict):
    """Render the limit order book table."""
    st.subheader("Limit Order Book Depth (L1-L8 Price-Time Priority)")

    bids = state["book"]["bids"]
    asks = state["book"]["asks"]
    best_bid = state["book"]["best_bid"]
    best_ask = state["book"]["best_ask"]
    spread = state["book"]["spread"]

    # Summary stats
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Best Bid", f"{best_bid:.4f}" if best_bid else "—")
    with col2:
        st.metric("Spread", f"{spread:.4f}" if spread else "—")
    with col3:
        st.metric("Best Ask", f"{best_ask:.4f}" if best_ask else "—")

    # Order book table
    max_len = max(len(bids), len(asks))
    data = []
    for i in range(max_len):
        bid_px = bids[i][0] if i < len(bids) else 0
        bid_qty = bids[i][1] if i < len(bids) else 0
        ask_px = asks[i][0] if i < len(asks) else 0
        ask_qty = asks[i][1] if i < len(asks) else 0
        data.append({
            "Level": f"L{i+1}",
            "Bid Qty": bid_qty,
            "Bid Px": f"{bid_px:.4f}",
            "Ask Px": f"{ask_px:.4f}",
            "Ask Qty": ask_qty,
        })

    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_latency_chart(latency_history: list):
    """Render latency sparkline chart."""
    st.subheader("Measured Matching Latency")

    if not latency_history:
        st.info("No latency data available yet")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        y=latency_history,
        mode='lines+markers',
        line=dict(color='#00f2fe', width=2),
        marker=dict(size=4),
        fill='tozeroy',
        fillcolor='rgba(0, 242, 254, 0.1)',
    ))

    fig.add_hline(y=50, line_dash="dash", line_color="rgba(255, 51, 102, 0.5)",
                  annotation_text="50 µs reference (not an SLA)")

    fig.update_layout(
        xaxis_title="Sample",
        yaxis_title="Latency (µs)",
        hovermode="x unified",
        height=300,
        margin=dict(l=0, r=0, t=0, b=0),
        plot_bgcolor='#0e131f',
        paper_bgcolor='#0e131f',
        font=dict(color='#f8fafc'),
    )

    st.plotly_chart(fig, use_container_width=True)


def render_trade_tape(tape: list):
    """Render recent trade tape."""
    st.subheader("Recent Executions")

    if not tape:
        st.info("No trades yet")
        return

    data = []
    for trade in tape:
        color = "green" if trade["side"] == "BUY" else "red"
        data.append({
            "Time": trade["time"],
            "Side": trade["side"],
            "Quantity": f"{trade['quantity']:,} shs",
            "Price": f"{trade['price']:.4f}",
        })

    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True, hide_index=True)


def main():
    """Main Streamlit application."""
    st.set_page_config(
        page_title="ChronosMatch - HFT Trading Engine",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Custom CSS
    st.markdown("""
    <style>
    .stApp {
        background-color: #07090e;
    }
    </style>
    """, unsafe_allow_html=True)

    # Initialize dashboard in session state
    if "dashboard" not in st.session_state:
        st.session_state.dashboard = StreamlitDashboard(target_ops=DEFAULT_TARGET_OPS)
        st.session_state.dashboard.start()

    dashboard = st.session_state.dashboard

    # Header
    st.markdown("""
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;">
        <div style="display: flex; align-items: center; gap: 16px;">
            <div style="width: 36px; height: 36px; background: linear-gradient(135deg, #00f2fe, #4facfe); border-radius: 8px; display: flex; align-items: center; justify-content: center;">
                <span style="font-size: 20px;">📊</span>
            </div>
            <div>
                <h1 style="margin: 0; font-size: 1.5rem; font-weight: 700;">ChronosMatch</h1>
                <span style="font-size: 0.72rem; background: rgba(0, 242, 254, 0.08); border: 1px solid rgba(0, 242, 254, 0.25); color: #00f2fe; padding: 3px 8px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.05em;">
                    SIMULATION ONLY
                </span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Get current state
    state = dashboard.snapshot()

    # Sidebar controls
    with st.sidebar:
        st.header("Controls")

        # Status
        status_color = "🟢" if state["status"] == "running" else "🟡" if state["status"] == "paused" else "🔴"
        st.metric("Status", f"{status_color} {state['status'].upper()}")

        # Buffer status
        st.metric("Ring Buffer", f"{state['buffer_size']:,} / {state['buffer_capacity']:,}")

        st.divider()

        # Target TPS slider
        target_ops = st.slider(
            "Target Orders/sec",
            min_value=10000,
            max_value=150000,
            value=state["target_ops"],
            step=5000,
            format="%d"
        )
        if target_ops != state["target_ops"]:
            dashboard.set_target(target_ops)

        st.divider()

        # Control buttons
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⏸ Pause", use_container_width=True, disabled=state["status"] != "running"):
                dashboard.pause()
        with col2:
            if st.button("▶ Resume", use_container_width=True, disabled=state["status"] != "paused"):
                dashboard.resume()

        if st.button("🔄 Reset Stats", use_container_width=True):
            dashboard.reset()

        st.divider()

        st.info("""
        **Note:** This is a simulated high-frequency trading engine for educational purposes only.
        It does not connect to real exchanges or brokers.
        """)

    # Error display
    if state["error"]:
        st.error(f"Engine error: {state['error']}")
        return

    # KPI Cards
    metrics = state["metrics"]
    latency = metrics["latency"]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_kpi_card(
            "Target Rate",
            f"{state['target_ops']:,}",
            "ord/s target",
            "cyan"
        )
    with col2:
        render_kpi_card(
            "Orders Sent",
            f"{metrics['orders_sent']:,}",
            f"{metrics['dropped']:,} dropped",
            "green"
        )
    with col3:
        render_kpi_card(
            "Actual Rate",
            f"{metrics['enqueue_rate']:,.0f}",
            "ord/sec",
            "purple"
        )
    with col4:
        render_kpi_card(
            "Avg Latency",
            f"{latency['avg']:.2f} µs",
            f"P99: {latency['p99']:.2f} µs",
            "yellow"
        )

    # Second row of KPIs
    col1, col2, col3 = st.columns(3)
    with col1:
        render_kpi_card(
            "Trades Fired",
            f"{metrics['trades_fired']:,}",
            "executions",
            "green"
        )
    with col2:
        render_kpi_card(
            "Volume Traded",
            f"{metrics['volume_traded']:,}",
            "shares",
            "purple"
        )
    with col3:
        render_kpi_card(
            "Fill Rate",
            f"{metrics['orders_matched']:,}",
            "orders matched",
            "cyan"
        )

    st.divider()

    # Main content area
    col1, col2 = st.columns([3, 2])

    with col1:
        render_order_book(state)

    with col2:
        render_latency_chart(state["latency_history"])
        st.divider()
        render_trade_tape(state["tape"])

    # Auto-refresh
    time.sleep(0.5)
    st.rerun()


if __name__ == "__main__":
    main()
