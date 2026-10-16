#!/usr/bin/env python3
"""
Add a late-recorded deposit/withdrawal to past history points.

history.json is built from holdings.json as it was on each day, so a deposit only shows up
in the portfolio line once holdings are updated. The benchmark applies flows on their real
date, which makes the two lines jump on different days. This shifts the history points
from the flow date up to (not including) the day holdings were updated.

Run: python scripts/backfill_flow.py 2026-10-12 1000 2026-10-16
     (flow date, amount in EUR, first date that already includes it; default = today)
"""
import json
import sys
from datetime import date
from pathlib import Path

HISTORY_F = Path(__file__).resolve().parent.parent / "data" / "history.json"


def main() -> None:
    flow_date, amount = sys.argv[1], float(sys.argv[2])
    until = sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat()
    history = json.loads(HISTORY_F.read_text())
    n = 0
    for p in history:
        if flow_date <= p["date"] < until:
            p["total_eur"] = round(p["total_eur"] + amount, 2)
            n += 1
    HISTORY_F.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Shifted {n} points by {amount:+.2f} EUR ({flow_date} .. {until})")


if __name__ == "__main__":
    main()
