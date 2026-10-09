"""Broker adapters. DryRunBroker logs orders (no keys needed).
TradierBroker paper-trades through the Tradier sandbox."""
import os
import re
import requests

SANDBOX = "https://sandbox.tradier.com"
LIVE = "https://api.tradier.com"


# Matches OCC option symbols like QQQ261005C00640000.
_OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def occ_symbol(root, exp_yyyymmdd, cp, strike):
    """OCC option symbol, e.g. QQQ 2026-10-05 C 640 -> QQQ261005C00640000."""
    return f"{root}{exp_yyyymmdd[2:]}{cp}{int(round(strike * 1000)):08d}"


def re_occ_match(occ):
    """True when occ parses as an OCC option symbol."""
    return bool(_OCC_RE.match(occ))


def parse_occ(occ):
    """Split an OCC symbol into (root, exp_yymmdd, cp, strike)."""
    m = _OCC_RE.match(occ)
    if not m:
        raise ValueError(f"not an OCC option symbol: {occ}")
    root, yymmdd, cp, strike8 = m.groups()
    return root, yymmdd, cp, int(strike8) / 1000


class DryRunBroker:
    def __init__(self):
        self.orders = []

    def place(self, occ, side, qty, tag):
        self.orders.append({"occ": occ, "side": side, "qty": qty, "tag": tag})
        print(f"[DRYRUN] BUY_TO_OPEN {qty}x {occ} ({tag})")
        return {"id": f"dry-{len(self.orders)}"}

    def close(self, occ, qty, tag):
        print(f"[DRYRUN] SELL_TO_CLOSE {qty}x {occ} ({tag})")

    def positions(self):
        return []


class TradierBroker:
    """Paper trading via Tradier sandbox. Set TRADIER_API_KEY and
    TRADIER_ACCOUNT_ID env vars (never commit them)."""

    def __init__(self, account_id=None, api_key=None, env="sandbox"):
        self.key = api_key or os.getenv("TRADIER_API_KEY")
        self.acct = account_id or os.getenv("TRADIER_ACCOUNT_ID")
        self.base = SANDBOX if env == "sandbox" else LIVE
        if not self.key or not self.acct:
            raise RuntimeError("TRADIER_API_KEY and TRADIER_ACCOUNT_ID are required")

    def _post(self, path, data):
        r = requests.post(
            self.base + path, data=data,
            headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def place(self, occ, side, qty, tag):
        return self._post(f"/v1/accounts/{self.acct}/orders", {
            "class": "option", "symbol": occ, "side": "buy_to_open",
            "quantity": qty, "type": "market", "duration": "day", "tag": tag,
        })

    def close(self, occ, qty, tag):
        return self._post(f"/v1/accounts/{self.acct}/orders", {
            "class": "option", "symbol": occ, "side": "sell_to_close",
            "quantity": qty, "type": "market", "duration": "day", "tag": tag,
        })

    def positions(self):
        """Open positions normalized to {occ, qty, side} (side: CALL/PUT for
        long option legs). Only long option legs matter to the bot."""
        r = requests.get(
            f"{self.base}/v1/accounts/{self.acct}/positions",
            headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json().get("positions", {}).get("position") or []
        if isinstance(raw, dict):
            raw = [raw]
        out = []
        for p in raw:
            occ = p.get("symbol", "")
            qty = float(p.get("quantity", 0))
            if qty <= 0 or not re_occ_match(occ):
                continue
            out.append({"occ": occ, "qty": int(qty), "side": "long"})
        return out
