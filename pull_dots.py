#!/usr/bin/env python3
"""
pull_dots.py - 440 & Friends, Dot Pot leaderboard.

Writes docs/dots.json, overwritten every run.

$5 to whoever has the week's highest-scoring STARTER at QB, RB, WR, and TE.
Like High Score and the weekly bonus category, this is never computed live -
it's a straight read of bank-ledger.json's "dots" entries, settled by hand
(see settle_week.py / yc.top_scorers_by_position for the candidate winners a
human reviews before pasting them into the ledger).

Because it only reads the ledger, this script makes NO Yahoo API call and
needs no token - it can run as its own step with no auth setup.

    python3 pull_dots.py                       # -> docs/dots.json
    python3 pull_dots.py --dry-run
    python3 pull_dots.py --ledger ~/bank-ledger.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="docs")
    ap.add_argument("--ledger", help="path to bank-ledger.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    weeks, _awards = yc.load_ledger(args.ledger)
    payload = yc.dot_pot_summary(weeks)

    settled = len(payload["history"])
    print("%d week(s) settled, $%.2f paid of $%.2f pot"
          % (settled, payload["pot"]["paid_out"], payload["pot"]["total"]))

    text = json.dumps(payload, indent=2) + "\n"
    out = Path(args.out_dir).expanduser().resolve() / "dots.json"
    if args.dry_run:
        print("--- would write %s ---" % out)
        print(text[:1800])
        return
    tmp = str(out) + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, out)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
