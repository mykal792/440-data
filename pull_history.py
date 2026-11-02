#!/usr/bin/env python3
"""pull_history.py - every season of the league, straight from Yahoo.

Walks the league's "renew" chain backwards from this season's key (2026 ->
2025 -> ... -> 2013) and writes three files the player profiles are built from:

    docs/history/seasons.json    each season: settings, every team, its manager,
                                 final rank, record, points for/against
    docs/history/matchups.json   every game ever played, one row per game,
                                 regular season, playoffs and consolation
    docs/history/managers.json   every Yahoo account that ever had a team, and
                                 which site manager it maps to

    python3 pull_history.py --dry-run          # all seasons, print, write nothing
    python3 pull_history.py                    # all seasons, write the files
    python3 pull_history.py --since 2026       # just this season, merged into
                                               # the existing files (weekly use)

WHO IS WHO. Team names change every year and team_id is only stable inside one
season, so neither can link seasons together. The Yahoo account (manager guid)
can. This season's TEAM_MAP gives team_id -> manager for 2026; that turns into
guid -> manager, and every older team is matched on its guid.

Anyone whose guid never appears in 2026 - managers who left the league, or a
current manager who once played on a different Yahoo account - comes out as
"former-<nickname>" and is listed at the end of the run. Cam and Steve, who
left before Hayden and Scotty joined, are recognised by name (FORMER_MANAGERS). To fix one, add it to
history-managers.json at the repo root (hand-owned, no script writes it):

    { "<guid>": "graham" }

ONLY FINISHED WEEKS are written. A week still in progress is skipped, so the
weekly run never publishes a half-played score.

This script is read-only against Yahoo and never touches any other docs/ file.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc

OUT_DIR = Path("docs") / "history"
OVERRIDES_FILE = yc.REPO / "history-managers.json"


# --- fetching ----------------------------------------------------------------

def get(path, token, tries=3):
    """yc.fetch with a short retry. 300-odd calls in one run makes the odd
    401 "Invalid cookie" hiccup likely; one bad response shouldn't kill it."""
    for attempt in range(tries):
        try:
            return yc.fetch(path, token)
        except SystemExit as exc:
            if attempt == tries - 1 or "403" in str(exc)[:4]:
                raise
            print("  retrying %s (%s)" % (path, str(exc).splitlines()[0]))
            time.sleep(3 * (attempt + 1))


def league_block(payload, name):
    league = payload["fantasy_content"]["league"]
    return yc.find_key(league[1:], name)


def league_meta(payload):
    return yc.merge_meta(payload["fantasy_content"]["league"][0])


def renew_to_key(renew):
    """'461_29726' -> '461.l.29726'. Empty means the chain ends here."""
    m = re.match(r"^(\d+)_(\d+)$", str(renew or "").strip())
    return "%s.l.%s" % (m.group(1), m.group(2)) if m else None


# --- parsing -----------------------------------------------------------------

def team_meta(team):
    return yc.merge_meta(team[0] if isinstance(team[0], list) else team)


def team_managers(tmeta):
    """-> list of {guid, nickname, is_comanager}"""
    out = []
    for wrapper in tmeta.get("managers") or []:
        m = wrapper.get("manager") if isinstance(wrapper, dict) else None
        if not m:
            continue
        out.append({"guid": m.get("guid") or "",
                    "nickname": yc.clean(m.get("nickname") or ""),
                    "is_comanager": str(m.get("is_comanager", "0")) == "1"})
    # primary manager first
    out.sort(key=lambda x: x["is_comanager"])
    return out


def parse_teams(standings_payload):
    """-> {team_key: {...}} with manager, final rank, record, points."""
    standings = league_block(standings_payload, "standings")
    if isinstance(standings, list):
        standings = standings[0]
    out = {}
    for wrapper in yc.numbered((standings or {}).get("teams", {})):
        team = wrapper.get("team")
        if team is None:
            continue
        meta = team_meta(team)
        ts = yc.find_key(team[1:], "team_standings") or {}
        totals = ts.get("outcome_totals") or {}
        managers = team_managers(meta)
        key = meta.get("team_key")
        out[key] = {
            "team_key": key,
            "team_id": str(meta.get("team_id", "")),
            "team_name": yc.clean(meta.get("name", "")),
            "guid": managers[0]["guid"] if managers else "",
            "nickname": managers[0]["nickname"] if managers else "",
            "co_managers": [m["nickname"] for m in managers[1:]],
            "final_rank": int(yc.fnum(ts.get("rank"))) or None,
            "playoff_seed": int(yc.fnum(ts.get("playoff_seed"))) or None,
            "wins": int(yc.fnum(totals.get("wins"))),
            "losses": int(yc.fnum(totals.get("losses"))),
            "ties": int(yc.fnum(totals.get("ties"))),
            "points_for": round(yc.fnum(ts.get("points_for")), 2),
            "points_against": round(yc.fnum(ts.get("points_against")), 2),
        }
    return out


def parse_settings(settings_payload):
    s = league_block(settings_payload, "settings")
    s = yc.merge_meta(s) if isinstance(s, list) else (s or {})
    return {
        "playoff_start_week": int(yc.fnum(s.get("playoff_start_week"))) or None,
        "num_playoff_teams": int(yc.fnum(s.get("num_playoff_teams"))) or None,
        "uses_playoff": str(s.get("uses_playoff", "1")) == "1",
        "consolation": str(s.get("has_playoff_consolation_games", "0")) == "1",
        "scoring_type": s.get("scoring_type"),
    }


def parse_week(payload, season):
    """-> (games, all_finished). One row per game, both sides."""
    sb = league_block(payload, "scoreboard") or {}
    container = sb.get("0", {}).get("matchups") or sb.get("matchups") or {}
    games, finished = [], True
    for wrapper in yc.numbered(container):
        m = wrapper.get("matchup")
        if m is None:
            continue
        mm = yc.merge_meta(m) if isinstance(m, list) else m
        status = mm.get("status", "")
        if status != "postevent":
            finished = False
            continue
        sides = []
        for tw in yc.numbered(mm.get("0", {}).get("teams") or mm.get("teams") or {}):
            team = tw.get("team")
            if team is None:
                continue
            meta = team_meta(team)
            pts = yc.find_key(team[1:], "team_points") or {}
            sides.append({"team_key": meta.get("team_key"),
                          "points": round(yc.fnum(pts.get("total")), 2)})
        if len(sides) != 2:
            continue
        a, b = sides
        if str(mm.get("is_tied", "0")) == "1" or a["points"] == b["points"]:
            winner = None
        else:
            wk = mm.get("winner_team_key")
            if wk not in (a["team_key"], b["team_key"]):
                wk = a["team_key"] if a["points"] > b["points"] else b["team_key"]
            winner = wk
        playoff = str(mm.get("is_playoffs", "0")) == "1"
        consolation = str(mm.get("is_consolation", "0")) == "1"
        games.append({
            "season": season,
            "week": int(yc.fnum(mm.get("week"))),
            "type": ("consolation" if consolation else
                     "playoff" if playoff else "regular"),
            "a": a, "b": b,
            "winner": winner,
        })
    return games, finished


# --- identity ----------------------------------------------------------------

def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "unknown").lower()).strip("-")


def build_guid_map(current_teams, overrides):
    """guid -> manager key, seeded from this season's TEAM_MAP."""
    gmap = {}
    for t in current_teams.values():
        mgr = yc.TEAM_MAP.get(t["team_id"])
        if mgr and t["guid"]:
            gmap[t["guid"]] = mgr
    gmap.update(overrides)
    return gmap


# Managers who left the league, recognised by a team name they used at least
# once. Matching one team name is enough: the Yahoo account (guid) behind it
# then carries the name to every other season that account played, whatever
# the team was called. Lowercase substrings.
FORMER_MANAGERS = {
    "cam": ["cindy gricar"],
    "steve": ["jaypa"],
}


def hinted_manager(team):
    name = (team.get("team_name") or "").lower()
    for mgr, hints in FORMER_MANAGERS.items():
        if any(h in name for h in hints):
            return mgr
    return None


def resolve(team, gmap, is_current):
    if is_current and team["team_id"] in yc.TEAM_MAP:
        return yc.TEAM_MAP[team["team_id"]]
    if team["guid"] and team["guid"] in gmap:
        return gmap[team["guid"]]
    return hinted_manager(team) or "former-" + slug(team["nickname"] or team["team_name"])


# --- main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=0,
                    help="only pull seasons from this year on, and merge into "
                         "the existing files (e.g. --since 2026 weekly)")
    ap.add_argument("--start-key", default=yc.LEAGUE_KEY,
                    help="league key to start walking back from")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--yf-dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo_cwd = Path.cwd()
    out_dir = (repo_cwd / args.out_dir).resolve()
    token = yc.get_token(args.yf_dir)   # note: chdirs into the yf dir

    overrides = {}
    if OVERRIDES_FILE.exists():
        overrides = {k: v for k, v in json.loads(OVERRIDES_FILE.read_text()).items()
                     if not k.startswith("_")}

    seasons, games, calls = {}, [], 0
    key, gmap, seen = args.start_key, None, set()
    while key and key not in seen:
        seen.add(key)
        meta = league_meta(get("league/" + key, token)); calls += 1
        season = int(yc.fnum(meta.get("season")))
        if args.since and season < args.since:
            break
        settings = parse_settings(get("league/%s/settings" % key, token))
        teams = parse_teams(get("league/%s/standings" % key, token))
        calls += 2

        is_current = key == yc.LEAGUE_KEY
        if gmap is None:
            # The first league walked must be the current one - it's the only
            # season TEAM_MAP describes.
            if not is_current:
                sys.exit("--start-key must be the current league (%s) so the "
                         "manager map can be seeded." % yc.LEAGUE_KEY)
            gmap = build_guid_map(teams, overrides)

        for t in teams.values():
            t["_current"] = is_current

        end_week = int(yc.fnum(meta.get("end_week"))) or 17
        start_week = int(yc.fnum(meta.get("start_week"))) or 1
        season_games, incomplete = [], []
        for week in range(start_week, end_week + 1):
            wk_games, finished = parse_week(
                get("league/%s/scoreboard;week=%d" % (key, week), token), season)
            calls += 1
            if not finished:
                incomplete.append(week)
            season_games.extend(wk_games)
            if not wk_games and not finished:
                break          # nothing played from here on - current season

        reg_weeks = (settings["playoff_start_week"] - 1
                     if settings["playoff_start_week"] else None)
        seasons[season] = {
            "season": season,
            "league_key": key,
            "name": yc.clean(meta.get("name", "")),
            "finished": str(meta.get("is_finished", "0")) == "1",
            "regular_weeks": reg_weeks,
            "end_week": end_week,
            **settings,
            "teams": sorted(teams.values(),
                            key=lambda t: (t["final_rank"] or 99)),
        }
        seasons[season]["_incomplete"] = incomplete
        games.extend(season_games)
        print("  pulled %d (%s)" % (season, key))
        key = renew_to_key(meta.get("renew"))

    # Who is who - done after every season is in hand, so a former manager
    # recognised by a team name in one year is carried by his Yahoo account
    # to every other year, whichever order the seasons were walked in.
    for s in seasons.values():
        for t in s["teams"]:
            mgr = None if t["_current"] else hinted_manager(t)
            if mgr and t["guid"] and t["guid"] not in gmap:
                gmap[t["guid"]] = mgr
    print()
    for season in sorted(seasons):
        s = seasons[season]
        by_key = {}
        for t in s["teams"]:
            t["manager"] = resolve(t, gmap, t.pop("_current"))
            by_key[t["team_key"]] = t["manager"]
        n = 0
        for g in games:
            if g["season"] != season:
                continue
            n += 1
            for side in ("a", "b"):
                g[side]["manager"] = by_key.get(g[side]["team_key"], "unknown")
            g["winner_manager"] = by_key.get(g["winner"]) if g["winner"] else None
        champ = next((t for t in s["teams"] if t["final_rank"] == 1), None)
        inc = s.pop("_incomplete")
        print("%d  %-14s %2d teams  %3d games  champ: %s%s" % (
            season, s["league_key"], len(s["teams"]), n,
            champ["manager"] if champ and s["finished"] else "-",
            ("   (unfinished weeks: %s)" % inc) if inc else ""))

    # Merge with what's already published when only part of history was pulled.
    seasons_path = out_dir / "seasons.json"
    matchups_path = out_dir / "matchups.json"
    if args.since and seasons_path.exists() and matchups_path.exists():
        old_seasons = json.loads(seasons_path.read_text())["seasons"]
        old_games = json.loads(matchups_path.read_text())["games"]
        keep = [s for s in old_seasons if s["season"] < args.since]
        seasons_list = keep + sorted(seasons.values(), key=lambda s: s["season"])
        games = [g for g in old_games if g["season"] < args.since] + games
    else:
        seasons_list = sorted(seasons.values(), key=lambda s: s["season"])
    seasons_list.sort(key=lambda s: s["season"])
    games.sort(key=lambda g: (g["season"], g["week"], g["a"]["manager"]))

    # Every account that ever had a team.
    accounts = {}
    for s in seasons_list:
        for t in s["teams"]:
            acc = accounts.setdefault(t["guid"] or t["manager"], {
                "guid": t["guid"], "manager": t["manager"],
                "current": t["manager"] in yc.MANAGER_KEYS,
                "nicknames": [], "seasons": [], "team_names": []})
            if t["nickname"] and t["nickname"] not in acc["nicknames"]:
                acc["nicknames"].append(t["nickname"])
            acc["seasons"].append(s["season"])
            acc["team_names"].append("%d %s" % (s["season"], t["team_name"]))

    stamp = yc.now_iso()
    files = {
        "seasons.json": {"updated": stamp, "seasons": seasons_list},
        "matchups.json": {"updated": stamp, "count": len(games), "games": games},
        "managers.json": {"updated": stamp,
                          "accounts": sorted(accounts.values(),
                                             key=lambda a: (not a["current"],
                                                            a["manager"]))},
    }

    print("\n%d Yahoo calls. %d seasons, %d games." % (calls, len(seasons_list), len(games)))
    print("\nWho played which seasons:")
    for a in files["managers.json"]["accounts"]:
        print("  %-22s %s-%s  (%d seasons)  aka %s" % (
            a["manager"], min(a["seasons"]), max(a["seasons"]), len(a["seasons"]),
            ", ".join(a["nicknames"]) or "-"))
    formers = [a for a in files["managers.json"]["accounts"]
               if a["manager"].startswith("former-")]
    if formers:
        print("\nNot matched to a current manager (left the league, or an old "
              "Yahoo account). If any of these is a current manager, add "
              "\"<guid>\": \"<manager>\" to history-managers.json:")
        for a in formers:
            print("  %s  guid=%s  teams: %s" % (a["manager"], a["guid"] or "?",
                                               "; ".join(a["team_names"])))

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_text(json.dumps(data, indent=1) + "\n")
        print("wrote %s" % (out_dir / name))


if __name__ == "__main__":
    main()
