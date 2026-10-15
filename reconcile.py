"""Ledger-vs-broker reconciliation: what did fills actually make?

The bot's daily guard runs on estimated P&L (underlying move x delta),
which can diverge from real fill P&L — on 2026-10-09 the bot halted on
its own estimate while broker fills totaled a smaller loss. This module
pairs broker buy/sell fills FIFO per contract and computes realized
P&L from actual filled_avg_price values, so status.py can print the
estimate and the fill-based number side by side and flag divergence.

Pure functions, no network: callers pass Alpaca order dicts in.
Reporting only — nothing here changes trading or guard behavior.
"""
from collections import defaultdict, deque
from datetime import datetime

import pytz

ET = pytz.timezone("America/New_York")

# Flag estimate-vs-fill gaps larger than this (dollars) in status.py.
DIVERGENCE_THRESHOLD = 25.0


def _fill_time(order):
    """Best timestamp for a fill as an aware datetime, or None."""
    raw = order.get("filled_at") or order.get("created_at")
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=pytz.utc)


def _filled_qty(order):
    """Contracts that actually filled (0 for unfilled/canceled orders)."""
    price = order.get("filled_avg_price")
    if price is None:
        return 0.0
    qty = order.get("filled_qty", order.get("qty"))
    try:
        return float(qty or 0)
    except (TypeError, ValueError):
        return 0.0


def realized_pnl(orders, on_date=None):
    """Realized P&L from pairing buy and sell fills FIFO per symbol.

    orders: Alpaca-style dicts (symbol, side, qty/filled_qty,
    filled_avg_price, filled_at/created_at). Only orders with a fill
    price and filled quantity count. When on_date (a datetime.date,
    ET) is given, only sells filled that day contribute P&L, but all
    fills still pair in chronological order so the FIFO queues stay
    correct for sells inside the window.

    Returns {"realized": dollars, "pairs": matched sell lots,
    "closed_qty": contracts closed in the window,
    "unmatched_sell_qty": contracts sold with no buy in the input —
    the caller fetched too small a window to reconcile those}.
    """
    fills = []
    for o in orders or []:
        qty = _filled_qty(o)
        t = _fill_time(o)
        if qty <= 0 or t is None or o.get("side") not in ("buy", "sell"):
            continue
        fills.append((t, o, qty))
    fills.sort(key=lambda x: x[0])  # API returns newest first; pair oldest first

    books = defaultdict(deque)  # symbol -> deque of [remaining_qty, price]
    realized, pairs, closed_qty, unmatched = 0.0, 0, 0.0, 0.0
    for t, o, qty in fills:
        symbol = o.get("symbol", "")
        price = float(o["filled_avg_price"])
        if o["side"] == "buy":
            books[symbol].append([qty, price])
            continue
        # sell: close against the oldest open buys for this contract
        in_window = on_date is None or t.astimezone(ET).date() == on_date
        remaining = qty
        while remaining > 0 and books[symbol]:
            lot_qty, lot_price = books[symbol][0]
            matched = min(lot_qty, remaining)
            if in_window:
                realized += (price - lot_price) * 100 * matched
                closed_qty += matched
                pairs += 1
            lot_qty -= matched
            remaining -= matched
            if lot_qty <= 0:
                books[symbol].popleft()
            else:
                books[symbol][0][0] = lot_qty
        if remaining > 0 and in_window:
            unmatched += remaining
    return {"realized": round(realized, 2), "pairs": pairs,
            "closed_qty": closed_qty, "unmatched_sell_qty": unmatched}


def divergence(ledger_net, broker_realized):
    """Signed gap between fill-based and estimated P&L (broker - ledger)."""
    return round(broker_realized - ledger_net, 2)
