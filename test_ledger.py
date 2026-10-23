"""Unit tests for ledger.py — expectancy, drawdown, and streak stats.

Run: python -m unittest test_ledger -v
All expected values are hand-computed from the fixture P&Ls.
"""
import unittest

from ledger import parse_lines, summarize


def exits(pnls, **kw):
    return [{"event": "exit", "est_pnl": p, **kw} for p in pnls]


class TestPathStats(unittest.TestCase):
    def test_empty(self):
        s = summarize([])
        self.assertEqual(s["exits"], 0)
        self.assertEqual(s["max_drawdown"], 0.0)
        self.assertEqual(s["current_drawdown"], 0.0)
        self.assertEqual(s["longest_win_streak"], 0)
        self.assertEqual(s["longest_loss_streak"], 0)
        self.assertEqual(s["current_streak"], 0)
        self.assertIsNone(s["profit_factor"])

    def test_drawdown_and_recovery(self):
        # cum: 100, 50, -30, 170, 140 ; peaks: 100,100,100,170,170
        s = summarize(exits([100, -50, -80, 200, -30]))
        self.assertEqual(s["net"], 140.0)
        self.assertEqual(s["max_drawdown"], 130.0)
        self.assertEqual(s["current_drawdown"], 30.0)
        self.assertEqual(s["expectancy"], 28.0)
        self.assertEqual(s["profit_factor"], 1.88)  # 300 / 160

    def test_all_wins_no_drawdown(self):
        s = summarize(exits([10, 20, 30]))
        self.assertEqual(s["max_drawdown"], 0.0)
        self.assertEqual(s["current_drawdown"], 0.0)
        self.assertEqual(s["longest_win_streak"], 3)
        self.assertEqual(s["current_streak"], 3)
        self.assertIsNone(s["profit_factor"])  # no losses: never inf

    def test_drawdown_from_zero_peak(self):
        # Losses before any profit draw down from the 0 starting peak.
        s = summarize(exits([-40, -60, 25]))
        self.assertEqual(s["max_drawdown"], 100.0)
        self.assertEqual(s["current_drawdown"], 75.0)
        self.assertEqual(s["longest_loss_streak"], 2)
        self.assertEqual(s["current_streak"], 1)

    def test_streaks_and_breakeven_break(self):
        s = summarize(exits([5, 5, -1, -2, -3, 0, -7]))
        self.assertEqual(s["longest_win_streak"], 2)
        self.assertEqual(s["longest_loss_streak"], 3)
        self.assertEqual(s["current_streak"], -1)  # breakeven broke the run
        self.assertEqual(s["breakeven"], 1)

    def test_same_net_different_path(self):
        # The point of v2.10: identical net/expectancy, different risk.
        grind = summarize(exits([10, 10, 10, 10]))
        hole = summarize(exits([100, -90, 20, 10]))
        self.assertEqual(grind["net"], hole["net"])
        self.assertEqual(grind["max_drawdown"], 0.0)
        self.assertEqual(hole["max_drawdown"], 90.0)


class TestSummarize(unittest.TestCase):
    def test_entries_unpriced_recovered(self):
        recs = ([{"event": "entry"}, {"event": "entry"}]
                + exits([50], recovered=True)
                + [{"event": "exit"}, {"event": "exit", "est_pnl": "x"}]
                + exits([-20]))
        s = summarize(recs)
        self.assertEqual(s["entries"], 2)
        self.assertEqual(s["exits"], 2)
        self.assertEqual(s["unpriced_exits"], 2)
        self.assertEqual(s["recovered_exits"], 1)
        self.assertEqual(s["net"], 30.0)

    def test_by_reason_and_symbol_carry_path_stats(self):
        recs = (exits([100, -40], reason="atr-stop", symbol="QQQ")
                + exits([-10, -10], reason="atr-target", symbol="SPY"))
        s = summarize(recs)
        self.assertEqual(s["by_symbol"]["QQQ"]["max_drawdown"], 40.0)
        self.assertEqual(s["by_symbol"]["SPY"]["max_drawdown"], 20.0)
        self.assertEqual(s["by_symbol"]["SPY"]["current_streak"], -2)
        self.assertEqual(s["by_reason"]["atr-stop"]["net"], 60.0)

    def test_parse_lines_skips_corrupt(self):
        lines = ['{"event": "entry"}', "", "{bad json", "[1,2]",
                 '{"event": "exit", "est_pnl": 5}']
        recs = parse_lines(lines)
        self.assertEqual(len(recs), 2)  # list payload is not a record
        self.assertEqual(summarize(recs)["net"], 5.0)


if __name__ == "__main__":
    unittest.main()
