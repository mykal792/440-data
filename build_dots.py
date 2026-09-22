#!/usr/bin/env python3
"""
build_dots.py - rebuild docs/dots.json from bank-ledger.json.

    python3 build_dots.py

The Dot Pot leaderboard is pure arithmetic on the ledger: who won QB/RB/WR/TE
each settled week, the season totals, and what is left in the $300 pot. It makes
NO network calls, so it cannot fail because Yahoo had a bad moment.

It used to be written at the very end of pull_live.py, after a dozen-plus Yahoo
calls. That meant the dot pot only refreshed if every one of those succeeded,
and never refreshed at all from the weekly button, which doesn't run pull_live.
In week 2 the ledger had the new winners while dots.json sat a week behind.

Runs in both workflows right after settle, and rewrites the file only when the
contents actually change.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc

OUT = Path(__file__).resolve().parent / "docs" / "dots.json"


def main():
    weeks, _awards = yc.load_ledger()
    summary = yc.dot_pot_summary(weeks)

    # Only rewrite when something changed - otherwise a fresh timestamp alone
    # would commit this file on every run.
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            strip = lambda d: {k: v for k, v in d.items() if k != "updated"}
            if strip(old) == strip(summary):
                print("dots.json unchanged (%d settled week(s))."
                      % len(summary.get("history", [])))
                return
        except (ValueError, OSError):
            pass  # unreadable - just rewrite it

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, OUT)
    pot = summary.get("pot", {})
    print("wrote %s: %d settled week(s), $%s paid out, $%s left."
          % (OUT, len(summary.get("history", [])),
             pot.get("paid_out"), pot.get("remaining")))


if __name__ == "__main__":
    main()
