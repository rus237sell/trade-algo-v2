"""Live status: bot state + Alpaca paper account summary.

Usage: ~/trade-algo-v2/.venv/bin/python ~/trade-algo-v2/status.py
Paste the output in chat for a human-readable read.
"""
import json
import os
from datetime import datetime

import pytz
import requests

from alpaca import AlpacaAuth
from ledger import parse_lines, summarize
from reconcile import DIVERGENCE_THRESHOLD, divergence, realized_pnl

BASE = os.path.dirname(os.path.abspath(__file__))
PAPER = "https://paper-api.alpaca.markets"
ET = pytz.timezone("America/New_York")

print("== BOT ==")
try:
    with open(os.path.join(BASE, "state.json")) as f:
        st = json.load(f)
    print(f"as of {st['time']}")
    print(f"session={st['session']} | day_pnl={st['day_pnl']:+.2f} | "
          f"halted={st['halted']} | data_lag={st['data_lag_min']} min")
    for p in st["positions"]:
        print(f"  OPEN {p['symbol']} {p['side']} {p['qty']}x {p['occ']}")
    if not st["positions"]:
        print("  no open bot positions")
except FileNotFoundError:
    print("bot has not written state yet (not running?)")

print("== TRADES TODAY (local ledger) ==")
today = datetime.now(ET).date().isoformat()
ledger_net = None  # estimated net from the ledger, for reconciliation below
entries = exits = 0
net = 0.0
reasons = {}
all_records = []
try:
    with open(os.path.join(BASE, "trades.jsonl")) as f:
        all_records = parse_lines(f)
        for t in all_records:
            if not str(t.get("time", "")).startswith(today):
                continue
            if t.get("event") == "entry":
                entries += 1
            elif t.get("event") == "exit":
                exits += 1
                net += float(t.get("est_pnl") or 0)
                r = t.get("reason", "?")
                reasons[r] = reasons.get(r, 0) + 1
    ledger_net = net
    print(f"{entries} entries / {exits} exits | net est pnl {net:+.2f}")
    if reasons:
        print("  exits by reason: " + ", ".join(
            f"{r}x{n}" for r, n in sorted(reasons.items())))
    if entries + exits == 0:
        print("  no ledger entries today")
except FileNotFoundError:
    print("no trades logged yet")

print("== LEDGER SUMMARY (all time, estimates) ==")
_s = summarize(all_records)
if _s["exits"] == 0:
    print("  no priced exits in ledger yet — no expectancy to report")
else:
    _pf = f"{_s['profit_factor']:.2f}" if _s["profit_factor"] is not None else "n/a"
    _aw = f"{_s['avg_win']:+.2f}" if _s["avg_win"] is not None else "n/a"
    _al = f"{_s['avg_loss']:+.2f}" if _s["avg_loss"] is not None else "n/a"
    print(f"  {_s['entries']} entries / {_s['exits']} exits | "
          f"net {_s['net']:+.2f} | expectancy {_s['expectancy']:+.2f}/exit | "
          f"win rate {_s['win_rate']:.1%}")
    print(f"  avg win {_aw} | avg loss {_al} | profit factor {_pf}")
    _cs = _s['current_streak']
    _cs_txt = (f"{_cs}W" if _cs > 0 else
               f"{-_cs}L" if _cs < 0 else "flat")
    print(f"  max drawdown {_s['max_drawdown']:.2f} | "
          f"current drawdown {_s['current_drawdown']:.2f} | "
          f"longest loss streak {_s['longest_loss_streak']} | "
          f"current streak {_cs_txt}")
    for _label, _cuts in (("reason", _s["by_reason"]),
                          ("symbol", _s["by_symbol"])):
        for _k, _v in _cuts.items():
            print(f"  by {_label} {_k}: {_v['exits']} exits, "
                  f"net {_v['net']:+.2f}, expectancy {_v['expectancy']:+.2f}, "
                  f"win rate {_v['win_rate']:.1%}")
    if _s["recovered_exits"]:
        print(f"  note: {_s['recovered_exits']} exits were on recovered "
              f"positions (P&L measured from recovery price)")
    if _s["unpriced_exits"]:
        print(f"  WARNING: {_s['unpriced_exits']} exits have no est_pnl "
              f"and are excluded from these stats")

print("== ALPACA PAPER ==")
auth = AlpacaAuth()
if auth.dry:
    print("no keys in this shell's env")
else:
    h = auth.headers()
    acct = requests.get(f"{PAPER}/v2/account", headers=h, timeout=15).json()
    print(f"equity=${acct.get('equity')} buying_power=${acct.get('buying_power')}")
    poss = requests.get(f"{PAPER}/v2/positions", headers=h, timeout=15).json()
    for p in poss:
        print(f"  {p['symbol']} {p['qty']} uPL={float(p['unrealized_pl']):+.2f}")
    if not poss:
        print("  no open positions")
    print("-- recent orders --")
    # Fetch a wider window than we print: reconciliation pairs today's
    # sells against their buys, which may sit further back in the list.
    orders = requests.get(f"{PAPER}/v2/orders", headers=h, timeout=15,
                          params={"status": "closed", "limit": 100,
                                  "direction": "desc"}).json()
    for o in (orders or [])[:10]:
        print(f"  {str(o.get('created_at'))[:16]} {o.get('symbol')} "
              f"{o.get('side')} {o.get('qty')} {o.get('status')} "
              f"fill={o.get('filled_avg_price')}")

    print("== RECONCILIATION TODAY (fills vs ledger estimates) ==")
    rec = realized_pnl(orders, on_date=datetime.now(ET).date())
    if rec["pairs"] == 0 and rec["unmatched_sell_qty"] == 0:
        print("  no closed fills today to reconcile")
    else:
        print(f"  broker realized (fills): {rec['realized']:+.2f} "
              f"on {rec['closed_qty']:g} closed contracts")
        if rec["unmatched_sell_qty"]:
            print(f"  WARNING: {rec['unmatched_sell_qty']:g} sold contracts "
                  f"had no buy in the fetched window — realized is partial")
        if ledger_net is None:
            print("  no local ledger for today — nothing to compare")
        else:
            diff = divergence(ledger_net, rec["realized"])
            print(f"  ledger est: {ledger_net:+.2f} | "
                  f"broker - ledger: {diff:+.2f}")
            if abs(diff) > DIVERGENCE_THRESHOLD:
                print(f"  DIVERGENCE over ${DIVERGENCE_THRESHOLD:g}: the "
                      f"daily guard halts on estimates — check fills "
                      f"before trusting a halt")
