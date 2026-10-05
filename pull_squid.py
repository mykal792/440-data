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
    2026-09-01, preseason). This script prefers it when present.

    Otherwise it counts starters whose NFL GAME IS NOT OVER, using the NFL
    scoreboard's game state for each player's team. Points are not the test:
    a starter who finished on 0.00 has played. (The old fallback counted
    "starters with no points yet", which kept every zero-point performance
    on the board as still to come - week 4, Sadiq.)

    Per player, in order:
      1. his NFL team's game is final, or his team is on bye   -> played
      2. his team's game is scheduled or in progress           -> remaining
      3. game state unknown (scoreboard unreachable, or a team
         abbreviation we can't match)                          -> old rule:
         remaining only if he has no points yet
    Rule 3 keeps one bad lookup from zeroing a team's remaining count, which
    the board would read as near-certainty. Every run prints how many starters
    each rule decided, so a broken lookup is visible in the log.
"""

import argparse
import json
import os
import sys
import urllib.request
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

# ESPN's public NFL scoreboard (unofficial, no auth). Used only for game state.
ESPN_SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
                   "scoreboard?seasontype=2&week=%d")

# All 32 teams as ESPN spells them. A player whose team is in this set but has
# no game this week is on bye. A team NOT in this set is a spelling we failed
# to match - that must never be mistaken for a bye.
NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE",
    "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WSH",
}
# Yahoo editorial_team_abbr -> ESPN (after upper-casing). Yahoo writes "Was",
# "Jax", "KC" ...; only the ones that differ once upper-cased need an entry.
TEAM_ALIAS = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC"}


def display_stamp():
    """'SUN 4:12 PM' - what the board shows as UPD ..."""
    now = datetime.now(tz=timezone.utc).astimezone(EASTERN)
    return "%s %d:%02d %s" % (now.strftime("%a").upper(),
                              int(now.strftime("%I")), now.minute,
                              now.strftime("%p"))


def norm_team(abbr):
    if not abbr:
        return None
    a = str(abbr).strip().upper()
    return TEAM_ALIAS.get(a, a)


def nfl_team_of(player):
    """The player's NFL team abbreviation.

    yahoo_common.parse_rosters stores Yahoo's editorial_team_abbr as "team".
    The other keys are only a guard against that being renamed later.
    """
    for key in ("team", "nfl_team", "editorial_team_abbr"):
        if player.get(key):
            return player[key]
    return None


def fetch_game_states(week, fixtures_dir=None):
    """-> {ESPN team abbr: 'pre' | 'in' | 'post'} for this NFL week, or None.

    None means "couldn't tell" and sends every player to rule 3; it is never
    an empty dict, which would read as a league-wide bye.
    """
    try:
        if fixtures_dir:
            path = Path(fixtures_dir) / "espn_scoreboard.json"
            if not path.exists():
                return None
            data = json.loads(path.read_text())
        else:
            req = urllib.request.Request(ESPN_SCOREBOARD % week,
                                         headers={"User-Agent": "pull_squid/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        states = {}
        for ev in data.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            state = (((comp.get("status") or ev.get("status") or {})
                      .get("type") or {}).get("state"))
            for c in comp.get("competitors", []):
                abbr = norm_team((c.get("team") or {}).get("abbreviation"))
                if abbr and state:
                    states[abbr] = state
        return states or None
    except Exception as exc:                                  # network, JSON
        print("  ! NFL scoreboard unavailable (%s) - zero-point rule for all" % exc)
        return None


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


def remaining_from_rosters(rosters, game_states):
    """Fallback: starters whose NFL game is not over yet (rules 1-3 above).

    -> ({manager: count}, {"final_or_bye": n, "to_play": n, "zero_rule": n},
        [unmatched team abbreviations])
    """
    out = {}
    tally = {"final_or_bye": 0, "to_play": 0, "zero_rule": 0}
    unmatched = set()
    for manager, team in rosters.items():
        n = 0
        for p in team["players"]:
            if p["slot"] not in STARTERS:
                continue
            raw = nfl_team_of(p)
            abbr = norm_team(raw)
            if game_states is not None and abbr in NFL_TEAMS:
                state = game_states.get(abbr)
                if state in (None, "post"):         # no game = bye, or final
                    tally["final_or_bye"] += 1
                    continue
                tally["to_play"] += 1               # 'pre' or 'in'
                n += 1
                continue
            if raw and abbr not in NFL_TEAMS:
                unmatched.add(str(raw))
            tally["zero_rule"] += 1                 # rule 3: old behaviour
            if p["points"] == 0.0:
                n += 1
        out[manager] = n
    return out, tally, sorted(unmatched)


def build(week, status, matchups, rosters, sb_payload, game_states=None):
    scores = {k: 0.0 for k in yc.MANAGER_KEYS}
    for m in matchups:
        for manager, score in (m["home"], m["away"]):
            if manager:
                scores[manager] = round(score, 2)

    remaining = remaining_from_yahoo(sb_payload) if sb_payload else {}
    source = "team_remaining_games"
    if len(remaining) < len(yc.MANAGER_KEYS):
        remaining, tally, unmatched = remaining_from_rosters(rosters, game_states)
        source = ("NFL game state - %d final/bye, %d to play, %d by zero-point rule"
                  % (tally["final_or_bye"], tally["to_play"], tally["zero_rule"]))
        if unmatched:
            source += " | unmatched teams: %s (add to TEAM_ALIAS)" % ", ".join(unmatched)

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

    # The Squid Game week is the Yahoo fantasy week, which is the NFL
    # regular-season week for a league that starts in week 1.
    game_states = fetch_game_states(week, args.fixtures)

    payload, source, proj_source = build(week, status, matchups, rosters, sb_raw,
                                         game_states)

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
