#!/usr/bin/env python3
"""probe_live_projection.py - is there a LIVE projection field, and does it move?

Yahoo's site shows a "projected final" that updates during games: points
banked so far plus a projection for the starters still to play. The API field
we read, team_projected_points, barely moves once games start - which is what
"stuck on the original projections" looks like.

There is very likely a second field, team_live_projected_points, that appears
only while a week is in progress. Our earlier probe ran in preseason, when it
would not have existed yet, so it proved nothing either way.

RUN THIS WHILE GAMES ARE ACTUALLY IN PROGRESS. Outside a game window Yahoo
drops the live fields and you will get a false negative.

    python3 probe_live_projection.py
    python3 probe_live_projection.py --week 2

Prints, per team: every projection-ish field found, the live score, and
team_remaining_games. Writes nothing.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc


def total_of(node):
    if isinstance(node, dict):
        if node.get("total") is not None:
            return node.get("total")
        return json.dumps(node)[:80]
    return node


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int)
    ap.add_argument("--yf-dir")
    ap.add_argument("--dump", help="write the raw scoreboard payload here")
    args = ap.parse_args()

    token = yc.get_token(args.yf_dir)
    week = args.week or yc.current_week(token)
    payload = yc.fetch_scoreboard(token, week)

    _, status, _ = yc.parse_scoreboard(payload)
    print("week %d | status %s" % (week, status))
    if status != "live":
        print("!! week is not live - live-only fields will be absent.")
        print("!! a negative result here means nothing. Re-run during games.")
    print()

    league = payload["fantasy_content"]["league"]
    sb = yc.find_key(league[1:], "scoreboard") or league[1].get("scoreboard")
    container = (sb or {}).get("0", {}).get("matchups") or (sb or {}).get("matchups")

    seen = set()
    for wrapper in yc.numbered(container or {}):
        m = wrapper.get("matchup")
        mm = yc.merge_meta(m) if isinstance(m, list) else (m or {})
        teams_c = mm.get("0", {}).get("teams") or mm.get("teams") or {}
        for tw in yc.numbered(teams_c):
            team = tw.get("team")
            if team is None:
                continue
            tmeta = yc.merge_meta(team[0] if isinstance(team[0], list) else team)
            manager = yc.TEAM_MAP.get(str(tmeta.get("team_id", ""))) or "?"

            fields = {}
            def collect(node, path=""):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if "project" in str(k).lower() or k == "team_remaining_games":
                            fields[k] = total_of(v)
                            seen.add(k)
                        collect(v, path + "." + str(k))
                elif isinstance(node, list):
                    for v in node:
                        collect(v, path)
            collect(team[1:])

            pts = yc.find_key(team[1:], "team_points")
            score = total_of(pts) if pts else "?"
            print("%-10s score %-8s %s" % (
                manager, score,
                "  ".join("%s=%s" % (k, v) for k, v in sorted(fields.items()))
                or "(no projection fields)"))

    print()
    print("fields seen across all teams: %s" % (", ".join(sorted(seen)) or "none"))
    if "team_live_projected_points" in seen:
        print("-> live projections ARE available. yahoo_common.py already")
        print("   prefers this field, so nothing more to change.")
    elif status == "live":
        print("-> no live projection field while the week is live.")
        print("   team_projected_points is the only projection Yahoo exposes.")

    if args.dump:
        Path(args.dump).write_text(json.dumps(payload, indent=2))
        print("wrote %s" % args.dump)


if __name__ == "__main__":
    main()
