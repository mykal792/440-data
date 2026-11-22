#!/usr/bin/env python3
"""
nflverse_lines.py - stat lines for box scores Yahoo sent without them.

Yahoo returns every starter's fantasy points for old seasons, but not the stats
behind them (no stat lines before 2022). nflverse publishes every NFL player's
weekly stats back to 1999 as free CSV files on GitHub, so this fills the gaps:

    stats_player_week_<season>.csv   passing, rushing, receiving, fumbles, kicking
    stats_team_week_<season>.csv     team defence: sacks, picks, recoveries, TDs
    games.csv                        final scores (points allowed for a DEF)

https://github.com/nflverse/nflverse-data - CC-BY 4.0.

Used two ways:
  - pull_boxscores.py calls fill_box(box) on a newly pulled game that came
    back with no stat lines, so new record games are filled automatically;
  - run by hand once to fill the box scores already saved:
        python3 nflverse_lines.py            # fill docs/boxscores/*.json
        python3 nflverse_lines.py --dry-run  # report only

Players are matched by name within that NFL week (a defence by its nickname).
A player it can't match keeps an empty line - never a guess. Each filled box
score gets "lines": "nflverse".
"""

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent
BOX = REPO / "docs" / "boxscores"
CACHE = Path("/tmp/nflverse")
BASE = "https://github.com/nflverse/nflverse-data/releases/download/"

TEAMS = {   # Yahoo's defence names (nickname or city) -> nflverse team codes over the years
    "CARDINALS": ["ARI"], "FALCONS": ["ATL"], "RAVENS": ["BAL"], "BILLS": ["BUF"], "PANTHERS": ["CAR"],
    "BEARS": ["CHI"], "BENGALS": ["CIN"], "BROWNS": ["CLE"], "COWBOYS": ["DAL"], "BRONCOS": ["DEN"],
    "LIONS": ["DET"], "PACKERS": ["GB"], "TEXANS": ["HOU"], "COLTS": ["IND"], "JAGUARS": ["JAX"],
    "CHIEFS": ["KC"], "RAIDERS": ["LV", "OAK"], "CHARGERS": ["LAC", "SD"], "RAMS": ["LA", "STL"],
    "DOLPHINS": ["MIA"], "VIKINGS": ["MIN"], "PATRIOTS": ["NE"], "SAINTS": ["NO"], "GIANTS": ["NYG"],
    "JETS": ["NYJ"], "EAGLES": ["PHI"], "STEELERS": ["PIT"], "49ERS": ["SF"], "SEAHAWKS": ["SEA"],
    "BUCCANEERS": ["TB"], "TITANS": ["TEN"], "COMMANDERS": ["WAS"], "REDSKINS": ["WAS"], "FOOTBALL TEAM": ["WAS"],
    "ARIZONA": ["ARI"], "ATLANTA": ["ATL"], "BALTIMORE": ["BAL"], "BUFFALO": ["BUF"], "CAROLINA": ["CAR"],
    "CHICAGO": ["CHI"], "CINCINNATI": ["CIN"], "CLEVELAND": ["CLE"], "DALLAS": ["DAL"], "DENVER": ["DEN"],
    "DETROIT": ["DET"], "GREEN BAY": ["GB"], "HOUSTON": ["HOU"], "INDIANAPOLIS": ["IND"], "JACKSONVILLE": ["JAX"],
    "KANSAS CITY": ["KC"], "LAS VEGAS": ["LV"], "OAKLAND": ["OAK"], "MIAMI": ["MIA"], "MINNESOTA": ["MIN"],
    "NEW ENGLAND": ["NE"], "NEW ORLEANS": ["NO"], "PHILADELPHIA": ["PHI"], "PITTSBURGH": ["PIT"],
    "SAN FRANCISCO": ["SF"], "SEATTLE": ["SEA"], "TAMPA BAY": ["TB"], "TENNESSEE": ["TEN"], "WASHINGTON": ["WAS"],
    "SAN DIEGO": ["SD"], "ST. LOUIS": ["STL"], "ST LOUIS": ["STL"],
    "NEW YORK GIANTS": ["NYG"], "NEW YORK JETS": ["NYJ"], "LOS ANGELES RAMS": ["LA"], "LOS ANGELES CHARGERS": ["LAC"],
}
OFFENSE = {"QB", "RB", "FB", "WR", "TE", "K"}
POS = {"QB": {"QB"}, "RB": {"RB", "FB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"},
       "W/R": {"WR", "RB", "FB"}, "W/T": {"WR", "TE"}, "W/R/T": {"WR", "RB", "FB", "TE"}, "Q/W/R/T": OFFENSE}
ALIAS = {"HOLLYWOOD BROWN": "MARQUISE BROWN"}          # Yahoo name -> nflverse name
SUFFIX = re.compile(r"\b(JR|SR|II|III|IV|V)\b")


def norm(name):
    s = SUFFIX.sub("", re.sub(r"[^A-Z ]", "", str(name).upper().replace("-", " ")))
    return re.sub(r"\s+", " ", s).strip()


def fetch_csv(rel):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / rel.replace("/", "_")
    if not path.exists():
        with urllib.request.urlopen(BASE + rel, timeout=60) as r:
            path.write_bytes(r.read())
    return list(csv.DictReader(io.StringIO(path.read_text())))


def n(row, *cols):
    t = 0.0
    for c in cols:
        try:
            t += float(row.get(c) or 0)
        except ValueError:
            pass
    return t


def fmt(v):
    return str(int(v)) if v == int(v) else str(round(v, 1))


def offense_line(r):
    parts = []
    for label, cols in (("Pass TD", ["passing_tds"]), ("Pass Yds", ["passing_yards"]), ("Int", ["passing_interceptions"]),
                        ("Rush TD", ["rushing_tds"]), ("Rush Yds", ["rushing_yards"]),
                        ("Rec TD", ["receiving_tds"]), ("Rec Yds", ["receiving_yards"]), ("Rec", ["receptions"]),
                        ("Ret TD", ["special_teams_tds"]),
                        ("2-PT", ["passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions"]),
                        ("Fum Lost", ["rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost"]),
                        ("FG 50+", ["fg_made_50_59", "fg_made_60_"]), ("FG 40-49", ["fg_made_40_49"]),
                        ("FG 30-39", ["fg_made_30_39"]), ("FG 20-29", ["fg_made_20_29"]), ("FG 0-19", ["fg_made_0_19"]),
                        ("PAT Made", ["pat_made"]), ("PAT Miss", ["pat_missed"])):
        v = n(r, *cols)
        if v:
            parts.append("%s %s" % (fmt(v), label))
    return ", ".join(parts)


def defense_line(t, allowed=None):
    parts = []
    for label, cols in (("Sack", ["def_sacks"]), ("Int", ["def_interceptions"]), ("Fum Rec", ["fumble_recovery_opp"]),
                        ("TD", ["def_tds", "fumble_recovery_tds"]), ("Safe", ["def_safeties"]),
                        ("Blk Kick", ["def_fg_blocks", "def_punt_blocks", "def_pat_blocks"]), ("Ret TD", ["special_teams_tds"])):
        v = n(t, *cols)
        if v:
            parts.append("%s %s" % (fmt(v), label))
    return ", ".join(parts)


class Season:
    def __init__(self, season):
        self.season = season
        self.players = defaultdict(list)          # (week, normalized name) -> rows
        self.lastname = defaultdict(list)         # (week, normalized last name) -> rows
        for r in fetch_csv("stats_player/stats_player_week_%d.csv" % season):
            wk = int(r["week"])
            nm = norm(r.get("player_display_name") or r.get("player_name"))
            self.players[(wk, nm)].append(r)
            if nm:
                self.lastname[(wk, nm.split(" ")[-1])].append(r)
        self.teams = {(int(r["week"]), r["team"]): r for r in fetch_csv("stats_team/stats_team_week_%d.csv" % season)}
        self.allowed = {}
        for g in fetch_csv("schedules/games.csv"):
            if int(g["season"]) != season or not g["home_score"]:
                continue
            wk = int(g["week"])
            self.allowed[(wk, g["home_team"])] = float(g["away_score"])
            self.allowed[(wk, g["away_team"])] = float(g["home_score"])

    def line(self, week, p):
        if p.get("slot") == "DEF" or not p.get("first"):
            name = norm(p.get("last"))
            for code in TEAMS.get(str(p.get("last", "")).upper().strip(), TEAMS.get(name, [])):
                t = self.teams.get((week, code))
                if t:
                    return defense_line(t, self.allowed.get((week, code)))
            return ""
        pos = POS.get(p.get("slot"), OFFENSE)
        full = norm(ALIAS.get(norm(p["first"] + " " + p["last"]), p["first"] + " " + p["last"]))
        rows = [r for r in self.players.get((week, full), []) if r.get("position") in pos]   # two David Johnsons
        if len(rows) != 1:                                   # nicknames ("Mitch" Trubisky): last name, if unique that week
            cand = [r for r in self.lastname.get((week, full.split(" ")[-1]), []) if r.get("position") in pos]
            rows = cand if len(cand) == 1 else []
        return offense_line(rows[0]) if rows else ""


_SEASONS = {}


def fill_box(box):
    """fill empty stat lines in one box score (in place); returns how many it filled"""
    season, week = box["season"], box["week"]
    if season not in _SEASONS:
        _SEASONS[season] = Season(season)
    s, filled = _SEASONS[season], 0
    for side in ("a", "b"):
        for p in box[side]["players"]:
            if p.get("line"):
                continue
            ln = s.line(week, p)
            if ln:
                p["line"] = ln
                filled += 1
    if filled:
        box["lines"] = "nflverse"
    return filled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    total = missed = 0
    for f in sorted(BOX.glob("*.json")):
        box = json.loads(f.read_text())
        if box.get("source"):                                # Michael's hand-typed title games
            continue
        empty = [p for sd in ("a", "b") for p in box[sd]["players"] if not p.get("line")]
        if not empty:
            continue
        got = fill_box(box)
        total += got
        left = [p for sd in ("a", "b") for p in box[sd]["players"] if not p.get("line")]
        missed += len(left)
        if left:
            print("  %s: no line for %s" % (f.stem, ", ".join("%s %s (%s)" % (p["first"], p["last"], p["pts"]) for p in left)))
        if got and not args.dry_run:
            f.write_text(json.dumps(box, separators=(",", ":")) + "\n")
    print("Filled %d stat lines; %d players still without one." % (total, missed))


if __name__ == "__main__":
    sys.exit(main())
