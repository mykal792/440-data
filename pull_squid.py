#!/usr/bin/env python3
"""
pull_squid.py - 440 & Friends, Squid Game scores feed.

Writes ONE file, overwritten every run:

    docs/squid-scores.json   week, live, final, updated, scores,
                             remaining, projected

Yahoo's keys ONLY. Tokens, eliminated and duel live in the Wix
SquidGameState collection and are written by the commissioner page. Nothing
in this repo has a write path to them - the separation is structural, not a
convention someone has to remember. Duels are rare and irrecoverable: a job
that wrote the whole object would look fine for weeks and then wipe the one
thing that cannot be regenerated, at 1pm on a Sunday.

The board fetches this feed and overlays it on the collection state.

    python3 pull_squid.py                          # -> docs/squid-scores.json
    python3 pull_squid.py --week 4                 # force a week
    python3 pull_squid.py --dry-run
    python3 pull_squid.py --fixtures ~/440-samples # offline

REMAINING
    `remaining` is the count of starters who have not played yet. It drives the
    projected ranking and every survival percentage, so a stale value makes the
    board read current score as final - teams with games left look doomed and
    the odds snap to 0/100 mid-Sunday.

    Yahoo has a team_remaining_games field on live scoreboards, but it is absent
    outside game windows, so it could not be verified before the season (checked
    2026-09-01, preseason). This script prefers it when present and otherwise
    counts starters whose game has not produced a score yet. Both paths are
    below; the fallback degrades in the right direction, converging on zero as
    the week completes.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc

try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:                                  # pragma: no cover
    EASTERN = timezone.utc

STARTERS = yc.STARTING_SLOTS


def display_stamp():
    """'SUN 4:12 PM' - what the board shows as UPD ..."""
    now = datetime.now(tz=timezone.utc).astimezone(EASTERN)
    return "%s %d:%02d %s" % (now.strftime("%a").upper(),
                              int(now.strftime("%I")), now.minute,
                              now.strftime("%p"))


def remaining_from_yahoo(sb_payload):
    """-> {manager_key: int} from team_remaining_games, or {} if absent."""
    league = sb_payload["fantasy_content"]["league"]
    sb = yc.find_key(league[1:], "scoreboard") or league[1].get("scoreboard")
    container = (sb or {}).get("0", {}).get("matchups") or (sb or {}).get("matchups")

    out = {}
    for wrapper in yc.numbered(container or {}):
        m = wrapper.get("matchup")
        mm = yc.merge_meta(m) if isinstance(m, list) else (m or {})
        teams_c = mm.get("0", {}).get("teams") or mm.get("teams") or {}
        for tw in yc.numbered(teams_c):
            team = tw.get("team")
            if team is None:
                continue
            tmeta = yc.merge_meta(team[0] if isinstance(team[0], list) else team)
            manager = yc.TEAM_MAP.get(str(tmeta.get("team_id", "")))
            if manager is None:
                continue
            rg = yc.find_key(team[1:], "team_remaining_games")
            if rg is None:
                continue
            total = yc.merge_meta(rg).get("total")
            if total is None and isinstance(rg, dict):
                total = (rg.get("coverage_type") and rg.get("remaining_games"))
            if total is not None:
                out[manager] = int(yc.fnum(total))
    return out


def remaining_from_rosters(rosters):
    """Fallback: starters with no score yet.

    Before kickoff every starter counts as remaining, which is correct. As
    players score, the count falls. A genuine zero-point performance keeps a
    player counted until the week goes final, which slightly overstates what is
    left - safer than understating it, since the board treats a low `remaining`
    as near-certainty.
    """
    out = {}
    for manager, team in rosters.items():
        out[manager] = sum(
            1 for p in team["players"]
            if p["slot"] in STARTERS and p["points"] == 0.0)
    return out


def build(week, status, matchups, rosters, sb_payload):
    scores = {k: 0.0 for k in yc.MANAGER_KEYS}
    for m in matchups:
        for manager, score in (m["home"], m["away"]):
            if manager:
                scores[manager] = round(score, 2)

    remaining = remaining_from_yahoo(sb_payload) if sb_payload else {}
    source = "team_remaining_games"
    if len(remaining) < len(yc.MANAGER_KEYS):
        remaining = remaining_from_rosters(rosters)
        source = "unscored starters (fallback)"

    projected = yc.parse_projected(sb_payload) if sb_payload else {}
    proj_source = "team_projected_points" if projected else "absent - board will ESTIMATE"

    # Every manager, every week, including eliminated ones. A missing key would
    # read as 0 anyway; being explicit makes a broken pull obvious.
    remaining = {k: int(remaining.get(k, 0)) for k in yc.MANAGER_KEYS}
    projected = {k: round(yc.fnum(projected.get(k)), 2) for k in yc.MANAGER_KEYS}

    return {
        "season": yc.SEASON,
        "week": week,
        "live": status == "live",
        "final": status == "final",
        "updated": display_stamp(),
        "scores": scores,
        "remaining": remaining,
        "projected": projected,
        "_note": ("Written by pull_squid.py - Yahoo's keys only. Tokens, "
                  "eliminated and duel live in the Wix SquidGameState "
                  "collection, written by the commissioner page. Nothing "
                  "automated has a write path to them."),
    }, source, proj_source


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="override the week number")
    ap.add_argument("--out-dir", default="docs")
    ap.add_argument("--yf-dir", help="directory holding .yahoofantasy")
    ap.add_argument("--fixtures", help="parse saved JSON from this dir, no network")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.fixtures:
        fx = Path(args.fixtures).expanduser()
        sb_path = fx / "scoreboard_sample.json"
        sb_raw = json.loads(sb_path.read_text()) if sb_path.exists() else None
        rosters_raw = {tid: json.loads((fx / ("roster_t%s.json" % tid)).read_text())
                       for tid in yc.TEAM_MAP
                       if (fx / ("roster_t%s.json" % tid)).exists()}
    else:
        token = yc.get_token(args.yf_dir)
        week = args.week or yc.current_week(token)
        sb_raw = yc.fetch_scoreboard(token, week)
        rosters_raw = yc.fetch_rosters(token, week)

    rosters = yc.parse_rosters(rosters_raw)
    if sb_raw:
        sb_week, status, matchups = yc.parse_scoreboard(sb_raw)
    else:
        sb_week, status, matchups = (args.week or 1), "pregame", []
    week = args.week or sb_week or 1

    payload, source, proj_source = build(week, status, matchups, rosters, sb_raw)

    print("week %d | %s | remaining via %s | projected via %s"
          % (week, status, source, proj_source))
    if len(rosters) != 10:
        print("  ! expected 10 managers, got %d - check TEAM_MAP" % len(rosters))

    text = json.dumps(payload, indent=2) + "\n"
    out = Path(args.out_dir).expanduser().resolve() / "squid-scores.json"
    if args.dry_run:
        print("--- would write %s ---" % out)
        print(text)
        return
    tmp = str(out) + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, out)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
