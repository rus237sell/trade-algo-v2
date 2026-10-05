"""Market data via Tradier. Without an API key every call returns empty (dry-run)."""
import os
import requests

LIVE_BASE = "https://api.tradier.com"
SANDBOX_BASE = "https://sandbox.tradier.com"


class TradierData:
    def __init__(self, api_key=None, env="sandbox"):
        self.key = api_key or os.getenv("TRADIER_API_KEY")
        self.base = SANDBOX_BASE if env == "sandbox" else LIVE_BASE
        self.dry = not self.key

    def _get(self, path, params):
        if self.dry:
            return {}
        r = requests.get(
            self.base + path, params=params,
            headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def bars_1min(self, symbol, start, end):
        """1-minute bars between datetimes. Returns list of dicts."""
        out = self._get("/v1/markets/timesales", {
            "symbol": symbol, "interval": "1min",
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end": end.strftime("%Y-%m-%d %H:%M"),
        })
        series = (out.get("series") or {}).get("data")
        if not series:
            return []
        return series if isinstance(series, list) else [series]

    def expirations(self, symbol):
        out = self._get("/v1/markets/options/expirations",
                        {"symbol": symbol, "includeAllRoots": "true"})
        exp = (out.get("expirations") or {}).get("date")
        if not exp:
            return []
        return exp if isinstance(exp, list) else [exp]

    def chain(self, symbol, expiration):
        out = self._get("/v1/markets/options/chains",
                        {"symbol": symbol, "expiration": expiration, "greeks": "false"})
        opts = ((out.get("options") or {}).get("option")) or []
        return opts if isinstance(opts, list) else [opts]

    def quote(self, symbol):
        out = self._get("/v1/markets/quotes", {"symbols": symbol})
        q = ((out.get("quotes") or {}).get("quote")) or {}
        return q if isinstance(q, dict) else {}
