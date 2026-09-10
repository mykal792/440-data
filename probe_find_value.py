#!/usr/bin/env python3
"""probe_find_value.py - hunt for a number you can see on Yahoo's site.

Yahoo's matchup header shows two projections:

    Orig Proj   118.93   112.82     <- this is team_projected_points
    Live Proj   118.93    95.92     <- where does THIS come from?

Guessing field names failed, so this works backwards: give it the Live Proj
value you can see on the site and it searches every payload we can reach for
that number, reporting the exact path if it turns up.

    python3 probe_find_value.py --value 95.92
    python3 probe_find_value.py --value 95.92 --team 2
    python3 probe_find_value.py --value 95.92 --dump-dir ./payloads

Run it WHILE the site shows that value - projections move, and a stale target
will not match. Read-only; writes nothing unless --dump-dir is given.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc


def find_value(node, target, path=""):
    """Yield paths where `target` appears as a value, number or string."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from find_value(v, target, "%s.%s" % (path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from find_value(v, target, "%s[%d]" % (path, i))
    else:
        try:
            if node is not None and abs(float(node) - target) < 0.005:
                yield path, node
        except (TypeError, ValueError):
            pass


def scan(label, payload, target, dump_dir=None, name=None):
    if payload is None:
        print("  %-42s (no payload)" % label)
        return
    hits = list(find_value(payload, target))
    if hits:
        print("  %-42s %d HIT(S)" % (label, len(hits)))
        for where, raw in hits[:10]:
            print("      %s = %s" % (where, raw))
    else:
        print("  %-42s -" % label)
    if dump_dir and name:
        Path(dump_dir).mkdir(parents=True, exist_ok=True)
        (Path(dump_dir) / name).write_text(json.dumps(payload, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--value", type=float, required=True,
                    help="the Live Proj number shown on Yahoo right now")
    ap.add_argument("--team", default=None, help="team id that value belongs to")
    ap.add_argument("--week", type=int)
    ap.add_argument("--yf-dir")
    ap.add_argument("--dump-dir", help="save every payload here for eyeballing")
    args = ap.parse_args()

    token = yc.get_token(args.yf_dir)
    week = args.week or yc.current_week(token)
    team_id = args.team or list(yc.TEAM_MAP)[0]
    tk = "%s.t.%s" % (yc.LEAGUE_KEY, team_id)

    print("looking for %.2f | league %s | team %s | week %d"
          % (args.value, yc.LEAGUE_KEY, team_id, week))
    print()

    def get(path):
        try:
            return yc.fetch(path, token)
        except SystemExit as err:
            print("      rejected: %s" % str(err).splitlines()[0])
            return None
        except Exception as err:                    # noqa: BLE001
            print("      failed: %s" % err)
            return None

    targets = [
        ("scoreboard", "league/%s/scoreboard;week=%d" % (yc.LEAGUE_KEY, week),
         "scoreboard.json"),
        ("scoreboard;out=stats", "league/%s/scoreboard;week=%d;out=stats"
         % (yc.LEAGUE_KEY, week), "scoreboard_stats.json"),
        ("matchups on the team", "team/%s/matchups;weeks=%d" % (tk, week),
         "team_matchups.json"),
        ("team stats", "team/%s/stats;type=week;week=%d" % (tk, week),
         "team_stats.json"),
        ("team, all subresources",
         "team/%s;out=stats,roster,matchups,standings" % tk, "team_all.json"),
        ("roster with stats", "team/%s/roster/players/stats;type=week;week=%d"
         % (tk, week), "roster.json"),
    ]

    for label, path, fname in targets:
        scan(label, get(path), args.value, args.dump_dir, fname)

    print()
    print("A hit names the field to read. Nothing anywhere means Yahoo's site")
    print("computes Live Proj itself from per-player projections the API does")
    print("not expose - in which case it cannot be reproduced from this data.")
    if args.dump_dir:
        print("payloads saved to %s" % args.dump_dir)


if __name__ == "__main__":
    main()
