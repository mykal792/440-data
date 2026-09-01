#!/usr/bin/env python3
"""
settle_week.py — propose the two bonus winners for a completed week.

    python3 settle_week.py 8

Run once after a week's games finish. It prints a proposal; you paste the result
into bank-ledger.json. It deliberately does not write the ledger itself — a
bonus is real money and a tie or a judgment call should be a human decision.

WHAT IT CAN AND CAN'T DO
------------------------
Six of the fifteen categories, plus the weekly high score, come straight off the
scoreboard — one API call, fully automatic:

    high score      most points that week
    week 2   Match Up Blues   highest team score in a loss
    week 4   Lucky Duck       lowest team score in a win
    week 6   Photo Finish     narrowest margin of victory
    week 9   Blow Out         largest margin of victory
    week 10  Bad Beat         narrowest margin of defeat
    week 15  Points Against   highest season total points against

The other nine need player-level data — which player was in which slot and what
they scored — so each one costs ten more API calls, one roster per team:

    weeks 1, 3, 5, 7, 8, 11, 12, 13, 14

That's the reason this script is separate from pull_standings.py. Ten roster
calls once a week is nothing; ten roster calls every five minutes is a good way
to find out where Yahoo's undocumented rate limit actually sits.
"""

import sys
from collections import namedtuple

from pull_standings import SEASON, get_league, manager_name

SCOREBOARD_WEEKS = {2, 4, 6, 9, 10, 15}
ROSTER_WEEKS     = {1, 3, 5, 7, 8, 11, 12, 13, 14}

Side = namedtuple("Side", "manager points won margin")


def sides_for(league, week):
    """Flatten a week's matchups into one row per team."""
    out = []
    for m in league.scoreboard(week=week).matchups:
        a, b = m.teams.team
        pa, pb = float(a.team_points.total), float(b.team_points.total)
        out.append(Side(manager_name(a).upper(), pa, pa > pb, pa - pb))
        out.append(Side(manager_name(b).upper(), pb, pb > pa, pb - pa))
    return out


def propose(week):
    league = get_league()
    sides = sides_for(league, week)
    if not sides:
        sys.exit(f"No matchups returned for week {week}. Is the week finished?")

    top = max(sides, key=lambda s: s.points)
    tied_high = [s.manager for s in sides if s.points == top.points]

    print(f"\nWeek {week} — {SEASON}\n" + "─" * 46)
    for s in sorted(sides, key=lambda s: -s.points):
        print(f"  {s.manager:<12} {s.points:>7.2f}  {'W' if s.won else 'L'}  {s.margin:+7.2f}")

    print(f"\n  HIGH SCORE  ($15)  ->  {top.manager}  ({top.points:.2f})")
    if len(tied_high) > 1:
        print(f"  !! tie between {', '.join(tied_high)} — league call required")

    winners = [s for s in sides if s.won]
    losers  = [s for s in sides if not s.won]

    if week in SCOREBOARD_WEEKS:
        pick = {
            2:  lambda: max(losers,  key=lambda s: s.points),
            4:  lambda: min(winners, key=lambda s: s.points),
            6:  lambda: min(winners, key=lambda s: s.margin),
            9:  lambda: max(winners, key=lambda s: s.margin),
            10: lambda: max(losers,  key=lambda s: s.margin),
        }.get(week)
        if pick:
            s = pick()
            print(f"  CATEGORY    ($25)  ->  {s.manager}  "
                  f"({s.points:.2f}, margin {s.margin:+.2f})")
        else:  # week 15 is a season aggregate, not a single week
            print("  CATEGORY    ($25)  ->  needs season points-against totals; "
                  "read them off the standings page")
    elif week in ROSTER_WEEKS:
        print(f"  CATEGORY    ($25)  ->  needs rosters. Fetch each team's week-{week} "
              f"roster and compare the relevant slot:")
        print("       league.teams() -> team.roster(week) -> player.selected_position "
              "and player.player_points.total")
        print("       Bench categories use selected_position == 'BN'; the rest use "
              "the starting slot (QB/RB/WR/TE/K/DEF/W-R-T).")

    print(f"\n  Paste into bank-ledger.json under \"{SEASON}\":")
    print(f'    "{week}": {{ "high": "{top.manager}", "category": "?" }}\n')


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("Usage: python3 settle_week.py <week>")
    propose(int(sys.argv[1]))
