import pytest
from core.paper_market import DEMO_SOURCE, PaperTrading


def test_instrument_catalog_is_searchable_and_explicitly_simulated():
    market = PaperTrading()

    instruments = market.instruments(query="tata")
    assert {item["symbol"] for item in instruments} == {"TCS", "TATAMOTORS"}
    assert all(item["data_source"] == DEMO_SOURCE for item in instruments)
    assert len({item["price"] for item in market.instruments()}) > 1


def test_market_order_executes_through_matching_engine_and_updates_portfolio():
    market = PaperTrading()

    order = market.submit_order("TCS", "BUY", "MARKET", 10)
    state = market.state()

    assert order["status"] == "filled"
    assert order["filled_quantity"] == 10
    assert state["cash"] < 1_000_000
    assert state["holdings"][0]["symbol"] == "TCS"
    assert state["holdings"][0]["quantity"] == 10
    assert state["executions"][0]["quantity"] == 10
    assert state["executions"][0]["price"] == order["average_fill_price"]


def test_market_preview_matches_market_order_limit_and_available_cash():
    market = PaperTrading()
    preview = market.preview_market_order("TCS", "BUY")
    assert preview["estimated_price"] == market.book("TCS")["asks"][-1][0]
    assert preview["max_quantity"] == int(
        market.state()["buying_power"] // preview["estimated_price"]
    )

    order = market.submit_order(
        "TCS", "BUY", "MARKET", preview["max_quantity"]
    )
    assert order["status"] == "filled"
    with pytest.raises(ValueError, match="buying power"):
        market.submit_order(
            "TCS", "BUY", "MARKET", preview["max_quantity"] + 1
        )


def test_selling_holding_updates_cash_and_realized_profit():
    market = PaperTrading()
    market.submit_order("INFY", "BUY", "MARKET", 10)
    before_sale = market.state()

    sale = market.submit_order("INFY", "SELL", "MARKET", 10)
    state = market.state()

    assert sale["status"] == "filled"
    assert state["holdings"] == []
    assert state["cash"] > before_sale["cash"]
    assert state["realized_pnl"] < 0


def test_limit_order_can_be_cancelled_and_releases_reservation():
    market = PaperTrading()
    quote = market.instrument("RELIANCE")["quote"]
    order = market.submit_order(
        "RELIANCE", "BUY", "LIMIT", 2, price=quote["price"]
    )

    assert order["status"] == "open"
    assert market.state()["reserved_cash"] == 2 * quote["price"]
    cancelled = market.cancel_order(order["order_id"])
    assert cancelled["status"] == "cancelled"
    assert market.state()["reserved_cash"] == 0


def test_open_sell_order_reserves_shares_and_reports_available_quantity():
    market = PaperTrading()
    market.submit_order("INFY", "BUY", "MARKET", 5)
    book = market.book("INFY")
    resting_sell = market.submit_order(
        "INFY", "SELL", "LIMIT", 3, price=book["asks"][-1][0] + 1
    )

    holding = market.state()["holdings"][0]

    assert resting_sell["status"] == "open"
    assert holding["quantity"] == 5
    assert holding["reserved_quantity"] == 3
    assert holding["available_quantity"] == 2
    market.cancel_order(resting_sell["order_id"])
    assert market.state()["holdings"][0]["available_quantity"] == 5


def test_paper_engine_rejects_orders_that_would_self_match():
    market = PaperTrading()
    market.submit_order("SBIN", "BUY", "MARKET", 5)
    quote = market.instrument("SBIN")["quote"]
    market.submit_order("SBIN", "SELL", "LIMIT", 1, price=quote["price"])

    with pytest.raises(ValueError, match="own resting"):
        market.submit_order(
            "SBIN", "BUY", "LIMIT", 1, price=quote["price"]
        )


def test_insufficient_cash_and_unowned_sales_are_rejected():
    market = PaperTrading()

    with pytest.raises(ValueError, match="buying power"):
        market.submit_order("TCS", "BUY", "MARKET", 1_000_000)
    with pytest.raises(ValueError, match="shares"):
        market.submit_order("TCS", "SELL", "MARKET", 1)


@pytest.mark.parametrize(
    "args,error",
    [
        (("UNKNOWN", "BUY", "MARKET", 1, None), ValueError),
        (("TCS", "HOLD", "MARKET", 1, None), ValueError),
        (("TCS", "BUY", "STOP", 1, None), ValueError),
        (("TCS", "BUY", "MARKET", 0, None), ValueError),
        (("TCS", "BUY", "LIMIT", 1, float("nan")), ValueError),
        (("TCS", "BUY", "LIMIT", 1, 10**1000), ValueError),
    ],
)
def test_invalid_paper_orders_are_rejected(args, error):
    with pytest.raises(error):
        PaperTrading().submit_order(*args)


def test_session_history_and_reset_are_truthful():
    market = PaperTrading()
    details = market.instrument("SBIN", "1Y")
    assert details["history"]
    assert "only this server session" in details["history_note"]

    market.submit_order("SBIN", "BUY", "MARKET", 1)
    state = market.reset()

    assert state["cash"] == 1_000_000
    assert state["holdings"] == []
    assert state["orders"] == []
    assert state["executions"] == []
