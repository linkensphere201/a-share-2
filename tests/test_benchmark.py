from __future__ import annotations

import unittest
from datetime import date

from stock_harness.benchmarks.sqlite_hot_path import _summary, _weekdays


class BenchmarkHelpersTests(unittest.TestCase):
    def test_weekdays_skip_weekends(self) -> None:
        self.assertEqual(
            _weekdays(date(2026, 7, 31), 3),
            [date(2026, 7, 31), date(2026, 8, 3), date(2026, 8, 4)],
        )

    def test_summary_uses_nearest_rank_p95(self) -> None:
        summary = _summary([float(value) for value in range(1, 21)])
        self.assertEqual(summary["median"], 10.5)
        self.assertEqual(summary["p95"], 19.0)


if __name__ == "__main__":
    unittest.main()
