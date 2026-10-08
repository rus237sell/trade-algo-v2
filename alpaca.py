"""Alpaca adapter: free paper trading + free (delayed) market data.

Paper trading on Alpaca is free with no subscription, and paper accounts
support options. Free data tier is ~15-min delayed: fine for backtesting
and plumbing, not for live 1-min signals.

Env: ALPACA_API_KEY, ALPACA_API_SECRET (paper keys from alpaca.markets)
"""
import os
import requests
import pytz

ET = pytz.timezone("America/New_York")


def _utc_z(dt):
    """Format a datetime as a UTC 'Z' string for the Alpaca API.

    Callers pass ET wall-clock datetimes (bot.py) or naive datetimes
    (fetch_history.py). The old code appended a literal "Z" to ET wall
    time, shifting every request window -4h: `end` landed 4h in the
    past, so the freshest bar was always ~240 min old and the bot
    believed the feed was dead. Convert properly instead.
    """
    if dt.tzinfo is None:
        dt = ET.localize(dt)
    return dt.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"


class AlpacaAuth:
    def __init__(self, key=None, secret=None):
        self.key = key or os.getenv("ALPACA_API_KEY")
        self.secret = secret or os.getenv("ALPACA_API_SECRET")
        self.dry = not (self.key and self.secret)

    def headers(self):
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}


class AlpacaBroker:
    """Free paper trading. Real paper orders, no real money."""

    def __init__(self, auth=None):
        self.auth = auth or AlpacaAuth()
        if self.auth.dry:
            raise RuntimeError("ALPACA_API_KEY and ALPACA_API_SECRET are required")

    def _order(self, occ, qty, side, tag):
        r = requests.post(f"{PAPER_BASE}/v2/orders", json={
            "symbol": occ, "qty": qty, "side": side,
            "type": "market", "time_in_force": "day",
            "client_order_id": tag[:48],
        }, headers=self.auth.headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def place(self, occ, side, qty, tag):
        return self._order(occ, qty, "buy", tag)  # buy_to_open

    def close(self, occ, qty, tag):
        return self._order(occ, qty, "sell", tag)  # sell_to_close

    def wait_fill(self, order_id, timeout=15):
        """Poll an order until filled. Returns (filled_avg_price, filled_at)
        or (None, None) on timeout/error. Never raises — trading must not
        block forever on one fill."""
        try:
            import time as _t
            deadline = _t.time() + timeout
            while _t.time() < deadline:
                r = requests.get(f"{PAPER_BASE}/v2/orders/{order_id}",
                                 headers=self.auth.headers(), timeout=10)
                o = r.json()
                if o.get("status") == "filled" and o.get("filled_avg_price"):
                    return float(o["filled_avg_price"]), o.get("filled_at")
                _t.sleep(1)
        except Exception:
            pass
        return None, None

    def positions(self):
        r = requests.get(f"{PAPER_BASE}/v2/positions",
                         headers=self.auth.headers(), timeout=15)
        r.raise_for_status()
        return r.json()


class AlpacaData:
    """Free tier bars: 15-min delayed + full history. Good for backtest."""

    def option_quote(self, occ):
        """Latest bid/ask for one option contract. Returns (bid, ask) or
        (None, None) on any error. Never raises."""
        try:
            r = requests.get(f"{DATA_BASE}/v2/options/quotes/latest",
                             params={"symbols": occ, "feed": "indicative"},
                             headers=self.auth.headers(), timeout=10)
            q = (r.json().get("quotes") or {}).get(occ) or {}
            bid, ask = q.get("bp"), q.get("ap")
            return (float(bid) if bid else None, float(ask) if ask else None)
        except Exception:
            return None, None

    def __init__(self, auth=None):
        self.auth = auth or AlpacaAuth()

    def bars_1min(self, symbol, start, end):
        if self.auth.dry:
            return []
        bars, token = [], None
        while True:
            params = {"timeframe": "1Min",
                      "start": _utc_z(start),
                      "end": _utc_z(end),
                      "limit": 10000, "adjustment": "raw"}
            if token:
                params["page_token"] = token
            r = requests.get(f"{DATA_BASE}/v2/stocks/{symbol}/bars",
                             params=params, headers=self.auth.headers(), timeout=20)
            r.raise_for_status()
            body = r.json()
            for b in body.get("bars") or []:  # None on market holidays
                bars.append({"time": b["t"], "open": b["o"], "high": b["h"],
                             "low": b["l"], "close": b["c"], "volume": b["v"]})
            token = body.get("next_page_token")
            if not token:
                break
        return bars

    def option_contracts(self, underlying, exp_date, cp):
        """0DTE chain for one expiration (cp: 'call' or 'put')."""
        if self.auth.dry:
            return []
        r = requests.get(f"{PAPER_BASE}/v2/options/contracts", params={
            "underlying_symbols": underlying,
            "expiration_date_gte": exp_date, "expiration_date_lte": exp_date,
            "type": cp,
        }, headers=self.auth.headers(), timeout=15)
        r.raise_for_status()
        contracts = r.json().get("option_contracts", [])
        return contracts if isinstance(contracts, list) else []
