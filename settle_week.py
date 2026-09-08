#!/usr/bin/env python3
"""
settle_week.py - propose the two bonus winners for a completed week.

    python3 settle_week.py 8              # print the proposal, write nothing
    python3 settle_week.py 8 --apply      # also write it into bank-ledger.json
    python3 settle_week.py --auto --apply # work out which week, then settle it

Run after a week's games finish. --apply is what the Tuesday workflow uses.

TIES SPLIT AUTOMATICALLY. Every tied manager is recorded and the prize is
divided evenly between them - $25 two ways is $12.50 each. The run prints a
loud warning when it happens so it is never silent.

TWO THINGS IT WILL NOT DO
  - settle a week Yahoo has not marked final (use --force to override)
  - overwrite a week already in the ledger, ever

The second is the important one. A retried workflow, a manual run after an
automatic one, or a re-triggered Tuesday job must not pay anyone twice, and
must not undo a correction made by hand afterwards. To genuinely redo a week,
delete its key from bank-ledger.json first - a deliberate act.

Stat corrections land Tuesday and Wednesday and are not picked up: the ledger
is written once. They are rare; if one bites, edit the ledger by hand and
re-run pull_standings.py.

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


def settle(week, yf_dir=None, apply_it=False, force=False):
    meta = yc.load_categories()
    cat = meta["_by_week"].get(week)

    token = yc.get_token(yf_dir)
    sb_week, status, matchups = yc.parse_scoreboard(yc.fetch_scoreboard(token, week))
    if not matchups:
        sys.exit("No matchups returned for week %d." % week)

    standings = yc.parse_standings(yc.fetch_standings(token))
    # Dot Pot needs a starter's points at QB/RB/WR/TE every single week,
    # independent of whichever category is rotating in that week - so unlike
    # the old category-only logic, rosters are no longer conditional on
    # ROSTER_WEEKS. One extra call on weeks that didn't need it before.
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
    top = [h for h in high if h["rank"] == 1]
    if high:
        print("  HIGH SCORE  ($%d)  ->  %s  (%s)"
              % (meta["high_score"]["amount"],
                 ", ".join(h["manager"] for h in top), top[0]["value"]))
        if len(top) > 1:
            print("     !! tie - the $%d is split"
                  % meta["high_score"]["amount"])

    winners = []
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
                  % (l["rank"], l["manager"], l["value"], l["detail"]))
        if len(winners) > 1:
            print("     !! tie between %s - the $%d is split"
                  % (", ".join(w["manager"] for w in winners),
                     meta.get("category_amount", 25)))

    print("\n  DOT POT  ($%d/dot)" % yc.DOT_VALUE)
    dot_by_pos = yc.top_scorers_by_position(rosters)
    dots_entry = {}
    for pos in yc.DOT_POSITIONS:
        leaders = dot_by_pos.get(pos) or []
        pos_winners = [l for l in leaders if l["rank"] == 1]
        if not pos_winners:
            print("     %-3s  no starter found" % pos)
            dots_entry[pos] = None
            continue
        print("     %-3s  %-12s %8s   %s"
              % (pos, pos_winners[0]["manager"], pos_winners[0]["value"],
                 pos_winners[0]["detail"]))
        if len(pos_winners) > 1:
            print("          !! tie between %s - the $%d dot is split"
                  % (", ".join(w["manager"] for w in pos_winners), yc.DOT_VALUE))
        dots_entry[pos] = {"managers": [w["manager"] for w in pos_winners],
                           "value": pos_winners[0]["value"],
                           "detail": pos_winners[0]["detail"]}

    # The ledger carries the value and detail as well as the name: the
    # Weekly Winners card shows "Christian Watson 27.62" on the back, and
    # the DraftKings High Score column shows the winning score. Both read
    # from here, so a name alone isn't enough. This always prints now, even
    # on a week with no category bonus defined - it used to be nested
    # inside "if cat", so a category-less week silently printed nothing to
    # paste at all, not even High Score or Dot Pot.
    # "managers" is a list even when there is one winner, so a tie needs no
    # special case downstream - the money is divided between whoever is in it.
    entry = {
        "high": {"managers": [h["manager"] for h in top] if high else [],
                 "value": top[0]["value"] if high else "0.00"},
        "category": {"managers": [w["manager"] for w in winners],
                     "value": winners[0]["value"] if winners else "0.00",
                     "detail": winners[0]["detail"] if winners else ""},
        "dots": dots_entry,
    }
    any_tie = (len(winners) > 1 or (high and len(top) > 1) or
               any(len([l for l in (dot_by_pos.get(p) or []) if l["rank"] == 1]) > 1
                   for p in yc.DOT_POSITIONS))
    if any_tie:
        print("\n  !! TIE — the prize is split evenly between everyone listed\n"
              "     at rank 1 above. Recorded that way in the ledger.")

    if not apply_it:
        print("\n  Paste into bank-ledger.json under \"%d\":" % yc.SEASON)
        print('    "%d": %s\n' % (week, json.dumps(entry)))
        return

    if status != "final" and not force:
        sys.exit("\n  Week %d is %r, not 'final'. Nothing written.\n"
                 "  Re-run once the last game is over, or pass --force."
                 % (week, status))

    print("\n  " + yc.write_ledger_week(week, entry) + "\n")


def pick_week(token):
    """Which week to settle, without being told.

    Yahoo's current_week rolls over to the new week partway through Tuesday, so
    a Tuesday-morning job cannot simply trust it. Try current_week first, then
    the one before it, and settle the first that is genuinely final and not yet
    in the ledger. Returns None when there is nothing to do - the normal answer
    in the preseason, on a bye, and every Tuesday after the job has already run.
    """
    current = yc.current_week(token)
    settled, _awards = yc.load_ledger()
    for week in (current, current - 1):
        if week < 1 or week in settled:
            continue
        _w, status, matchups = yc.parse_scoreboard(yc.fetch_scoreboard(token, week))
        if matchups and status == "final":
            return week
    return None


if __name__ == "__main__":
    args = sys.argv[1:]
    apply_it = "--apply" in args
    force = "--force" in args
    auto = "--auto" in args

    yf = None
    if "--yf-dir" in args:
        i = args.index("--yf-dir")
        if i + 1 < len(args):
            yf = args[i + 1]

    weeks = [a for a in args if a.isdigit()]

    if auto:
        tok = yc.get_token(yf)
        target = pick_week(tok)
        if target is None:
            print("Nothing to settle: no finished, unsettled week.")
            sys.exit(0)
        settle(target, yf, apply_it, force)
    elif len(weeks) == 1:
        settle(int(weeks[0]), yf, apply_it, force)
    else:
        sys.exit("Usage: settle_week.py <week> [--apply] [--force] [--yf-dir DIR]\n"
                 "       settle_week.py --auto --apply")
