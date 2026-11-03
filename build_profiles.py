#!/usr/bin/env python3
"""build_profiles.py - every number on the player profile pages.

Reads, never calls Yahoo:
    docs/history/matchups.json   every game since 2013   (pull_history.py)
    docs/history/seasons.json    every season's teams    (pull_history.py)
    docs/season.json             this season's bank      (pull_standings.py)
    docs/dots.json               this season's dots      (pull_dots.py)
    profile-baseline.json        hand-owned: money and weekly category wins
                                 up to the end of last season

Writes:
    docs/profiles/<manager>.json one file per manager (~35 KB), so a profile
                                 page loads only its own player

    python3 build_profiles.py --dry-run     # print the summary, write nothing
    python3 build_profiles.py

THE RULES (decided 2026-11-03):
  - Half-point PPR started in 2018 and lifted every score, so career AVERAGES
    and LOW SCORES (career and playoff) count 2018 onward only. The playoff
    average counts every year. Everything else counts every season.
  - Career record, win %, averages, high/low score, blowout, high scores and
    weeks #1 are REGULAR SEASON games only. Playoffs have their own section.
  - "High scores" = weeks with the top score in the league (ties count for
    everyone tied). "Weeks #1" = regular-season weeks spent first in the
    standings after that week's games (wins, then points for).
  - Versus counts EVERY game against that manager: regular, playoff and
    consolation.
  - Ranks are among the ten current managers. Most is #1, except LOW SCORE,
    where the lowest is #1. Ties share a rank and show as "T-9".
  - The current season is included in career numbers as soon as each week is
    final, so career stats move every week.

Only rewrites a file when something other than its timestamp changed, so the live
workflow doesn't commit an identical file every five minutes.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc

REPO = yc.REPO
HISTORY = REPO / "docs" / "history"
OUT_DIR = REPO / "docs" / "profiles"
BASELINE = REPO / "profile-baseline.json"
PPR_FROM = 2018

CURRENT = list(yc.MANAGER_KEYS)
FORMER_NAMES = {"cam": "CAM", "steve": "STEVE", "patrick": "PATRICK"}


def name_of(key):
    return yc.DISPLAY.get(key) or FORMER_NAMES.get(key) or key.upper()


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        if default is not None:
            return default
        raise


# --- game rows from one manager's side -----------------------------------------

def sides(games):
    """Yield (game, me, opp) twice per game, once from each side."""
    for g in games:
        yield g, g["a"], g["b"]
        yield g, g["b"], g["a"]


def result(me, opp):
    if me["points"] > opp["points"]:
        return "W"
    if me["points"] < opp["points"]:
        return "L"
    return "T"


def ref(g, me, opp, value=None):
    return {"value": round(value if value is not None else me["points"], 2),
            "season": g["season"], "week": g["week"],
            "opponent": opp["manager"], "opponent_name": name_of(opp["manager"])}


# --- ranks -------------------------------------------------------------------

def rank_all(values, lowest_first=False):
    """{key: number or None} -> {key: "#3" / "#T-9" / None}"""
    have = {k: v for k, v in values.items() if v is not None}
    out = {k: None for k in values}
    for k, v in have.items():
        better = sum(1 for o in have.values() if (o < v if lowest_first else o > v))
        tied = sum(1 for o in have.values() if o == v) > 1
        out[k] = ("T-%d" if tied else "%d") % (better + 1)
    return out


# --- standings, week by week ---------------------------------------------------

def weeks_at_first(regular, seasons):
    """{manager: count} of regular-season weeks spent first in the standings."""
    count = defaultdict(int)
    by_season = defaultdict(lambda: defaultdict(list))
    for g in regular:
        by_season[g["season"]][g["week"]].append(g)
    for season, weeks in by_season.items():
        rec = defaultdict(lambda: [0.0, 0.0])          # wins, points for
        for week in sorted(weeks):
            for g, me, opp in sides(weeks[week]):
                r = rec[me["manager"]]
                r[0] += {"W": 1, "T": 0.5, "L": 0}[result(me, opp)]
                r[1] += me["points"]
            leader = max(rec.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
            count[leader] += 1
    return count


def streak(rows):
    """rows in date order -> 'W3' / 'L2' / None"""
    if not rows:
        return None
    last = rows[-1]["result"]
    n = 0
    for r in reversed(rows):
        if r["result"] != last:
            break
        n += 1
    return "%s%d" % (last, n)


# --- main build ----------------------------------------------------------------

def build():
    games = load(HISTORY / "matchups.json")["games"]
    seasons = {s["season"]: s for s in load(HISTORY / "seasons.json")["seasons"]}
    baseline = load(BASELINE, {})
    season_feed = load(REPO / "docs" / "season.json", {})
    dots_feed = load(REPO / "docs" / "dots.json", {})

    this_season = max(seasons)
    regular = [g for g in games if g["type"] == "regular"]
    playoff = [g for g in games if g["type"] == "playoff"]

    # weekly top score, regular season
    week_top = defaultdict(float)
    for g, me, opp in sides(regular):
        k = (g["season"], g["week"])
        week_top[k] = max(week_top[k], me["points"])

    first = weeks_at_first(regular, seasons)

    # this season's money and dots
    bonuses = season_feed.get("bonuses", {})
    bank_now = {m: round(sum(b.get("amount", 0) for b in bonuses.get(m, [])), 2)
                for m in CURRENT}
    category_now = {m: sum(1 for b in bonuses.get(m, []) if b.get("type") == "category")
                    for m in CURRENT}
    dots_now = {m: sum((dots_feed.get("totals", {}).get(m) or {}).values())
                for m in CURRENT}

    base_season = baseline.get("through_season", this_season - 1)
    add_current = this_season > base_season
    dues = baseline.get("season_dues") or 0

    P = {}
    for m in CURRENT:
        mine = [(g, me, opp) for g, me, opp in sides(regular) if me["manager"] == m]
        ppr = [(g, me, opp) for g, me, opp in mine if g["season"] >= PPR_FROM]
        w = sum(1 for g, me, opp in mine if result(me, opp) == "W")
        l = sum(1 for g, me, opp in mine if result(me, opp) == "L")
        t = sum(1 for g, me, opp in mine if result(me, opp) == "T")

        hi = max(mine, key=lambda x: x[1]["points"])
        lo = min(ppr, key=lambda x: x[1]["points"]) if ppr else None
        blow = max(mine, key=lambda x: x[1]["points"] - x[2]["points"])

        earnings = baseline.get("earnings", {}).get(m)
        if earnings is not None and add_current:
            earnings = round(earnings + bank_now[m] - dues, 2)
        category = baseline.get("weekly_category", {}).get(m)
        if category is not None and add_current:
            category += category_now[m]

        career = {
            "wins": w, "losses": l, "ties": t,
            "win_pct": round((w + 0.5 * t) / max(w + l + t, 1), 3),
            "high_scores": sum(1 for g, me, opp in mine
                               if me["points"] == week_top[(g["season"], g["week"])]),
            "weeks_first": first.get(m, 0),
            "blowout": ref(*blow, value=blow[1]["points"] - blow[2]["points"]),
            "earnings": earnings,
            "avg": round(sum(me["points"] for _, me, _ in ppr) / len(ppr), 2) if ppr else None,
            "avg_against": round(sum(o["points"] for _, _, o in ppr) / len(ppr), 2) if ppr else None,
            "high": ref(*hi),
            "low": ref(*lo) if lo else None,
        }

        # this season
        now_rows = [(g, me, opp) for g, me, opp in mine if g["season"] == this_season]
        now_team = next((t for t in seasons[this_season]["teams"] if t["manager"] == m), {})
        cur = {
            "season": this_season,
            "weeks_played": len(now_rows),
            "standing": now_team.get("final_rank"),
            "wins": sum(1 for g, me, o in now_rows if result(me, o) == "W"),
            "losses": sum(1 for g, me, o in now_rows if result(me, o) == "L"),
            "ties": sum(1 for g, me, o in now_rows if result(me, o) == "T"),
            "avg": round(sum(me["points"] for _, me, _ in now_rows) / len(now_rows), 2) if now_rows else None,
            "avg_against": round(sum(o["points"] for _, _, o in now_rows) / len(now_rows), 2) if now_rows else None,
            "high_scores": sum(1 for g, me, opp in now_rows
                               if me["points"] == week_top[(g["season"], g["week"])]),
            "streak": streak([{"result": result(me, o)} for g, me, o in
                              sorted(now_rows, key=lambda x: x[0]["week"])]),
            "dots": dots_now[m],
            "bank": bank_now[m],
        }

        # season history, newest first, each with its games
        history = []
        for season in sorted(seasons, reverse=True):
            team = next((t for t in seasons[season]["teams"] if t["manager"] == m), None)
            if team is None:
                continue
            season_games = sorted(
                [(g, me, opp) for g, me, opp in sides(games)
                 if g["season"] == season and me["manager"] == m],
                key=lambda x: x[0]["week"])
            reg = [x for x in season_games if x[0]["type"] == "regular"]
            history.append({
                "season": season,
                "team_name": team["team_name"],
                "wins": sum(1 for g, me, o in reg if result(me, o) == "W"),
                "losses": sum(1 for g, me, o in reg if result(me, o) == "L"),
                "ties": sum(1 for g, me, o in reg if result(me, o) == "T"),
                "playoffs": any(g["type"] == "playoff" for g, _, _ in season_games),
                "avg": round(sum(me["points"] for _, me, _ in reg) / len(reg), 2) if reg else None,
                "avg_against": round(sum(o["points"] for _, _, o in reg) / len(reg), 2) if reg else None,
                "finish": team["final_rank"],
                "in_progress": not seasons[season]["finished"],
                "games": [{"week": g["week"], "type": g["type"],
                           "opponent": o["manager"], "opponent_name": name_of(o["manager"]),
                           "points": me["points"], "opp_points": o["points"],
                           "result": result(me, o),
                           "high_score": (g["type"] == "regular" and me["points"]
                                          == week_top[(g["season"], g["week"])])}
                          for g, me, o in season_games],
            })

        # playoffs (championship bracket only - consolation isn't the playoffs)
        po = [(g, me, opp) for g, me, opp in sides(playoff) if me["manager"] == m]
        po_ppr = [x for x in po if x[0]["season"] >= PPR_FROM]
        finishes = [t["final_rank"] for s in seasons.values() if s["finished"]
                    for t in s["teams"] if t["manager"] == m]
        playoffs = {
            "appearances": len({g["season"] for g, _, _ in po}),
            "wins": sum(1 for g, me, o in po if result(me, o) == "W"),
            "losses": sum(1 for g, me, o in po if result(me, o) == "L"),
            # playoff average counts every year; playoff LOW is 2018+ (PPR)
            "avg": round(sum(me["points"] for _, me, _ in po) / len(po), 2) if po else None,
            "high": ref(*max(po, key=lambda x: x[1]["points"])) if po else None,
            "low": ref(*min(po_ppr, key=lambda x: x[1]["points"])) if po_ppr else None,
            "trophies": {"first": finishes.count(1), "second": finishes.count(2),
                         "third": finishes.count(3)},
            "titles": sorted(s["season"] for s in seasons.values() if s["finished"]
                             for t in s["teams"] if t["manager"] == m and t["final_rank"] == 1),
        }

        # versus - every game type
        vs = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0})
        for g, me, o in sides(games):
            if me["manager"] == m:
                r = result(me, o)
                vs[o["manager"]][{"W": "wins", "L": "losses", "T": "ties"}[r]] += 1
        versus = [dict(opponent=k, opponent_name=name_of(k), **v)
                  for k, v in vs.items() if k in CURRENT]
        versus.sort(key=lambda v: CURRENT.index(v["opponent"]))
        former = [dict(opponent=k, opponent_name=name_of(k), **v)
                  for k, v in vs.items() if k not in CURRENT]

        P[m] = {"key": m, "name": name_of(m),
                "team_name": now_team.get("team_name"),
                "season": cur, "career": career, "seasons": history,
                "playoffs": playoffs, "versus": versus, "versus_former": former,
                "bonus": {"weekly_category": category}}

    # ranks
    def put(section, field, getter, lowest_first=False):
        ranks = rank_all({m: getter(P[m]) for m in CURRENT}, lowest_first)
        for m in CURRENT:
            P[m][section].setdefault("ranks", {})[field] = ranks[m]

    put("career", "win_pct", lambda p: p["career"]["win_pct"])
    put("career", "high_scores", lambda p: p["career"]["high_scores"])
    put("career", "weeks_first", lambda p: p["career"]["weeks_first"])
    put("career", "blowout", lambda p: p["career"]["blowout"]["value"])
    put("career", "earnings", lambda p: p["career"]["earnings"])
    put("career", "avg", lambda p: p["career"]["avg"])
    put("career", "avg_against", lambda p: p["career"]["avg_against"])
    put("career", "high", lambda p: p["career"]["high"]["value"])
    put("career", "low", lambda p: (p["career"]["low"] or {}).get("value"), lowest_first=True)
    put("season", "avg", lambda p: p["season"]["avg"])
    put("season", "avg_against", lambda p: p["season"]["avg_against"])
    put("season", "high_scores", lambda p: p["season"]["high_scores"])
    put("season", "dots", lambda p: p["season"]["dots"])
    put("season", "bank", lambda p: p["season"]["bank"])
    put("bonus", "weekly_category", lambda p: p["bonus"]["weekly_category"])

    last = max(((g["season"], g["week"]) for g in games), default=(this_season, 0))
    return {"through": {"season": last[0], "week": last[1]},
            "rules": {"ppr_from": PPR_FROM},
            "order": CURRENT, "managers": P}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = build()
    for m in data["order"]:
        p = data["managers"][m]
        c, s, po = p["career"], p["season"], p["playoffs"]
        print("%-10s %3d-%-3d %.3f  avg %6s  ag %6s  hi %6.2f  lo %6s  HS %2d  #1 %2d  $%s"
              "  | %d now: #%s %d-%d %s  | PO %d-%d  %d/%d/%d"
              % (m, c["wins"], c["losses"], c["win_pct"], c["avg"], c["avg_against"],
                 c["high"]["value"], (c["low"] or {}).get("value"), c["high_scores"],
                 c["weeks_first"], c["earnings"], s["season"], s["standing"],
                 s["wins"], s["losses"], s["streak"], po["wins"], po["losses"],
                 po["trophies"]["first"], po["trophies"]["second"], po["trophies"]["third"]))

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for m in data["order"]:
        body = {"through": data["through"], "rules": data["rules"],
                "order": data["order"], **data["managers"][m]}
        path = OUT_DIR / ("%s.json" % m)
        old = load(path, {})
        if {k: v for k, v in old.items() if k != "updated"} == body:
            continue
        path.write_text(json.dumps({"updated": yc.now_iso(), **body}, indent=1) + "\n")
        written += 1
    print("profiles: %d of %d files changed." % (written, len(data["order"])))


if __name__ == "__main__":
    main()
