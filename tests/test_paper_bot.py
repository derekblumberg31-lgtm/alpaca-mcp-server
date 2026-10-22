"""The unattended bot never contacts Alpaca in its unit tests."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from alpaca_mcp_server.paper_bot import (
    NY, EmailReporter, Signal, compute_signal, plan_buy, run_cycle, run_from_environment,
)
from alpaca_mcp_server.paper_auth import main as authenticated_main


NOW = datetime(2026, 10, 21, 10, 5, tzinfo=NY)


def signal(symbol: str) -> Signal:
    return Signal(symbol, True, Decimal("100"), Decimal("95"), Decimal("90"),
                  0.10, 0.15, 0.0002, "pass")


def test_signal_uses_completed_bars_and_quote():
    bars = []
    for i in range(220):
        day = NOW.date() - timedelta(days=220 - i)
        bars.append({"t": f"{day.isoformat()}T12:00:00Z", "c": 80 + i * 0.1})
    bars.append({"t": "2026-10-21T12:00:00Z", "c": 1})  # partial day ignored
    quote = {"bp": 101.98, "ap": 102.02, "t": "2026-10-21T14:04:00Z"}
    result = compute_signal("QQQ", bars, quote, NOW)
    assert result.eligible
    assert result.close == Decimal("101.9")
    assert result.spread_pct < 0.0015


def test_plan_buy_uses_cash_not_margin_and_skips_duplicate():
    account = {"equity": "100154.34", "cash": "61154.34", "status": "ACTIVE"}
    positions = [{"symbol": "SPY", "side": "long", "market_value": "39000"}]
    orders = [{"symbol": "SPY", "side": "buy", "status": "filled",
               "submitted_at": "2026-10-21T13:00:00Z"}]
    order = plan_buy(account, positions, orders, [signal("SPY"), signal("QQQ")], NOW)
    assert order["symbol"] == "QQQ"
    assert Decimal(order["notional"]) <= Decimal("61154.34") - Decimal("100154.34") * Decimal("0.205")
    assert order["client_order_id"] == "paperv1-20261021-QQQ-buy"
    assert plan_buy(account, positions, orders + [dict(order, status="new",
                   submitted_at="2026-10-21T14:00:00Z")],
                   [signal("SPY"), signal("QQQ")], NOW) is None


def test_overweight_existing_position_blocks_new_buy():
    account = {"equity": "100000", "cash": "59000", "status": "ACTIVE"}
    positions = [{"symbol": "SPY", "side": "long", "market_value": "41000"}]
    assert plan_buy(account, positions, [], [signal("QQQ")], NOW) is None


class FakeAPI:
    def __init__(self, drawdown: bool = False):
        self.submitted = []
        self.drawdown = drawdown

    def get(self, path, *, data=False, params=None):
        if path == "/v2/account":
            return {"equity": "100154.34", "cash": "61154.34", "status": "ACTIVE"}
        if path == "/v2/positions":
            return [{"symbol": "SPY", "side": "long", "market_value": "39000"}]
        if path == "/v2/orders":
            return [{"symbol": "SPY", "side": "buy", "status": "filled",
                     "submitted_at": "2026-10-21T13:00:00Z"}]
        if path == "/v2/account/portfolio/history":
            return {"equity": ["112000" if self.drawdown else "100000"]}
        if path == "/v2/clock":
            return {"is_open": True}
        if path == "/v2/stocks/quotes/latest":
            return {"quotes": {s: {"bp": 101.98, "ap": 102.02,
                     "t": "2026-10-21T14:04:00Z"} for s in ("SPY", "QQQ", "IWM", "TLT")}}
        if path == "/v1beta1/news":
            return {"news": []}
        if path.startswith("/v2/stocks/"):
            return {"bars": []}
        raise AssertionError(path)

    def submit(self, order):
        self.submitted.append(order)
        return {"id": "paper-id"}


def test_observe_never_submits_even_with_signal():
    api = FakeAPI()
    with patch("alpaca_mcp_server.paper_bot.compute_signal", side_effect=lambda s, *_: signal(s)):
        result = run_cycle(api, "observe", NOW)
    assert not api.submitted
    assert result["proposal"]["symbol"] == "QQQ"


def test_trade_revalidates_and_submits_exactly_one_paper_order():
    api = FakeAPI()
    with patch("alpaca_mcp_server.paper_bot.compute_signal", side_effect=lambda s, *_: signal(s)):
        result = run_cycle(api, "trade", NOW)
    assert result["submitted"] is True
    assert len(api.submitted) == 1
    assert api.submitted[0]["type"] == "market"


def test_ten_percent_drawdown_pauses_trading():
    api = FakeAPI(drawdown=True)
    result = run_cycle(api, "trade", NOW)
    assert result["submitted"] is False
    assert "drawdown" in result["reason"]
    assert not api.submitted


def test_live_config_and_unverified_reporting_rejected():
    with patch.dict("os.environ", {"PAPER_BOT_MODE": "trade", "ALPACA_PAPER_TRADE": "false",
                                    "ALPACA_API_KEY": "x", "ALPACA_SECRET_KEY": "x"}):
        with pytest.raises(ValueError, match="live account"):
            run_from_environment("trade")
    with patch.dict("os.environ", {"PAPER_BOT_MODE": "trade", "ALPACA_PAPER_TRADE": "true",
                                    "ALPACA_API_KEY": "x", "ALPACA_SECRET_KEY": "x"}, clear=True):
        with pytest.raises(ValueError, match="reporting"):
            run_from_environment("trade")
    with patch.dict("os.environ", {"ALPACA_PAPER_TRADE": "false"}):
        with pytest.raises(ValueError, match="paper-trading only"):
            authenticated_main()


def test_fill_email_is_idempotent_and_does_not_send_broker_credentials():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "email-id"})

    reporter = EmailReporter("mail-test-key", "bot@example.com", "owner@example.com")
    reporter.client.close()
    reporter.client = httpx.Client(transport=httpx.MockTransport(handler),
                                   headers={"Authorization": "Bearer mail-test-key"})
    order = {"id": "paper-order", "status": "filled", "symbol": "SPY", "side": "buy",
             "filled_qty": "2", "filled_avg_price": "100.25",
             "filled_at": "2026-10-21T14:00:00Z"}
    account = {"equity": "100100", "cash": "90000"}
    reporter.report_fills([order], account)
    reporter.report_fills([order], account)
    assert len(requests) == 1
    assert requests[0].headers["Idempotency-Key"] == "paper-fill-paper-order"
    assert "APCA-API-KEY-ID" not in requests[0].headers
    assert requests[0].url.host == "api.resend.com"
    assert requests[0].read().decode().count("owner@example.com") == 1
    reporter.close()
