"""Clearly labelled local market simulation and paper-trading account."""

import math
import random
import threading
import time
from collections import deque
from datetime import datetime, timezone

from engine.limit_order_book import LimitOrderBook

from core.constants import SIDE_BUY, SIDE_SELL
from core.order_struct import Order

DEMO_SOURCE = "Simulated demo data — not exchange prices"

_INSTRUMENTS = (
    ("RELIANCE", "Reliance Industries", "Energy", 2840.0),
    ("TCS", "Tata Consultancy Services", "Information Technology", 3920.0),
    ("INFY", "Infosys", "Information Technology", 1580.0),
    ("HDFCBANK", "HDFC Bank", "Banking", 1670.0),
    ("ICICIBANK", "ICICI Bank", "Banking", 1120.0),
    ("SBIN", "State Bank of India", "Banking", 760.0),
    ("ITC", "ITC", "Consumer Goods", 445.0),
    ("LT", "Larsen & Toubro", "Engineering", 3580.0),
    ("BHARTIARTL", "Bharti Airtel", "Telecommunications", 1760.0),
    ("TATAMOTORS", "Tata Motors", "Automotive", 920.0),
)

_STARTING_CASH = 1_000_000.0
_MAX_ORDER_QUANTITY = 1_000_000
_MAKER_ID_START = 0xFFFFFFFE00000000


class PaperTrading:
    """Thread-safe multi-instrument simulation backed by the existing LOB."""

    def __init__(self):
        self._lock = threading.RLock()
        self._rng = {
            symbol: random.Random(symbol) for symbol, *_ in _INSTRUMENTS
        }
        self._quotes = {}
        self._books = {}
        self._orders = {}
        self._executions = deque(maxlen=500)
        self._holdings = {}
        self._cash = _STARTING_CASH
        self._realized_pnl = 0.0
        self._next_order_id = 1
        self._next_maker_id = _MAKER_ID_START
        now = time.monotonic()
        timestamp = self._timestamp()
        for symbol, name, sector, baseline in _INSTRUMENTS:
            self._quotes[symbol] = {
                "symbol": symbol,
                "name": name,
                "exchange": "NSE (simulated)",
                "sector": sector,
                "currency": "INR",
                "price": baseline,
                "previous_close": baseline,
                "open": baseline,
                "high": baseline,
                "low": baseline,
                "volume": 0,
                "change": 0.0,
                "change_percent": 0.0,
                "market_status": "Demo session",
                "data_source": DEMO_SOURCE,
                "updated_at": timestamp,
                "_baseline": baseline,
                "_last_tick": now,
                "_history": deque(
                    [{"time": timestamp, "price": baseline, "volume": 0}],
                    maxlen=720,
                ),
            }
            book = LimitOrderBook()
            self._books[symbol] = book
            self._seed_book(book, baseline)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _seed_book(self, book: LimitOrderBook, mid: float) -> None:
        tick_size = max(round(mid * 0.0002, 2), 0.01)
        for level in range(1, 9):
            quantity = level * 100
            book.submit(Order(
                self._next_maker_id, mid - level * tick_size,
                quantity, SIDE_BUY,
            ))
            book.submit(Order(
                self._next_maker_id + 1, mid + level * tick_size,
                quantity, SIDE_SELL,
            ))
            self._next_maker_id += 2

    def _advance(self, quote: dict, symbol: str) -> None:
        now = time.monotonic()
        if now - quote["_last_tick"] < 1:
            return
        quote["_last_tick"] = now
        previous = quote["price"]
        next_price = max(
            0.01,
            previous * (1 + self._rng[symbol].uniform(-0.0007, 0.0007)),
        )
        quote["price"] = round(next_price, 2)
        quote["high"] = max(quote["high"], quote["price"])
        quote["low"] = min(quote["low"], quote["price"])
        quote["volume"] += self._rng[symbol].randint(100, 10_000)
        quote["change"] = round(quote["price"] - quote["previous_close"], 2)
        quote["change_percent"] = (
            quote["change"] / quote["previous_close"] * 100
        )
        quote["updated_at"] = self._timestamp()
        quote["_history"].append({
            "time": quote["updated_at"],
            "price": quote["price"],
            "volume": quote["volume"],
        })

    def instruments(self, query: str = "", exchange: str = "", sector: str = "") -> list[dict]:
        query = query.strip().casefold()
        exchange = exchange.strip().casefold()
        sector = sector.strip().casefold()
        with self._lock:
            result = []
            for quote in self._quotes.values():
                self._advance(quote, quote["symbol"])
                if query and query not in quote["symbol"].casefold() and query not in quote["name"].casefold():
                    continue
                if exchange and exchange not in quote["exchange"].casefold():
                    continue
                if sector and sector != quote["sector"].casefold():
                    continue
                result.append(self._public_quote(quote))
            return result

    @staticmethod
    def _public_quote(quote: dict) -> dict:
        return {key: value for key, value in quote.items() if not key.startswith("_")}

    def instrument(self, symbol: str, interval: str = "1D") -> dict:
        with self._lock:
            quote = self._quotes.get(symbol.upper())
            if quote is None:
                raise KeyError("Unknown stock symbol")
            self._advance(quote, symbol.upper())
            ranges = {
                "1D": 24 * 60 * 60,
                "1W": 7 * 24 * 60 * 60,
                "1M": 30 * 24 * 60 * 60,
                "3M": 90 * 24 * 60 * 60,
                "6M": 180 * 24 * 60 * 60,
                "1Y": 365 * 24 * 60 * 60,
            }
            if interval not in ranges:
                raise ValueError("Unsupported history range")
            cutoff = time.time() - ranges[interval]
            history = [
                dict(point) for point in quote["_history"]
                if datetime.fromisoformat(point["time"]).timestamp() >= cutoff
            ]
            return {
                "quote": self._public_quote(quote),
                "history": history,
                "history_note": (
                    "This chart contains only this server session's simulated "
                    "observations and is not historical exchange data."
                ),
                "book": self._book_snapshot(symbol.upper()),
            }

    def _book_snapshot(self, symbol: str) -> dict:
        book = self._books[symbol]
        return book.top_of_book(8) | {
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "spread": (
                book.best_ask - book.best_bid
                if book.best_bid is not None and book.best_ask is not None
                else None
            ),
        }

    def market_state(self) -> dict:
        quotes = self.instruments()
        gainers = sorted(quotes, key=lambda q: q["change_percent"], reverse=True)
        losers = list(reversed(gainers))
        active = sorted(quotes, key=lambda q: q["volume"], reverse=True)
        return {
            "data_source": DEMO_SOURCE,
            "updated_at": self._timestamp(),
            "market_status": "Simulated session",
            "currency": "INR",
            "indices": [],
            "gainers": gainers[:5],
            "losers": losers[:5],
            "most_active": active[:5],
            "instruments": quotes,
        }

    def _reserved_cash(self) -> float:
        return sum(
            order["remaining_quantity"] * order["price"]
            for order in self._orders.values()
            if order["side"] == "BUY"
            and order["status"] in ("open", "partially_filled")
        )

    def _reserved_shares(self, symbol: str) -> int:
        return sum(
            order["remaining_quantity"]
            for order in self._orders.values()
            if order["symbol"] == symbol
            and order["side"] == "SELL"
            and order["status"] in ("open", "partially_filled")
        )

    @staticmethod
    def _market_order_price(book: LimitOrderBook, side: str) -> float:
        levels = book.top_of_book(100_000)
        prices = levels["asks"] if side == "BUY" else levels["bids"]
        if not prices:
            raise ValueError("No simulated liquidity is available")
        return prices[-1][0]

    def preview_market_order(self, symbol: str, side: str) -> dict:
        symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
        side = side.upper() if isinstance(side, str) else ""
        if symbol not in self._books:
            raise ValueError("Choose a supported stock")
        if side not in ("BUY", "SELL"):
            raise ValueError("Side must be BUY or SELL")

        with self._lock:
            price = self._market_order_price(self._books[symbol], side)
            if side == "BUY":
                available_cash = self._cash - self._reserved_cash()
                max_quantity = min(
                    _MAX_ORDER_QUANTITY, math.floor(available_cash / price)
                )
                available_shares = None
            else:
                available_cash = None
                available_shares = (
                    self._holdings.get(symbol, {}).get("quantity", 0)
                    - self._reserved_shares(symbol)
                )
                max_quantity = min(_MAX_ORDER_QUANTITY, available_shares)
            return {
                "symbol": symbol,
                "side": side,
                "estimated_price": price,
                "max_quantity": max_quantity,
                "available_cash": available_cash,
                "available_shares": available_shares,
                "note": "Estimate uses the current outer book price; actual fills can vary.",
            }

    def submit_order(
        self, symbol: str, side: str, order_type: str, quantity: int, price=None
    ) -> dict:
        symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
        side = side.upper() if isinstance(side, str) else ""
        order_type = order_type.upper() if isinstance(order_type, str) else ""
        if symbol not in self._books:
            raise ValueError("Choose a supported stock")
        if side not in ("BUY", "SELL"):
            raise ValueError("Side must be BUY or SELL")
        if order_type not in ("LIMIT", "MARKET"):
            raise ValueError("Order type must be LIMIT or MARKET")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise TypeError("Quantity must be a whole number")
        if not 1 <= quantity <= _MAX_ORDER_QUANTITY:
            raise ValueError(f"Quantity must be between 1 and {_MAX_ORDER_QUANTITY:,}")

        with self._lock:
            book = self._books[symbol]
            quote = self._quotes[symbol]
            self._advance(quote, symbol)
            if order_type == "LIMIT":
                if isinstance(price, bool) or not isinstance(price, (int, float)):
                    raise TypeError("A valid limit price is required")
                try:
                    price = float(price)
                except OverflowError as exc:
                    raise ValueError("Limit price is outside the supported range") from exc
                if not math.isfinite(price) or price <= 0:
                    raise ValueError("Limit price must be finite and positive")
            else:
                price = self._market_order_price(book, side)

            if side == "BUY" and quantity * price > self._cash - self._reserved_cash():
                raise ValueError("Insufficient paper-trading buying power")
            if side == "SELL":
                held = self._holdings.get(symbol, {}).get("quantity", 0)
                if quantity > held - self._reserved_shares(symbol):
                    raise ValueError("Not enough available shares to sell")

            crosses_own_order = any(
                order["symbol"] == symbol
                and order["status"] in ("open", "partially_filled")
                and order["remaining_quantity"] > 0
                and order["side"] != side
                and (
                    (side == "BUY" and price >= order["price"])
                    or (side == "SELL" and price <= order["price"])
                )
                for order in self._orders.values()
            )
            if crosses_own_order:
                raise ValueError("Order would match your own resting paper order")

            order_id = self._next_order_id
            self._next_order_id += 1
            order_record = {
                "order_id": order_id,
                "symbol": symbol,
                "name": quote["name"],
                "side": side,
                "type": order_type,
                "quantity": quantity,
                "filled_quantity": 0,
                "remaining_quantity": quantity,
                "price": price,
                "average_fill_price": 0.0,
                "status": "open",
                "reason": None,
                "created_at": self._timestamp(),
            }
            self._orders[order_id] = order_record
            order = Order(
                order_id, price, quantity,
                SIDE_BUY if side == "BUY" else SIDE_SELL,
            )
            trades = book.submit(order)
            self._apply_trades(order_record, trades)
            if order_type == "MARKET" and order_record["remaining_quantity"]:
                book.cancel(order_id)
                order_record["remaining_quantity"] = 0
                order_record["status"] = (
                    "partially_filled"
                    if order_record["filled_quantity"]
                    else "rejected"
                )
                if not order_record["filled_quantity"]:
                    order_record["reason"] = "No executable simulated liquidity"
            return dict(order_record)

    def _apply_trades(self, incoming: dict, trades: list) -> None:
        for trade in trades:
            resting = self._orders.get(trade.resting_id)
            resting_side = resting["side"] if resting else (
                "SELL" if incoming["side"] == "BUY" else "BUY"
            )
            if resting:
                self._apply_fill(resting, trade.price, trade.quantity)
            self._apply_fill(incoming, trade.price, trade.quantity)
            self._executions.appendleft({
                "time": self._timestamp(),
                "order_id": incoming["order_id"],
                "symbol": incoming["symbol"],
                "side": incoming["side"],
                "price": trade.price,
                "quantity": trade.quantity,
                "value": round(trade.price * trade.quantity, 2),
                "counterparty_side": resting_side,
            })

    def _apply_fill(self, order: dict, price: float, quantity: int) -> None:
        old_filled = order["filled_quantity"]
        order["filled_quantity"] += quantity
        order["remaining_quantity"] -= quantity
        order["average_fill_price"] = (
            (order["average_fill_price"] * old_filled + price * quantity)
            / order["filled_quantity"]
        )
        if order["side"] == "BUY":
            holding = self._holdings.setdefault(order["symbol"], {
                "quantity": 0, "average_price": 0.0
            })
            total_cost = holding["quantity"] * holding["average_price"] + price * quantity
            holding["quantity"] += quantity
            holding["average_price"] = total_cost / holding["quantity"]
            self._cash -= price * quantity
        else:
            holding = self._holdings[order["symbol"]]
            self._cash += price * quantity
            self._realized_pnl += (price - holding["average_price"]) * quantity
            holding["quantity"] -= quantity
            if holding["quantity"] == 0:
                del self._holdings[order["symbol"]]
        order["status"] = (
            "filled" if order["remaining_quantity"] == 0 else "partially_filled"
        )

    def cancel_order(self, order_id: int) -> dict:
        if isinstance(order_id, bool) or not isinstance(order_id, int):
            raise TypeError("Order ID must be an integer")
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise KeyError("Order not found")
            if order["status"] not in ("open", "partially_filled") or not order["remaining_quantity"]:
                raise ValueError("Order is no longer open")
            cancelled = self._books[order["symbol"]].cancel(order_id)
            if cancelled is None:
                raise RuntimeError("Open order could not be found in its book")
            order["remaining_quantity"] = 0
            order["status"] = "cancelled"
            return dict(order)

    def state(self) -> dict:
        with self._lock:
            holdings = []
            portfolio_value = self._cash
            invested = 0.0
            for symbol, holding in self._holdings.items():
                quote = self._quotes[symbol]
                self._advance(quote, symbol)
                value = holding["quantity"] * quote["price"]
                cost = holding["quantity"] * holding["average_price"]
                reserved = self._reserved_shares(symbol)
                invested += cost
                portfolio_value += value
                holdings.append({
                    "symbol": symbol,
                    "name": quote["name"],
                    "quantity": holding["quantity"],
                    "reserved_quantity": reserved,
                    "available_quantity": holding["quantity"] - reserved,
                    "average_price": holding["average_price"],
                    "market_price": quote["price"],
                    "market_value": value,
                    "unrealized_pnl": value - cost,
                })
            return {
                "mode": "PAPER SIMULATION — no real orders",
                "currency": "INR",
                "cash": self._cash,
                "reserved_cash": self._reserved_cash(),
                "buying_power": self._cash - self._reserved_cash(),
                "portfolio_value": portfolio_value,
                "invested_cost": invested,
                "realized_pnl": self._realized_pnl,
                "unrealized_pnl": sum(h["unrealized_pnl"] for h in holdings),
                "total_pnl": self._realized_pnl + sum(
                    h["unrealized_pnl"] for h in holdings
                ),
                "holdings": holdings,
                "orders": [dict(order) for order in reversed(tuple(self._orders.values()))],
                "executions": list(self._executions),
                "order_count": len(self._orders),
            }

    def book(self, symbol: str) -> dict:
        symbol = symbol.upper()
        with self._lock:
            if symbol not in self._books:
                raise KeyError("Unknown stock symbol")
            return self._book_snapshot(symbol)

    def reset(self) -> dict:
        with self._lock:
            self._cash = _STARTING_CASH
            self._realized_pnl = 0.0
            self._holdings.clear()
            self._orders.clear()
            self._executions.clear()
            self._next_order_id = 1
            for quote in self._quotes.values():
                self._books[quote["symbol"]] = LimitOrderBook()
                self._seed_book(
                    self._books[quote["symbol"]], quote["price"]
                )
            return self.state()
