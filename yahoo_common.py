#!/usr/bin/env python3
"""
yahoo_common.py - 440 & Friends, shared Yahoo Fantasy plumbing.

Everything that touches the Yahoo API or its JSON lives here, so the three
scripts that use it can't drift apart:

    pull_live.py       scoreboard.json + bonus.json   (live, every 10 min)
    pull_standings.py  season.json                    (weekly, after games)
    settle_week.py     winner proposal -> bank-ledger.json (by hand)

TRAPS, learned 2026-09-01:

  - The yahoofantasy library reads .yahoofantasy from the CURRENT WORKING
    DIRECTORY, not from home.  Pass yf_dir to get_token().

  - ctx._get_access_token() refreshes the token but returns None.  Call it for
    the side effect, then read ctx._access_token.

  - Do NOT use ctx.get_leagues().  The library's season table stops at 2025 and
    raises ValueError on 2026 before any network call.  We address the league
    by key instead, which sidesteps the table entirely.

  - Manager nicknames are NOT reliable identifiers.  Yahoo returns "M", "Mark",
    "Michael", "I'm Embarrassed".  Managers can change them mid-season.  Map on
    team_id, which is stable for the season.

  - Yahoo HTML-escapes names (&#39;).  Unescape before writing anything the site
    will render.

  - One call returns all ten rosters:
        league/<key>/teams/roster/players/stats;type=week;week=N
    Chaining teams under scoreboard does NOT work ("subresource teams not
    supported").  Fetch the scoreboard separately.

  - Yahoo requires an app to be manually activated against the Fantasy API
    after creation.  The permission checkbox and a successful consent screen are
    NOT sufficient.  Symptom is 403 "This application is not authorized to
    perform this action".  Email Yahoo Fantasy support with App ID, Client ID,
    and Yahoo ID.  Permissions cannot be added to an existing app, and an "&" in
    an app name makes the create form return a 500.
"""

import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- league identity ---------------------------------------------------------

SEASON = 2026
LEAGUE_KEY = "470.l.63529"      # 2025 was 461.l.29726
API = "https://fantasysports.yahooapis.com/fantasy/v2/"

# Next season: read the "renewed" field off this league's metadata to get the
# new key without waiting for any library update.  ("renew" points backwards.)

# Yahoo team_id -> site manager key.  Verified against Week 1 2026 matchups.
TEAM_MAP = {
    "1": "schilling",     # Schill The Thrill
    "2": "garrett",       # Anderton's Revenge
    "3": "hayden",        # Brian the MG
    "4": "greg",          # Deer Hunter
    "5": "scarpitti",     # Galaxy
    "6": "gangus",        # Gangus
    "7": "graham",        # Crackers
    "8": "coyne",         # LongLivePulledPork
    "9": "scotty",        # Reagan's Raiders
    "10": "anthony",      # OTPHJ
}

MANAGER_KEYS = list(TEAM_MAP.values())
DISPLAY = {k: k.upper() for k in MANAGER_KEYS}

# Roster slots, from league settings: QB, RB x2, WR x2, TE, W/R/T, K, DEF, BN x6, IR x2
STARTING_SLOTS = {"QB", "RB", "WR", "TE", "W/R/T", "K", "DEF"}
FLEX_SLOT = "W/R/T"
BENCH_SLOT = "BN"

REPO = Path(__file__).resolve().parent
CATEGORIES_FILE = REPO / "bonus-categories.json"


# --- Yahoo JSON helpers ------------------------------------------------------
# Yahoo nests as lists of single-key dicts and dicts keyed by stringified ints.
# These absorb that so the code above stays readable.

def merge_meta(seq):
    """Flatten Yahoo's list-of-one-key-dicts into a single dict."""
    if isinstance(seq, dict):
        return dict(seq)
    out = {}
    for item in seq or []:
        if isinstance(item, dict):
            out.update(item)
        elif isinstance(item, list):
            out.update(merge_meta(item))
    return out


def numbered(container):
    """Yield values out of a Yahoo {'0':.., '1':.., 'count':N} pseudo-list."""
    if not isinstance(container, dict):
        return
    try:
        n = int(container.get("count", 0))
    except (TypeError, ValueError):
        return
    for i in range(n):
        item = container.get(str(i))
        if item is not None:
            yield item


def find_key(seq, key):
    """Find `key` anywhere in a Yahoo list-of-dicts wrapper."""
    if isinstance(seq, dict):
        return seq.get(key)
    for item in seq or []:
        if isinstance(item, dict) and key in item:
            return item[key]
    return None


def fnum(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean(text):
    return html.unescape(text) if isinstance(text, str) else text


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- auth + fetch ------------------------------------------------------------

def get_token(yf_dir=None):
    """Refresh and return a valid access token.

    yf_dir is the directory containing .yahoofantasy.  Defaults to $YF_DIR, then
    the current directory.  In GitHub Actions, write the credentials file into
    the workspace and leave this unset.
    """
    target = yf_dir or os.environ.get("YF_DIR") or os.getcwd()
    target = os.path.expanduser(target)
    if not os.path.exists(os.path.join(target, ".yahoofantasy")):
        sys.exit("No .yahoofantasy in %s\nRun 'yahoofantasy login' there, or set "
                 "YF_DIR." % target)
    os.chdir(target)

    from yahoofantasy import Context
    ctx = Context()
    ctx._get_access_token()        # refreshes; returns None
    token = ctx._access_token
    if not token:
        sys.exit("Could not obtain an access token. Re-run 'yahoofantasy login'.")
    return token


def fetch(path, token):
    import requests
    r = requests.get(API + path + "?format=json",
                     headers={"Authorization": "Bearer " + token}, timeout=30)
    if r.status_code == 403:
        sys.exit("403 from Yahoo for %s\n"
                 "The app is not activated against the Fantasy API. This is a "
                 "manual step on Yahoo's side - email Fantasy support with the "
                 "App ID, Client ID, and Yahoo ID.\n%s" % (path, r.text[:300]))
    if r.status_code != 200:
        sys.exit("Yahoo returned %s for %s\n%s" % (r.status_code, path, r.text[:400]))
    return r.json()


def fetch_rosters(token, week):
    """All ten rosters, as {team_id: payload}.

    TEN CALLS, ON PURPOSE. The league-wide form looks like an obvious
    optimisation and is a trap:

        league/<key>/teams/roster/players/stats;type=week;week=N

    returns 200 with all 150 players and their selected_position, but NO
    player_points blocks at all - verified 2026-09-01, with and without the
    type/week parameters. Every bonus would silently compute as 0.00 for the
    whole season. Only the per-team form carries points:

        team/<key>.t.<id>/roster/players/stats;type=week;week=N

    Ten calls per refresh is roughly 60/hour at a 10-minute cadence, which is
    well inside normal use.
    """
    out = {}
    for team_id in TEAM_MAP:
        out[team_id] = fetch(
            "team/%s.t.%s/roster/players/stats;type=week;week=%d"
            % (LEAGUE_KEY, team_id, week), token)
    return out


def fetch_scoreboard(token, week):
    return fetch("league/%s/scoreboard;week=%d" % (LEAGUE_KEY, week), token)


def fetch_standings(token):
    return fetch("league/%s/standings" % LEAGUE_KEY, token)


def fetch_league_meta(token):
    return merge_meta(fetch("league/" + LEAGUE_KEY, token)["fantasy_content"]["league"][0])


def current_week(token):
    return int(fnum(fetch_league_meta(token).get("current_week"), 1))


# --- parsers -----------------------------------------------------------------

def parse_rosters(payloads):
    """-> {manager_key: {'team_id', 'team_name', 'players': [...]}}

    Takes {team_id: payload} from fetch_rosters. Each payload is a single-team
    response: fantasy_content.team = [ [metadata...], {roster...} ].

    player: {name, display_position, slot, is_flex, points}
    """
    out = {}
    for team_id, payload in (payloads or {}).items():
        manager = TEAM_MAP.get(str(team_id))
        if manager is None:
            continue
        team = payload.get("fantasy_content", {}).get("team")
        if not team:
            continue

        meta = merge_meta(team[0] if isinstance(team[0], list) else team)
        roster = find_key(team[1:], "roster")
        players_container = None
        if isinstance(roster, dict):
            players_container = (roster.get("0", {}).get("players")
                                 or roster.get("players"))

        players = []
        for pw in numbered(players_container or {}):
            p = pw.get("player")
            if p is None:
                continue
            pmeta = merge_meta(p[0] if isinstance(p[0], list) else p)
            sel = merge_meta(find_key(p[1:], "selected_position") or [])
            pts = find_key(p[1:], "player_points") or {}
            players.append({
                "name": clean((pmeta.get("name") or {}).get("full", "")),
                "display_position": pmeta.get("display_position", ""),
                "slot": sel.get("position", ""),
                "is_flex": str(sel.get("is_flex", "0")) == "1",
                "points": fnum(pts.get("total")),
            })

        out[manager] = {"team_id": str(team_id),
                        "team_name": clean(meta.get("name", "")),
                        "players": players}
    return out


def parse_scoreboard(payload):
    """-> (week, status, matchups)

    matchup: {'home': (manager, score), 'away': (manager, score), 'status': str}
    status is 'pregame' | 'live' | 'final' for the week as a whole.
    """
    league = payload["fantasy_content"]["league"]
    sb = find_key(league[1:], "scoreboard") or league[1].get("scoreboard")
    week = int(fnum((sb or {}).get("week"), 0))
    matchups_container = ((sb or {}).get("0", {}).get("matchups")
                          or (sb or {}).get("matchups"))

    matchups, statuses = [], []
    for mw in numbered(matchups_container or {}):
        m = mw.get("matchup")
        if m is None:
            continue
        mm = merge_meta(m) if isinstance(m, list) else m
        statuses.append(mm.get("status", ""))

        # NOTE: teams sit one level deeper than you'd expect - mm['0']['teams'].
        teams_c = mm.get("0", {}).get("teams") or mm.get("teams") or {}
        sides = []
        for tw in numbered(teams_c):
            team = tw.get("team")
            if team is None:
                continue
            tmeta = merge_meta(team[0] if isinstance(team[0], list) else team)
            manager = TEAM_MAP.get(str(tmeta.get("team_id", "")))
            pts = find_key(team[1:], "team_points") or {}
            sides.append((manager, fnum(pts.get("total"))))

        if len(sides) == 2:
            matchups.append({"home": sides[0], "away": sides[1],
                             "status": mm.get("status", "")})

    if statuses and all(s == "postevent" for s in statuses):
        status = "final"
    elif any(s in ("midevent", "postevent") for s in statuses):
        status = "live"
    else:
        status = "pregame"
    return week, status, matchups


def parse_projected(payload):
    """-> {manager_key: float} from team_projected_points on the scoreboard.

    Yahoo's projected total is LIVE, not frozen at kickoff: it swaps each
    starter's projection for their actual score as they play, so it converges
    on the real total and equals it once every starter is done. That makes it
    useful before kickoff (a real number instead of 0.00) and during games (a
    team on 40 with six starters left still projects near 110).

    Returns {} if the field is absent.
    """
    league = payload["fantasy_content"]["league"]
    sb = find_key(league[1:], "scoreboard") or league[1].get("scoreboard")
    container = (sb or {}).get("0", {}).get("matchups") or (sb or {}).get("matchups")

    out = {}
    for wrapper in numbered(container or {}):
        m = wrapper.get("matchup")
        mm = merge_meta(m) if isinstance(m, list) else (m or {})
        teams_c = mm.get("0", {}).get("teams") or mm.get("teams") or {}
        for tw in numbered(teams_c):
            team = tw.get("team")
            if team is None:
                continue
            tmeta = merge_meta(team[0] if isinstance(team[0], list) else team)
            manager = TEAM_MAP.get(str(tmeta.get("team_id", "")))
            if manager is None:
                continue
            proj = find_key(team[1:], "team_projected_points")
            if isinstance(proj, dict) and proj.get("total") is not None:
                out[manager] = round(fnum(proj.get("total")), 2)
    return out


def parse_standings(payload):
    """-> {manager_key: {rank, wins, losses, ties, points_for, points_against}}"""
    league = payload["fantasy_content"]["league"]
    standings = find_key(league[1:], "standings") or league[1].get("standings")
    if isinstance(standings, list):
        standings = standings[0]
    teams = (standings or {}).get("teams", {})

    out = {}
    for wrapper in numbered(teams):
        team = wrapper.get("team")
        if team is None:
            continue
        meta = merge_meta(team[0] if isinstance(team[0], list) else team)
        manager = TEAM_MAP.get(str(meta.get("team_id", "")))
        if manager is None:
            continue
        ts = find_key(team[1:], "team_standings") or {}
        totals = ts.get("outcome_totals", {}) or {}
        out[manager] = {
            "rank": int(fnum(ts.get("rank"))),
            "wins": int(fnum(totals.get("wins"))),
            "losses": int(fnum(totals.get("losses"))),
            "ties": int(fnum(totals.get("ties"))),
            "points_for": fnum(ts.get("points_for")),
            "points_against": fnum(ts.get("points_against")),
        }
    return out


# --- bonus categories --------------------------------------------------------

def load_categories(path=None):
    """Read bonus-categories.json. Single source of truth for keys and labels."""
    p = Path(path) if path else CATEGORIES_FILE
    meta = json.loads(p.read_text(encoding="utf-8"))
    meta["_by_week"] = {int(c["week"]): c for c in meta.get("categories", [])}
    return meta


# --- bank ledger -------------------------------------------------------------
# bank-ledger.json is the single source of truth for money and winners. It feeds
# the bank column in standings, the Weekly Winners cards, and the High Score
# list on the DraftKings board.
#
#   {"2026": {
#      "1": {"high":     {"manager": "GREG",  "value": "142.88"},
#            "category": {"manager": "COYNE", "value": "27.62",
#                         "detail": "Christian Watson"}},
#      "awards": [{"manager": "GREG", "label": "Playoff 1st",
#                  "short": "P1", "amount": 500}]}}
#
# Playoff payouts, the DraftKings Challenge, and the Thanksgiving DK all go in
# "awards" - never edit the HTML, or the numbers end up somewhere no script can
# see and the boards drift apart.

LEDGER_FILE = REPO / "bank-ledger.json"


def manager_key(name):
    """Accept 'GREG', 'greg', ' Greg ' -> 'greg'. None if unrecognised."""
    slug = str(name or "").strip().lower()
    return slug if slug in DISPLAY else None


def normalize_winner(entry):
    """Accept either shape and return {manager, value, detail} or None.

        "GREG"                                   (old, name only)
        {"manager": "GREG", "value": "142.88"}   (current)
    """
    if not entry:
        return None
    if isinstance(entry, str):
        key = manager_key(entry)
        return {"manager": key, "value": "", "detail": ""} if key else None
    key = manager_key(entry.get("manager"))
    if not key:
        return None
    return {"manager": key,
            "value": str(entry.get("value", "")),
            "detail": str(entry.get("detail", ""))}


def load_ledger(path=None):
    """-> (weeks, awards) for the current season.

    weeks: {week_int: {"high": {...} or None, "category": {...} or None}}
    awards: [{manager, label, short, amount}, ...]
    """
    p = Path(path) if path else LEDGER_FILE
    if not p.exists():
        return {}, []
    raw = json.loads(p.read_text(encoding="utf-8"))
    season = raw.get(str(SEASON), {}) or {}

    weeks = {}
    for week_str, entry in season.items():
        if not str(week_str).isdigit() or not isinstance(entry, dict):
            continue
        weeks[int(week_str)] = {
            "high": normalize_winner(entry.get("high")),
            "category": normalize_winner(entry.get("category")),
        }

    awards = []
    for a in season.get("awards", []) or []:
        key = manager_key(a.get("manager"))
        if key:
            awards.append({"manager": key,
                           "label": a.get("label", "Award"),
                           "short": a.get("short", a.get("label", "")),
                           "amount": int(fnum(a.get("amount")))})
    return weeks, awards


# --- bonus calculators -------------------------------------------------------
# Each returns [(manager_key, value, detail), ...] unsorted.

def _best_player(rosters, predicate):
    rows = []
    for manager, team in rosters.items():
        best = None
        for p in team["players"]:
            if predicate(p) and (best is None or p["points"] > best["points"]):
                best = p
        if best:
            rows.append((manager, best["points"], best["name"]))
    return rows


def _starting(p):
    return p["slot"] in STARTING_SLOTS


def _margins(matchups):
    """-> [(winner, loser, margin), ...], ties excluded."""
    out = []
    for m in matchups:
        (hm, hs), (am, asc) = m["home"], m["away"]
        if hs > asc:
            out.append((hm, am, round(hs - asc, 2)))
        elif asc > hs:
            out.append((am, hm, round(asc - hs, 2)))
    return out


def c_bench_warmer(r, m, s):
    return _best_player(r, lambda p: p["slot"] == BENCH_SLOT)


def c_matchup_blues(r, m, s):
    rows = []
    for w, l, _ in _margins(m):
        for mu in m:
            for manager, score in (mu["home"], mu["away"]):
                if manager == l:
                    rows.append((l, score, "lost to %s" % DISPLAY.get(w, w)))
    return rows


def c_beast_mode(r, m, s):
    return _best_player(r, lambda p: _starting(p) and p["display_position"] == "RB")


def c_lucky_duck(r, m, s):
    rows = []
    for w, l, _ in _margins(m):
        for mu in m:
            for manager, score in (mu["home"], mu["away"]):
                if manager == w:
                    rows.append((w, score, "beat %s" % DISPLAY.get(l, l)))
    return rows


def c_the_sheriff(r, m, s):
    return _best_player(r, lambda p: _starting(p) and p["display_position"] == "QB")


def c_photo_finish(r, m, s):
    return [(w, margin, "beat %s" % DISPLAY.get(l, l)) for w, l, margin in _margins(m)]


def c_the_flex(r, m, s):
    return _best_player(r, lambda p: p["slot"] == FLEX_SLOT or p["is_flex"])


def c_nice_hands(r, m, s):
    return _best_player(r, lambda p: _starting(p) and p["display_position"] == "WR")


def c_blow_out(r, m, s):
    return c_photo_finish(r, m, s)


def c_bad_beat(r, m, s):
    return [(l, margin, "lost to %s" % DISPLAY.get(w, w)) for w, l, margin in _margins(m)]


def c_hammer_toe(r, m, s):
    return _best_player(r, lambda p: p["slot"] == "K")


def c_one_two_punch(r, m, s):
    """The two dedicated RB slots. A flex RB has slot 'W/R/T', so it's excluded."""
    rows = []
    for manager, team in r.items():
        rbs = [p for p in team["players"] if p["slot"] == "RB"]
        if not rbs:
            continue
        total = round(sum(p["points"] for p in rbs), 2)
        detail = " + ".join(p["name"] for p in sorted(rbs, key=lambda x: -x["points"]))
        rows.append((manager, total, detail))
    return rows


def c_pick_six(r, m, s):
    return _best_player(r, lambda p: p["slot"] == "DEF")


def c_tighty_whities(r, m, s):
    return _best_player(r, lambda p: _starting(p) and p["display_position"] == "TE")


def c_points_against(r, m, s):
    return [(k, round(v["points_against"], 2), "season total")
            for k, v in (s or {}).items()]


CALCULATORS = {
    1: c_bench_warmer,   2: c_matchup_blues,  3: c_beast_mode,
    4: c_lucky_duck,     5: c_the_sheriff,    6: c_photo_finish,
    7: c_the_flex,       8: c_nice_hands,     9: c_blow_out,
    10: c_bad_beat,      11: c_hammer_toe,    12: c_one_two_punch,
    13: c_pick_six,      14: c_tighty_whities, 15: c_points_against,
}

# Weeks where the LOWEST value wins.
ASCENDING = {4, 6, 10}

# Weeks needing player-level data (one extra API call, not ten).
ROSTER_WEEKS = {1, 3, 5, 7, 8, 11, 12, 13, 14}
SCOREBOARD_WEEKS = {2, 4, 6, 9, 10}
STANDINGS_WEEKS = {15}


def high_score_rows(rosters, matchups, standings):
    rows = []
    for m in matchups:
        for manager, score in (m["home"], m["away"]):
            rows.append((manager, score, "week total"))
    return rows


def rank_rows(rows, ascending=False, limit=3):
    """Rank rows into leader dicts. Ties share a rank.

    `manager` carries the UPPERCASE display name because that is what the
    bonus board renders. The lowercase key is in `face`, matching
    scoreboard.json, so avatars can be wired to it later.
    """
    rows = [r for r in rows if r[0]]
    if not rows:
        return []
    rows.sort(key=lambda r: r[1], reverse=not ascending)

    leaders, seen = [], []
    for manager, value, detail in rows:
        if not seen or value != seen[-1]:
            if len(seen) >= limit:
                break
            seen.append(value)
        leaders.append({
            "rank": len(seen),
            "manager": DISPLAY.get(manager, manager),
            "face": manager,
            "value": "%.2f" % value,
            "detail": detail,
        })
    return leaders


def pad_leaders(leaders, size=3):
    """Always exactly `size` rows.

    The bonus board sizes itself to its content, so a list that shrinks leaves
    white space between HTML blocks that cannot talk to each other. Real
    leaders first, placeholders to fill, hard cap at `size` - a three-way tie
    would otherwise push the list to four rows and grow the block.

    settle_week.py deliberately does NOT pad or cap: when money is being paid
    out, every tied manager must be visible.
    """
    out = list(leaders[:size])
    for i in range(len(out), size):
        out.append({
            "rank": i + 1,
            "manager": "NAME",
            "face": "",
            "value": "0.00",
            "detail": "",
        })
    return out


def compute_bonus(week, rosters, matchups, standings):
    """-> (rows, ascending) for the given week, or ([], False) if no bonus."""
    calc = CALCULATORS.get(week)
    if not calc:
        return [], False
    return calc(rosters, matchups, standings), week in ASCENDING
