#!/usr/bin/env python3
"""probe_stats.py - why are Top Players stat lines empty?

Every player in top-players.json comes back with "stats": [], so the Dot Pot
board shows points and nothing else. Two things could cause that and this
tells you which, in one run:

  1. parse_stat_categories() may not read Yahoo's payload correctly. Its own
     docstring says the shape was modeled on other resources, never verified.

  2. STAT_NAME_BUCKETS maps Yahoo's category NAMES to your abbreviations, and
     those names were copied from Yahoo's help page rather than from this
     league's actual response. One wrong plural and that bucket silently
     never populates.

Prints the real category table, every bucket name that does NOT match it, and
one real player's raw stat block so you can see the ids actually being sent.

    python3 probe_stats.py
    python3 probe_stats.py --week 2 --team 3
    python3 probe_stats.py --write        # save stat-categories.json

Read-only unless --write is passed.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int)
    ap.add_argument("--team", default=None)
    ap.add_argument("--yf-dir")
    ap.add_argument("--write", action="store_true",
                    help="write stat-categories.json so runs stop refetching")
    args = ap.parse_args()

    token = yc.get_token(args.yf_dir)
    week = args.week or yc.current_week(token)
    team_id = args.team or list(yc.TEAM_MAP)[0]

    # --- 1. does the parser read the payload? ---------------------------
    print("=" * 62)
    print("1. STAT CATEGORY TABLE  (game/%s/stat_categories)" % yc.game_key())
    print("=" * 62)
    payload = yc.fetch_stat_categories(token)
    id_to_name = yc.parse_stat_categories(payload)

    if not id_to_name:
        print("  PARSER RETURNED NOTHING - the payload shape differs from what")
        print("  parse_stat_categories() expects. Top-level keys were:")
        print("    %s" % list(payload.get("fantasy_content", {}).keys()))
        print("\n  raw (first 1200 chars):")
        print(json.dumps(payload)[:1200])
        return

    print("  parsed %d categories" % len(id_to_name))
    for sid, name in sorted(id_to_name.items(), key=lambda kv: int(kv[0])):
        print("    %4s  %s" % (sid, name))

    # --- 2. do the bucket names match those? ----------------------------
    print()
    print("=" * 62)
    print("2. STAT_NAME_BUCKETS vs the real names")
    print("=" * 62)
    real = set(id_to_name.values())
    buckets = yc.resolve_stat_buckets(id_to_name)
    for abbrev in yc.STAT_ORDER:
        wanted = yc.STAT_NAME_BUCKETS.get(abbrev) or ()
        hits = buckets.get(abbrev) or set()
        misses = [n for n in wanted if n not in real]
        flag = "ok  " if hits else "MISS"
        print("  %s %-9s -> ids %s" % (flag, abbrev,
                                       sorted(hits) if hits else "(none)"))
        for n in misses:
            print("         no category named %r" % n)

    empty = [a for a in yc.STAT_ORDER if not (buckets.get(a) or set())]
    print("\n  %d of %d buckets resolved; empty: %s"
          % (len(yc.STAT_ORDER) - len(empty), len(yc.STAT_ORDER),
             ", ".join(empty) or "none"))

    # --- 3. what does a real player's stat block look like? -------------
    print()
    print("=" * 62)
    print("3. A REAL PLAYER  (team %s, week %d)" % (team_id, week))
    print("=" * 62)
    roster = yc.fetch(
        "team/%s.t.%s/roster/players/stats;type=week;week=%d"
        % (yc.LEAGUE_KEY, team_id, week), token)
    parsed = yc.parse_rosters({team_id: roster})
    players = []
    for team in parsed.values():
        players = sorted(team["players"], key=lambda p: -p["points"])
    if not players:
        print("  no players parsed - parse_rosters() found nothing")
        return

    p = players[0]
    print("  %s (%s) %.2f pts" % (p["name"], p.get("display_position"), p["points"]))
    raw = p.get("raw_stats") or {}
    if not raw:
        print("  raw_stats EMPTY - _parse_stat_block() is not finding the")
        print("  player_stats block, so no mapping could ever work.")
    else:
        print("  raw_stats (%d ids):" % len(raw))
        for sid, v in sorted(raw.items(), key=lambda kv: int(kv[0])):
            print("    %4s = %-8s %s" % (sid, v, id_to_name.get(sid, "?")))
        print("\n  summarize_stats() gives: %s"
              % yc.summarize_stats(raw, buckets))

    if args.write and id_to_name:
        yc.STAT_CATEGORIES_FILE.write_text(json.dumps(id_to_name, indent=2) + "\n")
        print("\nwrote %s - commit it so runs stop refetching."
              % yc.STAT_CATEGORIES_FILE)


if __name__ == "__main__":
    main()
