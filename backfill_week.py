#!/usr/bin/env python3
"""backfill_week.py - write a past week into docs/weeks/ after the fact.

The archive only started when update_week_archive() went in, so any week that
settled before that has no snapshot and can't be browsed. Yahoo still has the
data, so this rebuilds one.

    python3 backfill_week.py 1 --dry-run     # show what it would write
    python3 backfill_week.py 1               # write docs/weeks/w1.json

SAFE BY DESIGN: this only ever touches docs/weeks/wN.json and
docs/weeks/index.json. It does NOT write scoreboard.json, bonus.json or
top-players.json, so the live board keeps showing the current week while this
runs. Running pull_live.py --week 1 would have clobbered all three.

It also refuses to overwrite an existing snapshot unless you pass --force. A
week that was archived while it was live is the real record; this is a
reconstruction, and the real thing wins.

ONE ACCURACY NOTE. pull_live.py fetches rosters as:

    team/<key>/roster/players/stats;type=week;week=N

which is TODAY's roster with week N's stats - correct while N is the current
week, wrong afterwards, because waiver moves and drops have happened since.
A bench bonus computed that way would score players who weren't on that bench.
This script asks for the roster AS IT WAS instead:

    team/<key>/roster;week=N/players/stats;type=week;week=N

Matchups, scores and standings come straight from the week's scoreboard, so
those are exact either way.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc
import pull_live as pl


def fetch_rosters_as_they_were(token, week):
    """Ten rosters as they stood in `week`, not as they stand today."""
    out = {}
    for team_id in yc.TEAM_MAP:
        out[team_id] = yc.fetch(
            "team/%s.t.%s/roster;week=%d/players/stats;type=week;week=%d"
            % (yc.LEAGUE_KEY, team_id, week, week), token)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("week", type=int, help="week number to rebuild")
    ap.add_argument("--out-dir", default="docs")
    ap.add_argument("--categories", default="bonus-categories.json")
    ap.add_argument("--yf-dir")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing snapshot")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    week = args.week
    out_dir = Path(args.out_dir)
    target = out_dir / "weeks" / ("w%d.json" % week)

    if target.exists() and not args.force:
        print("%s already exists. It was written while the week was live, "
              "which beats a reconstruction. Pass --force to replace it."
              % target)
        return

    token = yc.get_token(args.yf_dir)
    meta = yc.load_categories(args.categories)

    print("rebuilding week %d from Yahoo..." % week)
    rosters_raw = fetch_rosters_as_they_were(token, week)
    sb_raw = yc.fetch_scoreboard(token, week)
    standings_raw = yc.fetch_standings(token)

    rosters = yc.parse_rosters(rosters_raw)
    standings = yc.parse_standings(standings_raw)
    sb_week, status, matchups = yc.parse_scoreboard(sb_raw)
    projected = yc.parse_projected(sb_raw)
    remaining = yc.remaining_starters(sb_raw, rosters)

    stat_categories = yc.fetch_stat_categories(token)
    stat_buckets = yc.resolve_stat_buckets(stat_categories)

    print("  week %d | status %s | %d managers | %d matchups"
          % (sb_week or week, status, len(rosters), len(matchups)))
    if status != "final":
        print("  ! Yahoo does not call this week final yet - the snapshot will")
        print("    say '%s'. Re-run once it settles." % status)

    scoreboard = pl.build_scoreboard(week, status, matchups, projected)
    bonus = pl.build_bonus(week, status, rosters, matchups, standings, meta,
                           True, projected, remaining)
    top_players = pl.build_top_players(week, status, rosters, stat_buckets)

    for label, race in (("category", bonus["live"]["category"]),
                        ("high", bonus["live"]["high"])):
        top = (race.get("leaders") or [{}])[0]
        print("  %-8s %-14s %s %s" % (label, race.get("label", "?"),
                                      top.get("manager", "-"),
                                      top.get("value", "-")))

    pl.update_week_archive(out_dir, week, scoreboard, bonus, top_players,
                           args.dry_run)
    if args.dry_run:
        print("\n--dry-run: nothing written. Snapshot would be:")
        print(json.dumps({"week": week, "status": status,
                          "matchups": scoreboard["matchups"]}, indent=2)[:900])
    else:
        print("\nwrote %s and refreshed the index." % target)
        print("Commit docs/weeks/ and the arrows will find it.")


if __name__ == "__main__":
    main()
