#!/usr/bin/env python3
"""
settle_week.py - propose the two bonus winners for a completed week.

    python3 settle_week.py 8

Run once after a week's games finish. It prints a proposal; you paste the result
into bank-ledger.json. It deliberately does not write the ledger itself - a
bonus is real money, and a tie or a judgment call should be a human decision.

FIXED 2026-09-01:
  - imported manager_name from pull_standings, which never defined it. The
    script could not run at all. Shared code now lives in yahoo_common.
  - the old docstring said the nine player-level categories cost ten API calls
    each, one roster per team. They don't:
        league/<key>/teams/roster/players/stats;type=week;week=N
    returns all ten rosters in a single call. Every category is now computed,
    including the nine that used to be left as a manual instruction.

All fifteen categories fall inside the regular season (playoffs start week 16),
so every week has all five matchups.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc

RULE = "-" * 52


def settle(week, yf_dir=None):
    meta = yc.load_categories()
    cat = meta["_by_week"].get(week)

    token = yc.get_token(yf_dir)
    sb_week, status, matchups = yc.parse_scoreboard(yc.fetch_scoreboard(token, week))
    if not matchups:
        sys.exit("No matchups returned for week %d." % week)

    standings = yc.parse_standings(yc.fetch_standings(token))
    rosters = {}
    if week in yc.ROSTER_WEEKS:
        rosters = yc.parse_rosters(yc.fetch_rosters(token, week))

    print("\nWeek %d - %d" % (week, yc.SEASON))
    print(RULE)
    if status != "final":
        print("  ! week status is %r, not 'final'. Numbers may still move.\n"
              % status)

    sides = []
    for m in matchups:
        (hm, hs), (am, asc) = m["home"], m["away"]
        sides.append((hm, hs, hs > asc, round(hs - asc, 2)))
        sides.append((am, asc, asc > hs, round(asc - hs, 2)))

    for manager, points, won, margin in sorted(sides, key=lambda s: -s[1]):
        print("  %-12s %7.2f  %s  %+7.2f"
              % (yc.DISPLAY.get(manager, manager), points,
                 "W" if won else "L", margin))

    print()
    high = yc.rank_rows(yc.high_score_rows(rosters, matchups, standings), limit=1)
    if high:
        top = [h for h in high if h["rank"] == 1]
        print("  HIGH SCORE  ($%d)  ->  %s  (%s)"
              % (meta["high_score"]["amount"],
                 ", ".join(h["name"] for h in top), top[0]["value"]))
        if len(top) > 1:
            print("     !! tie - the $%d is split"
                  % meta["high_score"]["amount"])

    if not cat:
        print("  CATEGORY    ->  no bonus defined for week %d" % week)
    else:
        rows, ascending = yc.compute_bonus(week, rosters, matchups, standings)
        leaders = yc.rank_rows(rows, ascending=ascending)
        winners = [l for l in leaders if l["rank"] == 1]
        print("\n  %s ($%d) - %s"
              % (cat["label"].upper(), meta.get("category_amount", 25),
                 cat["description"]))
        if not leaders:
            print("     no result - check the week is finished")
        for l in leaders:
            print("     %d. %-12s %8s   %s"
                  % (l["rank"], l["name"], l["value"], l["detail"]))
        if len(winners) > 1:
            print("     !! tie between %s - the $%d is split"
                  % (", ".join(w["name"] for w in winners),
                     meta.get("category_amount", 25)))

        # The ledger carries the value and detail as well as the name: the
        # Weekly Winners card shows "Christian Watson 27.62" on the back, and
        # the DraftKings High Score column shows the winning score. Both read
        # from here, so a name alone isn't enough.
        entry = {
            "high": {"manager": top[0]["name"] if high else "?",
                     "value": top[0]["value"] if high else "0.00"},
            "category": {"manager": winners[0]["name"] if winners else "?",
                         "value": winners[0]["value"] if winners else "0.00",
                         "detail": winners[0]["detail"] if winners else ""},
        }
        print("\n  Paste into bank-ledger.json under \"%d\":" % yc.SEASON)
        print('    "%d": %s\n' % (week, json.dumps(entry)))
        if len(winners) > 1 or (high and len(top) > 1):
            print("  (tie - record the split however the league decides; the\n"
                  "   bank totals come straight off this file)\n")


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    yf = None
    for i, a in enumerate(sys.argv):
        if a == "--yf-dir" and i + 1 < len(sys.argv):
            yf = sys.argv[i + 1]
            argv = [x for x in argv if x != yf]
    if len(argv) != 1 or not argv[0].isdigit():
        sys.exit("Usage: python3 settle_week.py <week> [--yf-dir DIR]")
    settle(int(argv[0]), yf)
