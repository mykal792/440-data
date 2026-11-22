#!/usr/bin/env python3
"""
pull_boxscores.py - box scores for the games in the Hall of Records.

Every single-game and playoff-game record row in docs/records.json carries a
game id, "season-week-<team id a>-<team id b>" (build_records.py). For each id
that has no docs/boxscores/<id>.json yet, this pulls both lineups for that
week from Yahoo and writes one small file the record page loads when a row is
tapped:

    {"id", "season", "week", "type", "round",
     "a": {"manager", "team_name", "points", "players": [...]},
     "b": {...}}
    player: {"slot": "QB", "first": "LAMAR", "last": "JACKSON", "nfl": "BAL",
             "pts": 37.82, "line": "3 Pass TD, 238 Pass Yds, 103 Rush Yds"}

Starters only (no bench / IR), in lineup order. A box score never changes once
the week is final, so each game is pulled exactly once and never again.

Two calls per game (one per team), the same per-team roster form pull_live.py
already uses, with the week pinned on the roster so past seasons return that
week's lineup rather than the final one:

    team/<team key>/roster;week=N/players/stats;type=week;week=N

The first run has about 100 games to pull; --limit (default 40 games = 80
calls) spreads that over a few runs. After that it is a no-op almost every
run - a new call only when a new game breaks into a record.

    python3 pull_boxscores.py                 # pull up to 40 missing games
    python3 pull_boxscores.py --limit 5 --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc

REPO = Path(__file__).resolve().parent
OUT = REPO / "docs" / "boxscores"
RECORDS = REPO / "docs" / "records.json"
MATCHUPS = REPO / "docs" / "history" / "matchups.json"
SEASONS = REPO / "docs" / "history" / "seasons.json"

SLOT_ORDER = ["QB", "WR", "RB", "TE", "W/R/T", "W/R", "W/T", "Q/W/R/T", "K", "DEF"]
SKIP_SLOTS = {"BN", "IR", "IR+", "NA", ""}

# Yahoo's NFL stat ids (the same in every season) -> the short labels Michael's
# championship box scores use. Points-allowed buckets are a flag (value 1), so
# they print without a count. Anything not listed is left out of the line.
STAT_LABEL = {
    "5": "Pass TD", "4": "Pass Yds", "6": "Int",
    "10": "Rush TD", "9": "Rush Yds",
    "13": "Rec TD", "12": "Rec Yds", "11": "Rec",
    "15": "Ret TD", "16": "2-PT", "18": "Fum Lost", "57": "Fum Ret TD",
    "23": "FG 50+", "22": "FG 40-49", "21": "FG 30-39", "20": "FG 20-29", "19": "FG 0-19",
    "29": "PAT Made", "30": "PAT Miss",
    "32": "Sack", "33": "Int", "34": "Fum Rec", "35": "TD", "36": "Safe", "37": "Blk Kick",
    "49": "Ret TD",
}
FLAG_LABEL = {"50": "Pts Allow 0", "51": "Pts Allow 1-6", "52": "Pts Allow 7-13", "53": "Pts Allow 14-20",
              "54": "Pts Allow 21-27", "55": "Pts Allow 28-34", "56": "Pts Allow 35+"}


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def wanted_ids():
    data = load(RECORDS, {})
    ids = []
    for c in data.get("categories", []):
        for r in c.get("records", []):
            lists = [r["rows"]] if "rows" in r else [e["rows"] for e in r.get("eras", [])]
            for rows in lists:
                for row in rows:
                    if row.get("g") and row["g"] not in ids:
                        ids.append(row["g"])
    return ids


def stat_line(raw):
    parts = []
    for sid, label in STAT_LABEL.items():
        v = yc.fnum((raw or {}).get(sid))
        if v:
            parts.append("%s %s" % (int(v) if v == int(v) else round(v, 1), label))
    for sid, label in FLAG_LABEL.items():
        if yc.fnum((raw or {}).get(sid)):
            parts.append(label)
    return ", ".join(parts)


def parse_team(payload):
    """one team's roster payload -> (team_name, starters in lineup order)"""
    team = (payload or {}).get("fantasy_content", {}).get("team")
    if not team:
        return "", []
    meta = yc.merge_meta(team[0] if isinstance(team[0], list) else team)
    roster = yc.find_key(team[1:], "roster")
    container = None
    if isinstance(roster, dict):
        container = (roster.get("0", {}).get("players") or roster.get("players"))
    players = []
    for pw in yc.numbered(container or {}):
        p = pw.get("player")
        if p is None:
            continue
        pmeta = yc.merge_meta(p[0] if isinstance(p[0], list) else p)
        slot = yc.merge_meta(yc.find_key(p[1:], "selected_position") or []).get("position", "")
        if slot in SKIP_SLOTS:
            continue
        name = pmeta.get("name") or {}
        first, last = yc.clean(name.get("first") or ""), yc.clean(name.get("last") or "")
        if slot == "DEF" or not last:            # a defence is just "Baltimore"
            first, last = "", yc.clean(name.get("full") or first)
        pts = yc.find_key(p[1:], "player_points") or {}
        players.append({
            "slot": slot, "first": first.upper(), "last": last.upper(),
            "nfl": pmeta.get("editorial_team_abbr", ""),
            "pts": round(yc.fnum(pts.get("total")), 2),
            "line": stat_line(yc._parse_stat_block(yc.find_key(p[1:], "player_stats"))),
        })
    rank = {s: i for i, s in enumerate(SLOT_ORDER)}
    players.sort(key=lambda q: rank.get(q["slot"], len(SLOT_ORDER)))   # stable: WR, WR stay in Yahoo order
    return yc.clean(meta.get("name", "")), players


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40, help="most games to pull this run (2 calls each)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    missing = [i for i in wanted_ids() if not (OUT / ("%s.json" % i)).exists()]
    if not missing:
        print("Box scores up to date.")
        return
    games = {}
    for g in load(MATCHUPS, {}).get("games", []):
        if g.get("type") == "consolation":
            continue
        gid = "%d-%d-%s-%s" % (g["season"], g["week"], g["a"]["team_key"].split(".t.")[-1], g["b"]["team_key"].split(".t.")[-1])
        games[gid] = g
    seasons = load(SEASONS, {}).get("seasons", [])
    rank_of = {(s["season"], t["manager"]): t["final_rank"] for s in seasons for t in s["teams"]}
    # history also holds the week being played: a game is only pulled once its
    # week is over (a later week exists, or the season is finished), because a
    # box score is never pulled twice
    open_season = {s["season"]: max([g["week"] for g in games.values() if g["season"] == s["season"]] or [0])
                   for s in seasons if not s.get("finished")}
    missing = [i for i in missing if not (games.get(i) and games[i]["season"] in open_season
                                          and games[i]["week"] >= open_season[games[i]["season"]])]
    if not missing:
        print("Box scores up to date (waiting on this week's games).")
        return

    token = yc.get_token()
    OUT.mkdir(parents=True, exist_ok=True)
    done = 0
    for gid in missing[:args.limit]:
        g = games.get(gid)
        if not g:
            print("  %s: not in matchups.json, skipped" % gid)
            continue
        out = {"id": gid, "season": g["season"], "week": g["week"], "type": g["type"], "round": ""}
        if g["type"] == "playoff":
            rk = {rank_of.get((g["season"], g["a"]["manager"])), rank_of.get((g["season"], g["b"]["manager"]))}
            out["round"] = "CHAMPIONSHIP" if rk == {1, 2} else "3RD PLACE" if rk == {3, 4} else "SEMIFINAL"
        ok = True
        for side in ("a", "b"):
            t = g[side]
            payload = yc.fetch("team/%s/roster;week=%d/players/stats;type=week;week=%d"
                               % (t["team_key"], g["week"], g["week"]), token)
            name, players = parse_team(payload)
            if not players:
                ok = False
            out[side] = {"manager": t["manager"], "team_name": name, "points": t["points"], "players": players}
        if not ok:
            print("  %s: Yahoo returned no starters, will retry next run" % gid)
            continue
        if not any(pl.get("line") for sd in ("a", "b") for pl in out[sd]["players"]):
            try:                                   # Yahoo has no stat lines before 2022: fill them from nflverse
                import nflverse_lines
                nflverse_lines.fill_box(out)
            except Exception as e:
                print("  %s: nflverse stat lines skipped (%s)" % (gid, e))
        if args.dry_run:
            print(json.dumps(out)[:400])
        else:
            (OUT / ("%s.json" % gid)).write_text(json.dumps(out, separators=(",", ":")) + "\n")
        done += 1
    print("Box scores: %d pulled, %d still to go." % (done, max(0, len(missing) - done)))


if __name__ == "__main__":
    main()
