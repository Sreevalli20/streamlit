import pytest

from core.constants import SIDE_BUY, SIDE_SELL
from core.order_struct import Order
from engine.limit_order_book import LimitOrderBook


def test_price_time_priority_and_partial_remainder():
    book = LimitOrderBook()
    book.submit(Order(1, 101.0, 3, SIDE_SELL))
    book.submit(Order(2, 100.0, 2, SIDE_SELL))
    book.submit(Order(3, 100.0, 4, SIDE_SELL))

    trades = book.submit(Order(4, 101.0, 8, SIDE_BUY))

    assert [(t.resting_id, t.price, t.quantity) for t in trades] == [
        (2, 100.0, 2),
        (3, 100.0, 4),
        (1, 101.0, 2),
    ]
    assert book.ask_depth == 1
    assert book.best_ask == 101.0
    assert book.total_volume == 8


def test_sell_matches_best_bid_and_keeps_partial_order():
    book = LimitOrderBook()
    book.submit(Order(1, 100.0, 2, SIDE_BUY))
    book.submit(Order(2, 101.0, 4, SIDE_BUY))

    trades = book.submit(Order(3, 100.5, 2, SIDE_SELL))

    assert [(trade.resting_id, trade.price, trade.quantity) for trade in trades] == [
        (2, 101.0, 2)
    ]
    assert book.best_bid == 101.0
    assert book.bid_depth == 2


def test_non_crossing_orders_remain_on_book():
    book = LimitOrderBook()
    assert book.submit(Order(1, 99.0, 5, SIDE_BUY)) == []
    assert book.submit(Order(2, 101.0, 5, SIDE_SELL)) == []
    assert (book.best_bid, book.best_ask) == (99.0, 101.0)


def test_invalid_submission_does_not_mutate_book():
    book = LimitOrderBook()
    order = Order(1, 100.0, 1, SIDE_BUY)
    order.price = float("inf")
    with pytest.raises(ValueError):
        book.submit(order)
    with pytest.raises(TypeError):
        book.submit(object())
    assert book.total_orders == 0
    assert book.bid_depth == book.ask_depth == 0


def test_cancel_removes_only_the_unfilled_remainder():
    book = LimitOrderBook()
    book.submit(Order(1, 100.0, 5, SIDE_SELL))
    book.submit(Order(2, 99.0, 5, SIDE_BUY))
    trades = book.submit(Order(3, 100.0, 2, SIDE_BUY))

    assert trades[0].resting_id == 1
    assert book.cancel(1) == 3
    assert book.ask_depth == 0
    assert book.cancel(1) is None


@pytest.mark.parametrize("order_id, error", [(True, TypeError), ("1", TypeError)])
def test_cancel_rejects_invalid_order_ids(order_id, error):
    with pytest.raises(error):
        LimitOrderBook().cancel(order_id)


@pytest.mark.parametrize("levels, error", [(0, ValueError), (-1, ValueError), (True, TypeError)])
def test_top_of_book_levels_are_validated(levels, error):
    with pytest.raises(error):
        LimitOrderBook().top_of_book(levels)
