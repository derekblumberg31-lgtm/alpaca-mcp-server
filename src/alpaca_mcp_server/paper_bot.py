"""One-shot, paper-only portfolio observer and optional order runner.

The free scheduled workflow invokes this once per run in ``observe`` mode.
No broker credentials are stored in this repository. Broker state, rather
than a runner's temporary filesystem, is the source of truth.
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

import httpx


LOG = logging.getLogger("alpaca.paper_bot")
NY = ZoneInfo("America/New_York")
PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
EMAIL_URL = "https://api.resend.com/emails"
UNIVERSE = ("SPY", "QQQ", "IWM", "TLT")
MONEY = Decimal("0.01")
MAX_ORDER = Decimal("40000")
MIN_ORDER = Decimal("1000")
TARGET_WEIGHT = Decimal("0.395")  # room below the 40% hard position cap
MIN_CASH_WEIGHT = Decimal("0.205")  # room above the 20% hard cash floor


def dec(value: object) -> Decimal:
    return Decimal(str(value))


def dollars(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_DOWN)


@dataclass(frozen=True)
class Signal:
    symbol: str
    eligible: bool
    close: Decimal
    sma50: Decimal
    sma200: Decimal
    momentum63: float
    volatility20: float
    spread_pct: float
    reason: str


def compute_signal(symbol: str, bars: list[dict], quote: dict, now: datetime) -> Signal:
    """Only completed daily bars and a recent, reasonably tight quote qualify."""
    completed = sorted(
        (b for b in bars if datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
         .astimezone(NY).date() < now.date()),
        key=lambda b: b["t"],
    )
    if len(completed) < 200:
        raise ValueError(f"{symbol}: fewer than 200 completed daily bars")
    if (now.date() - datetime.fromisoformat(completed[-1]["t"].replace("Z", "+00:00"))
            .astimezone(NY).date()).days > 5:
        raise ValueError(f"{symbol}: historical bars are stale")
    close = [dec(b["c"]) for b in completed]
    if any(c <= 0 for c in close[-200:]):
        raise ValueError(f"{symbol}: invalid historical close")
    bid, ask = dec(quote.get("bp", 0)), dec(quote.get("ap", 0))
    stamp = datetime.fromisoformat(str(quote.get("t", "")).replace("Z", "+00:00"))
    age = (now - stamp.astimezone(NY)).total_seconds()
    if bid <= 0 or ask <= bid or age < -60 or age > 900:
        raise ValueError(f"{symbol}: quote is invalid or older than 15 minutes")
    spread = float((ask - bid) / ((ask + bid) / 2))
    sma50 = sum(close[-50:]) / 50
    sma200 = sum(close[-200:]) / 200
    momentum = float(close[-1] / close[-64] - 1)
    changes = [math.log(float(b / a)) for a, b in zip(close[-21:-1], close[-20:])]
    volatility = statistics.stdev(changes) * math.sqrt(252)
    eligible = (close[-1] > sma200 and sma50 > sma200 and momentum > 0
                and volatility < 0.35 and spread < 0.0015)
    reason = "trend, momentum, volatility and spread pass" if eligible else "signal gate failed"
    return Signal(symbol, eligible, close[-1], sma50, sma200, momentum,
                  volatility, spread, reason)


def plan_buy(account: dict, positions: list[dict], orders: list[dict],
             signals: list[Signal], now: datetime) -> dict | None:
    """Return at most one paper buy, never exceeding cash or position limits."""
    equity, cash = dec(account["equity"]), dec(account["cash"])
    if equity <= 0 or cash < 0 or account.get("status") != "ACTIVE":
        return None
    if account.get("trading_blocked") or account.get("account_blocked"):
        return None
    if any(p.get("symbol") not in UNIVERSE or p.get("side") != "long" for p in positions):
        return None  # refuse to manage an unexpected portfolio
    if any(dec(p["market_value"]) > equity * Decimal("0.40") for p in positions):
        return None  # do not add risk while any existing holding breaches the cap
    if any(o.get("status") in {"new", "accepted", "pending_new", "partially_filled"}
           for o in orders):
        return None
    submitted_today = [o for o in orders if str(o.get("submitted_at", ""))[:10] == now.date().isoformat()]
    if len(submitted_today) >= 2:
        return None
    values = {p["symbol"]: dec(p["market_value"]) for p in positions}
    available = dollars(cash - equity * MIN_CASH_WEIGHT)
    if available < MIN_ORDER:
        return None
    selected = sorted((s for s in signals if s.eligible), key=lambda s: s.momentum63,
                      reverse=True)[:2]
    for signal in selected:
        symbol = signal.symbol
        if any(o.get("symbol") == symbol and o.get("side") == "buy"
               for o in submitted_today):
            continue
        current = values.get(symbol, Decimal("0"))
        gap = equity * TARGET_WEIGHT - current
        if gap < equity * Decimal("0.05"):
            continue  # avoid small, frequent trades
        notional = dollars(min(gap, available, MAX_ORDER))
        if notional < MIN_ORDER or (current + notional) > equity * Decimal("0.40"):
            continue
        if cash - notional < equity * Decimal("0.20"):
            continue
        return {
            "symbol": symbol, "side": "buy", "notional": str(notional),
            "type": "market", "time_in_force": "day",
            "client_order_id": f"paperv1-{now:%Y%m%d}-{symbol}-buy",
        }
    return None


class PaperAPI:
    """Fixed paper host: a live-trading URL cannot be supplied by environment."""

    def __init__(self, key: str, secret: str):
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.client = httpx.Client(headers=headers, timeout=20.0)

    def close(self) -> None:
        self.client.close()

    def get(self, path: str, *, data: bool = False, params: dict | None = None):
        base = DATA_URL if data else PAPER_URL
        response = self.client.get(base + path, params=params)
        response.raise_for_status()
        return response.json()

    def submit(self, order: dict):
        response = self.client.post(PAPER_URL + "/v2/orders", json=order)
        response.raise_for_status()
        return response.json()


class EmailReporter:
    """HTTPS-only, idempotent trade and balance reports; never uses Alpaca headers."""

    def __init__(self, api_key: str, sender: str, recipient: str):
        if not api_key or not sender or "@" not in sender or "@" not in recipient:
            raise ValueError("Resend API key, verified sender, and recipient are required")
        self.sender = sender
        self.recipient = recipient
        self.client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"},
                                   timeout=20.0)
        self.sent: set[str] = set()

    def close(self) -> None:
        self.client.close()

    def _send(self, unique_key: str, subject: str, body: str) -> None:
        if unique_key in self.sent:
            return
        response = self.client.post(
            EMAIL_URL,
            headers={"Idempotency-Key": unique_key},
            json={"from": self.sender, "to": [self.recipient], "subject": subject,
                  "text": body},
        )
        if response.status_code == 409 and "invalid_idempotent_request" in response.text:
            # A previous request with this key was accepted with a different
            # balance snapshot. Treat it as delivered, not a reason to resend.
            self.sent.add(unique_key)
            return
        response.raise_for_status()
        self.sent.add(unique_key)

    def report_fills(self, orders: list[dict], account: dict) -> None:
        for order in orders:
            if order.get("status") != "filled" or not order.get("filled_at"):
                continue
            if not order.get("filled_qty") or not order.get("filled_avg_price"):
                continue
            qty = dec(order["filled_qty"])
            price = dec(order["filled_avg_price"])
            gross = dollars(qty * price)
            order_id = str(order.get("id", ""))
            if not order_id:
                continue
            body = (f"Alpaca PAPER fill\nOrder: {order_id}\n"
                    f"Symbol: {order.get('symbol')}\nSide: {order.get('side')}\n"
                    f"Filled quantity: {qty}\nAverage fill price: ${price}\n"
                    f"Gross dollars moved: ${gross}\n"
                    "Realized P/L: not established from this fill alone\n"
                    f"Account equity at report time: ${account.get('equity')}\n"
                    f"Cash at report time: ${account.get('cash')}\n"
                    f"Filled at: {order['filled_at']}\n")
            self._send(f"paper-fill-{order_id}",
                       f"Paper trade filled: {order.get('side')} {order.get('symbol')}", body)

    def report_balance(self, account: dict, now: datetime) -> None:
        if now.weekday() >= 5 or (now.hour, now.minute) < (16, 5):
            return
        body = (f"Alpaca PAPER daily balance — {now.date()}\n"
                f"Equity: ${account.get('equity')}\nCash: ${account.get('cash')}\n"
                f"Long market value: ${account.get('long_market_value')}\n"
                f"Last equity: ${account.get('last_equity')}\n"
                "This is a paper account, not realized revenue or a profit guarantee.\n")
        self._send(f"paper-balance-{now:%Y%m%d}", "Paper account balance", body)

    def report_drawdown(self, account: dict, drawdown: float, now: datetime) -> None:
        body = ("Alpaca PAPER bot paused new orders after a 10% or greater "
                "one-month account drawdown. Human review is required.\n"
                f"Drawdown: {drawdown:.1%}\n"
                f"Equity at check time: ${account.get('equity')}\n"
                f"Cash at check time: ${account.get('cash')}\n")
        self._send(f"paper-drawdown-{now:%Y%m%d}", "Paper bot paused: major loss", body)


def run_cycle(api: PaperAPI, mode: str, now: datetime | None = None,
              reporter: EmailReporter | None = None) -> dict:
    now = (now or datetime.now(NY)).astimezone(NY)
    if mode not in {"observe", "trade"}:
        raise ValueError("bot mode must be observe or trade")
    account = api.get("/v2/account")
    positions = api.get("/v2/positions")
    orders = api.get("/v2/orders", params={"status": "all", "limit": 500,
        "after": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()})
    result = {"at": now.isoformat(), "mode": mode, "equity": account.get("equity"),
              "cash": account.get("cash"), "position_symbols": [p.get("symbol") for p in positions],
              "orders_today": len(orders), "submitted": False}
    if reporter is not None:
        reporter.report_fills(orders, account)
        reporter.report_balance(account, now)
    history = api.get("/v2/account/portfolio/history",
                      params={"period": "1M", "timeframe": "1D"})
    equity_history = [dec(v) for v in history.get("equity", []) if v is not None and dec(v) > 0]
    peak = max(equity_history + [dec(account["equity"])])
    drawdown = float(dec(account["equity"]) / peak - 1)
    result["drawdown_1m"] = round(drawdown, 4)
    if drawdown <= -0.10:
        if reporter is not None:
            reporter.report_drawdown(account, drawdown, now)
        result["reason"] = "10% drawdown pause; review required"
        return result
    clock = api.get("/v2/clock")
    local_time = now.time()
    if not clock.get("is_open") or not ((local_time.hour, local_time.minute) >= (9, 45)
                                          and (local_time.hour, local_time.minute) < (15, 30)):
        result["reason"] = "outside the regular-session decision window"
        return result
    start = (now.date() - timedelta(days=420)).isoformat()
    end = now.date().isoformat()
    quotes = api.get("/v2/stocks/quotes/latest", data=True,
                     params={"symbols": ",".join(UNIVERSE), "feed": "iex"}).get("quotes", {})
    news = api.get("/v1beta1/news", data=True,
                   params={"symbols": ",".join(UNIVERSE), "limit": 20,
                           "start": (now - timedelta(days=1)).isoformat()})
    result["news_headlines_checked"] = len(news.get("news", []))
    headlines = [str(item.get("headline", "")).lower() for item in news.get("news", [])]
    if any(term in headline for headline in headlines
           for term in ("trading halt", "market closure", "exchange outage")):
        result["reason"] = "material market-disruption headline; review required"
        return result
    signals = []
    for symbol in UNIVERSE:
        bars = api.get(f"/v2/stocks/{symbol}/bars", data=True,
                       params={"timeframe": "1Day", "start": start, "end": end,
                               "feed": "iex", "limit": 1000}).get("bars", [])
        try:
            signals.append(compute_signal(symbol, bars, quotes.get(symbol, {}), now))
        except (ValueError, KeyError, TypeError) as error:
            LOG.warning("signal unavailable: %s", error)
    result["eligible"] = [s.symbol for s in signals if s.eligible]
    order = plan_buy(account, positions, orders, signals, now)
    if order is None:
        result["reason"] = "no eligible order within portfolio limits"
        return result
    result["proposal"] = order
    if mode == "observe":
        result["reason"] = "observation only; no order sent"
        return result
    # Re-read broker state immediately before sending. Never trust margin buying power.
    fresh_account = api.get("/v2/account")
    fresh_positions = api.get("/v2/positions")
    fresh_orders = api.get("/v2/orders", params={"status": "all", "limit": 500,
        "after": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()})
    fresh = plan_buy(fresh_account, fresh_positions, fresh_orders, signals, now)
    if fresh != order:
        result["reason"] = "broker state changed before submission"
        return result
    placed = api.submit(order)
    result["submitted"] = True
    result["broker_order_id"] = placed.get("id")
    result["reason"] = "paper order accepted; fill not yet confirmed"
    return result


def run_from_environment(mode: str) -> dict:
    """Run one bounded cycle; trade mode requires a separate verified reporter."""
    if mode not in {"observe", "trade"}:
        raise ValueError("mode must be observe or trade")
    if os.environ.get("ALPACA_PAPER_TRADE", "true").lower() not in {"true", "1", "yes"}:
        raise ValueError("paper bot refuses a live account configuration")
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise ValueError("paper bot requires Alpaca API credentials")
    email_key = os.environ.get("RESEND_API_KEY", "")
    email_sender = os.environ.get("PAPER_BOT_FROM_EMAIL", "")
    email_to = os.environ.get("PAPER_BOT_EMAIL_TO", "")
    if mode == "trade" and not (email_key and email_sender and email_to):
        raise ValueError("trade mode requires a configured email reporting channel")
    api = PaperAPI(key, secret)
    reporter = (EmailReporter(email_key, email_sender, email_to)
                if email_key and email_sender and email_to else None)
    try:
        return run_cycle(api, mode, reporter=reporter)
    finally:
        api.close()
        if reporter is not None:
            reporter.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="One paper-account research cycle")
    parser.add_argument("--mode", choices=("observe", "trade"), default="observe")
    parser.add_argument("--quiet", action="store_true", help="omit account details from public logs")
    args = parser.parse_args()
    result = run_from_environment(args.mode)
    if args.quiet:
        print("Paper account observation completed; no account details printed")
    else:
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
