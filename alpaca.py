"""Alpaca adapter: free paper trading + free (delayed) market data.

Paper trading on Alpaca is free with no subscription, and paper accounts
support options. Free data tier is ~15-min delayed: fine for backtesting
and plumbing, not for live 1-min signals.

Env: ALPACA_API_KEY, ALPACA_API_SECRET (paper keys from alpaca.markets)
"""
import os
import uuid
from datetime import datetime, timedelta

import requests
import pytz

ET = pytz.timezone("America/New_York")

# Alpaca's free Basic plan refuses SIP data younger than 15 min (403
# Forbidden), so bars requests must end at least this far in the past.
FREE_PLAN_DELAY_MIN = 16


def _utc_z(dt):
    """Format a datetime as a true UTC 'Z' string for the Alpaca API.
    Callers pass ET wall-clock datetimes; appending a literal 'Z' to ET
    time shifts the window -4h, so the freshest bar is always ~240 min
    old and the fail-safe blocks every entry. Convert properly."""
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

    def _order(self, occ, qty, side, tag, limit_price=None):
        # client_order_id must be unique per order on the account: Alpaca
        # rejects a reused id with 422. Bare reason tags ("v2:atr-stop")
        # collide with orders from earlier sessions, which wedged every
        # exit retry. Suffix a short unique id per submission instead.
        cid = f"{tag}:{uuid.uuid4().hex[:8]}"[:48]
        payload = {
            "symbol": occ, "qty": qty, "side": side,
            "type": "limit" if limit_price is not None else "market",
            "time_in_force": "day",
            "client_order_id": cid,
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)
        r = requests.post(f"{PAPER_BASE}/v2/orders", json=payload,
                          headers=self.auth.headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def place(self, occ, side, qty, tag):
        return self._order(occ, qty, "buy", tag)  # buy_to_open

    def close(self, occ, qty, tag):
        try:
            return self._order(occ, qty, "sell", tag)  # sell_to_close
        except requests.HTTPError as e:
            # Alpaca rejects a MARKET sell when the contract has no
            # available quote (403, code 40310000: "please reenter with
            # a limit") — exactly what happens to a 0DTE contract that
            # travelled far OTM. Its own remedy is a limit order: retry
            # once at the $0.01 floor so the exit exists instead of
            # retrying a doomed market order every cycle.
            body = e.response.text if e.response is not None else ""
            if e.response is not None and e.response.status_code == 403 \
                    and "no available quote" in body:
                return self._order(occ, qty, "sell", tag, limit_price=0.01)
            raise

    def positions(self):
        r = requests.get(f"{PAPER_BASE}/v2/positions",
                         headers=self.auth.headers(), timeout=15)
        r.raise_for_status()
        return r.json()


class AlpacaData:
    """Free tier bars: 15-min delayed + full history. Good for backtest."""

    def __init__(self, auth=None):
        self.auth = auth or AlpacaAuth()

    def bars_1min(self, symbol, start, end):
        if self.auth.dry:
            return []

        def _aware(dt):
            return ET.localize(dt) if dt.tzinfo is None else dt

        # Clamp the window end into the free plan's entitlement: asking
        # for SIP bars newer than the delay cap 403s the whole request.
        end_cap = datetime.now(pytz.UTC) - timedelta(minutes=FREE_PLAN_DELAY_MIN)
        end = min(_aware(end), end_cap)
        if end <= _aware(start):
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
        """0DTE chain for one expiration (cp: 'call' or 'put').

        The endpoint paginates (100 contracts per page, strikes
        ascending). Reading only the first page truncates the chain
        far below the money on QQQ/SPY — strike selection then lands
        on the page boundary (a 655 put with QQQ at 721). Follow
        next_page_token to the end of the chain.
        """
        if self.auth.dry:
            return []
        contracts, token = [], None
        while True:
            params = {
                "underlying_symbols": underlying,
                "expiration_date_gte": exp_date,
                "expiration_date_lte": exp_date,
                "type": cp, "limit": 100,
            }
            if token:
                params["page_token"] = token
            r = requests.get(f"{PAPER_BASE}/v2/options/contracts",
                             params=params, headers=self.auth.headers(),
                             timeout=15)
            r.raise_for_status()
            body = r.json()
            page = body.get("option_contracts", [])
            if isinstance(page, list):
                contracts.extend(page)
            token = body.get("next_page_token")
            if not token:
                break
        return contracts
