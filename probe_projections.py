#!/usr/bin/env python3
"""probe_projections.py - does Yahoo return per-player projections?

The public API documents projections only at the TEAM level
(team_projected_points, which pull_live.py already uses). Per-player
projections are all over the Yahoo site, so the question is whether they ride
along undocumented in a payload we can already fetch - the same way
player_points does on the per-team roster form but not the league-wide one.

This asks the API directly instead of guessing. It fetches a few payload
variants and walks the ENTIRE json tree for any key, or any string value,
containing "project" - so it finds the field under any nesting or spelling.

    python3 probe_projections.py                 # current week, team 1
    python3 probe_projections.py --week 3 --team 4
    python3 probe_projections.py --dump out.json # save the roster payload

Nothing is written unless --dump is passed. No files in docs/ are touched.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc


def walk(node, path=""):
    """Yield (path, key, value) for every dict key mentioning 'project'."""
    if isinstance(node, dict):
        for k, v in node.items():
            here = "%s.%s" % (path, k)
            if "project" in str(k).lower():
                yield here, k, v
            elif isinstance(v, str) and "project" in v.lower():
                yield here, k, v
            yield from walk(v, here)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, "%s[%d]" % (path, i))


def probe(label, path, token):
    print("\n=== %s" % label)
    print("    %s" % path)
    try:
        payload = yc.fetch(path, token)
    except SystemExit as err:
        # yahoo_common.fetch() calls sys.exit() on any non-200, and SystemExit
        # is not an Exception subclass - so a single 400 used to abort the
        # whole probe before the later variants ran.
        print("    rejected by Yahoo: %s" % str(err).splitlines()[0])
        return None
    except Exception as err:                      # noqa: BLE001
        print("    request failed: %s" % err)
        return None

    hits = list(walk(payload))
    if not hits:
        print("    no 'project' anywhere in this payload")
        return payload

    print("    %d hit(s):" % len(hits))
    for where, key, value in hits[:25]:
        shown = json.dumps(value)
        if len(shown) > 160:
            shown = shown[:160] + "..."
        print("      %s = %s" % (where, shown))
    if len(hits) > 25:
        print("      ... and %d more" % (len(hits) - 25))
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="week number (default: current)")
    ap.add_argument("--team", default=None, help="team id (default: first in TEAM_MAP)")
    ap.add_argument("--yf-dir", help="directory holding .yahoofantasy")
    ap.add_argument("--dump", help="write the plain roster payload here")
    args = ap.parse_args()

    token = yc.get_token(args.yf_dir)
    week = args.week or yc.current_week(token)
    team_id = args.team or list(yc.TEAM_MAP)[0]
    tk = "%s.t.%s" % (yc.LEAGUE_KEY, team_id)

    print("league %s | team %s | week %d" % (yc.LEAGUE_KEY, team_id, week))
    print("looking for any key or value containing 'project'")

    # 1. exactly the call pull_live.py already makes
    roster = probe(
        "roster as fetched today",
        "team/%s/roster/players/stats;type=week;week=%d" % (tk, week),
        token)

    # 2. same call, asking for projections as an extra sub-resource
    probe("roster with ;out=projected_points",
          "team/%s/roster/players/stats;type=week;week=%d;out=projected_points"
          % (tk, week), token)

    # 3. bare roster, in case projections hang off selected_position
    probe("bare roster, no stats subresource",
          "team/%s/roster;week=%d" % (tk, week), token)

    # 4. week on the roster resource itself, stats nested under it
    probe("roster;week=N with nested stats",
          "team/%s/roster;week=%d/players/stats;type=week;week=%d"
          % (tk, week, week), token)

    # 5. every player subresource at once - if a projection rides along on any
    #    of the documented ones, it shows up here
    probe("roster with all player subresources",
          "team/%s/roster/players/stats;type=week;week=%d"
          ";out=stats,ownership,percent_owned,draft_analysis" % (tk, week), token)

    # 6. season-long coverage, in case weekly is the only gap
    probe("player stats, season coverage",
          "team/%s/roster/players/stats;type=season" % tk, token)

    # 7. CONTROL - team_projected_points is documented here and pull_live.py
    #    already reads it. If this finds nothing, the probe is broken and the
    #    negatives above mean nothing.
    probe("team stats (control - should find team_projected_points)",
          "team/%s/stats;type=week;week=%d" % (tk, week), token)

    # 8. CONTROL - the scoreboard call pull_live.py makes every run
    probe("league scoreboard (control - should find team_projected_points)",
          "league/%s/scoreboard;week=%d" % (yc.LEAGUE_KEY, week), token)

    if args.dump and roster:
        Path(args.dump).write_text(json.dumps(roster, indent=2))
        print("\nwrote %s" % args.dump)


if __name__ == "__main__":
    main()
