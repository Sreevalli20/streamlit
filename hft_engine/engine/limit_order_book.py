"""
engine/limit_order_book.py  -  Phase 2: Limit Order Book
=========================================================
Price-Time Priority matching engine (pure Python with sortedcontainers).
The companion Cython version is in cython_src/lob.pyx.

Matching rules
--------------
  BUY  order: match against cheapest resting ask  where ask.price <= buy.price
  SELL order: match against highest resting bid   where bid.price >= sell.price
  Partial fills supported; residual rests in the book.
  Full fills remove the resting order.
"""

import time
from dataclasses import dataclass, field

from sortedcontainers import SortedList

from core.constants import SIDE_BUY, SIDE_SELL
from core.order_struct import Order


@dataclass(slots=True)
class Trade:
    aggressor_id  : int
    resting_id    : int
    price         : float
    quantity      : int
    timestamp_ns  : int = field(
        default_factory=lambda: time.perf_counter_ns()
    )


@dataclass(slots=True)
class BookEntry:
    """A resting order in the book (may be partially filled)."""
    order_id : int
    price    : float
    quantity : int    # remaining quantity
    seq      : int    # global sequence number (time priority)

    def bid_key(self):
        return (-self.price, self.seq)

    def ask_key(self):
        return ( self.price, self.seq)


class LimitOrderBook:
    """
    Central matching engine.

    Usage::
        lob    = LimitOrderBook()
        trades = lob.submit(order)   # returns list[Trade]
    """

    def __init__(self):
        # Bids: sort by (-price, seq) so index[0] = best bid (highest price)
        self._bids: SortedList = SortedList(key=lambda e: (-e.price, e.seq))
        # Asks: sort by (+price, seq) so index[0] = best ask (lowest price)
        self._asks: SortedList = SortedList(key=lambda e: ( e.price, e.seq))

        self._seq           = 0
        self.total_orders   = 0
        self.total_trades   = 0
        self.total_volume   = 0
        self._last_match_ns = 0

    def submit(self, order) -> list[Trade]:
        """Submit an incoming order; return list of resulting Trade objects."""
        if not isinstance(order, Order):
            raise TypeError("order must be an Order instance")
        order.__post_init__()
        self.total_orders += 1
        self._seq += 1
        entry = BookEntry(
            order_id=order.order_id,
            price=order.price,
            quantity=order.quantity,
            seq=self._seq,
        )
        t0     = time.perf_counter_ns()
        trades = (self._match_buy(entry)
                  if order.is_buy else self._match_sell(entry))
        self._last_match_ns = time.perf_counter_ns() - t0
        self.total_trades  += len(trades)
        self.total_volume  += sum(t.quantity for t in trades)
        return trades

    # ---- book queries -------------------------------------------------------

    @property
    def best_bid(self) -> float | None:
        return self._bids[0].price if self._bids else None

    @property
    def best_ask(self) -> float | None:
        return self._asks[0].price if self._asks else None

    @property
    def bid_depth(self) -> int:
        return len(self._bids)

    @property
    def ask_depth(self) -> int:
        return len(self._asks)

    @property
    def last_match_us(self) -> float:
        return self._last_match_ns / 1_000.0

    def top_of_book(self, levels: int = 8) -> dict:
        """Return top-N bid and ask levels aggregated by price."""
        if isinstance(levels, bool) or not isinstance(levels, int):
            raise TypeError("levels must be a positive integer")
        if levels <= 0:
            raise ValueError("levels must be positive")
        bids: dict = {}
        for e in self._bids:
            bids.setdefault(e.price, 0)
            bids[e.price] += e.quantity
            if len(bids) >= levels:
                break
        asks: dict = {}
        for e in self._asks:
            asks.setdefault(e.price, 0)
            asks[e.price] += e.quantity
            if len(asks) >= levels:
                break
        return {
            "bids": sorted(bids.items(), reverse=True),
            "asks": sorted(asks.items()),
        }

    def cancel(self, order_id: int) -> int | None:
        """Cancel a resting order and return its remaining quantity."""
        if isinstance(order_id, bool) or not isinstance(order_id, int):
            raise TypeError("order_id must be an integer")
        for side in (self._bids, self._asks):
            for entry in side:
                if entry.order_id == order_id:
                    side.remove(entry)
                    return entry.quantity
        return None

    def stats(self) -> dict:
        return {
            "total_orders"  : self.total_orders,
            "total_trades"  : self.total_trades,
            "total_volume"  : self.total_volume,
            "bid_depth"     : self.bid_depth,
            "ask_depth"     : self.ask_depth,
            "best_bid"      : self.best_bid,
            "best_ask"      : self.best_ask,
            "last_match_us" : self.last_match_us,
        }

    # ---- matching -----------------------------------------------------------

    def _match_buy(self, buy: BookEntry) -> list[Trade]:
        trades = []
        while buy.quantity > 0 and self._asks:
            best = self._asks[0]
            if best.price > buy.price:
                break
            fill = min(buy.quantity, best.quantity)
            trades.append(Trade(
                aggressor_id=buy.order_id,
                resting_id=best.order_id,
                price=best.price,
                quantity=fill,
            ))
            buy.quantity  -= fill
            best.quantity -= fill
            if best.quantity == 0:
                self._asks.pop(0)
        if buy.quantity > 0:
            self._bids.add(buy)
        return trades

    def _match_sell(self, sell: BookEntry) -> list[Trade]:
        trades = []
        while sell.quantity > 0 and self._bids:
            best = self._bids[0]
            if best.price < sell.price:
                break
            fill = min(sell.quantity, best.quantity)
            trades.append(Trade(
                aggressor_id=sell.order_id,
                resting_id=best.order_id,
                price=best.price,
                quantity=fill,
            ))
            sell.quantity -= fill
            best.quantity -= fill
            if best.quantity == 0:
                self._bids.pop(0)
        if sell.quantity > 0:
            self._asks.add(sell)
        return trades


# ---- Self-test --------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    from core.constants import SIDE_BUY, SIDE_SELL
    from core.order_struct import Order

    lob = LimitOrderBook()
    for i, px in enumerate([99.50, 99.75, 100.00]):
        lob.submit(Order(order_id=i, price=px, quantity=100, side=SIDE_BUY))
    for i, px in enumerate([100.25, 100.50, 100.75], start=3):
        lob.submit(Order(order_id=i, price=px, quantity=100, side=SIDE_SELL))

    tob = lob.top_of_book(3)
    print(f"Best bid: {lob.best_bid}   Best ask: {lob.best_ask}")
    trades = lob.submit(Order(order_id=99, price=100.50, quantity=150, side=SIDE_BUY))
    print(f"Aggressive BUY 150@100.50 -> {len(trades)} trade(s)")
    for t in trades:
        print(f"  TRADE qty={t.quantity} @ {t.price:.4f}  lat={lob.last_match_us:.2f}us")
    print(f"Stats: {lob.stats()}")
