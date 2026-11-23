#!/usr/bin/env python3
"""build_records.py - every number in the Record Book (docs/record-book.html).

Reads, never calls Yahoo:
    docs/history/matchups.json   every game since 2013   (pull_history.py)
    docs/history/seasons.json    each season's teams      (pull_history.py)
    docs/profiles/<m>.json       money and bonus totals   (build_profiles.py)
Writes docs/records.json. Run after build_profiles.py.

Rules (same as the profiles):
  - Single-game and season records count everyone who ever played, including
    former managers. Career, playoff-career and money lists rank the ten
    current managers only.
  - Regular season only unless the record says playoffs. Playoff games are the
    4-team bracket (semis, final, 3rd-place game); consolation games never count.
  - Half-PPR began in 2018 (league scoring jumps ~15 points a team that year),
    so every LOW record is kept twice: PPR (2018+) and PRE-PPR (2013-17).
  - Season records use finished seasons only. Game, career and streak records
    include the live season.
  - Up to 10 places. Ties share a rank ("T-3").
"""
import json
from collections import defaultdict
from pathlib import Path

import yahoo_common as yc

REPO = Path(__file__).resolve().parent
HISTORY = REPO / "docs" / "history"
OUT = REPO / "docs" / "records.json"
PPR_FROM = 2018
TOP = 10
CURRENT = list(yc.MANAGER_KEYS)


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def f2(x):
    return "%.2f" % x


def comma(x, dp=2):
    return "{:,.{}f}".format(x, dp)


def pct(x):
    return "1.000" if x >= 1 else ("%.3f" % x).lstrip("0")


def signed(x):
    return ("+" if x >= 0 else "−") + "%.2f" % abs(x)


def wk(season, week):
    return "WK %d, %d" % (week, season)


def ranked(items, key, low=False, limit=TOP):
    """items: dicts with a sort value at items[key]. Ranks with ties, keeps the
    first `limit` rows (best first; equal values keep the given order)."""
    items = sorted(items, key=lambda r: r[key] if low else -r[key])
    vals = [r[key] for r in items]
    out = []
    for r in items[:limit]:
        v = r[key]
        better = sum(1 for o in vals if (o < v if low else o > v))
        tied = vals.count(v) > 1
        row = {k: val for k, val in r.items() if k != key}
        row["r"] = ("T-%d" if tied else "%d") % (better + 1)
        out.append(row)
    return out


def eras(items, key, low=True):
    """the same record twice: PPR (2018+) first, then PRE-PPR (2013-17)."""
    return [{"label": "PPR · 2018+", "rows": ranked([i for i in items if i["_y"] >= PPR_FROM], key, low)},
            {"label": "PRE-PPR · 2013–17", "rows": ranked([i for i in items if i["_y"] < PPR_FROM], key, low)}]


def strip(rows):
    for r in rows:
        r.pop("_y", None)
    return rows


def build():
    games = load(HISTORY / "matchups.json")["games"]
    seasons = sorted(load(HISTORY / "seasons.json")["seasons"], key=lambda s: s["season"])
    this_season = seasons[-1]["season"]
    finished = [s for s in seasons if s["finished"]]
    profiles = {m: load(REPO / "docs" / "profiles" / ("%s.json" % m), {}) for m in CURRENT}

    reg = sorted([g for g in games if g["type"] == "regular"], key=lambda g: (g["season"], g["week"]))
    po = sorted([g for g in games if g["type"] == "playoff"], key=lambda g: (g["season"], g["week"]))

    def sides(g):
        yield g["a"], g["b"]
        yield g["b"], g["a"]

    def res(me, opp):
        return "W" if me["points"] > opp["points"] else "L" if me["points"] < opp["points"] else "T"

    # every team-week: who scored what, and where it finished that week
    week_scores = defaultdict(list)
    for g in reg:
        for me, _ in sides(g):
            week_scores[(g["season"], g["week"])].append(me["points"])
    week_top = {k: max(v) for k, v in week_scores.items()}
    week_third = {k: sorted(v, reverse=True)[min(2, len(v) - 1)] for k, v in week_scores.items()}

    # a manager-season's regular-season line; finished seasons only for season records
    line = defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "g": 0, "hs": 0, "t3": 0, "opps": []})
    for g in reg:
        for me, opp in sides(g):
            L = line[(g["season"], me["manager"])]
            r = res(me, opp)
            L[{"W": "w", "L": "l", "T": "t"}[r]] += 1
            L["pf"] += me["points"]; L["pa"] += opp["points"]; L["g"] += 1
            L["hs"] += me["points"] == week_top[(g["season"], g["week"])]
            L["t3"] += me["points"] >= week_third[(g["season"], g["week"])]
            L["opps"].append(opp["manager"])
    lg_avg = {}
    for y in {y for y, _ in line}:
        rows = [L for (yy, _), L in line.items() if yy == y]
        lg_avg[y] = sum(L["pf"] for L in rows) / sum(L["g"] for L in rows)
    ppg = {k: L["pf"] / L["g"] for k, L in line.items()}

    def sos(y, m):
        """how good your opponents were: their season scoring average minus the
        league's, averaged over your schedule. Era-proof, so one list."""
        opps = line[(y, m)]["opps"]
        return sum(ppg[(y, o)] - lg_avg[y] for o in opps) / len(opps)

    done = {s["season"] for s in finished}
    srows = [dict(_y=y, m=m, **L) for (y, m), L in line.items() if y in done]

    # ---------------------------------------------------------------- single game
    def gid(g):
        """the game's box score id: season-week-<team id a>-<team id b> (docs/boxscores/<id>.json)"""
        return "%d-%d-%s-%s" % (g["season"], g["week"], g["a"]["team_key"].split(".t.")[-1], g["b"]["team_key"].split(".t.")[-1])

    def vs(me, opp, label=""):
        """the detail line under a one-team game row: who it was against and how it went"""
        r = "WON" if me["points"] > opp["points"] else "LOST" if me["points"] < opp["points"] else "TIED"
        return "%s%s VS %s, %s" % (label + " \u00b7 " if label else "", r,
                                   yc.DISPLAY.get(opp["manager"], opp["manager"].upper()), f2(opp["points"]))

    team_games = [{"_y": g["season"], "m": me["manager"], "v": f2(me["points"]), "x": me["points"],
                   "s": wk(g["season"], g["week"]), "d": vs(me, opp), "g": gid(g)} for g in reg for me, opp in sides(g)]
    rank_of = {(s["season"], t["manager"]): t["final_rank"] for s in seasons for t in s["teams"]}

    def po_round(g):
        rk = {rank_of.get((g["season"], g["a"]["manager"])), rank_of.get((g["season"], g["b"]["manager"]))}
        return "CHAMPIONSHIP" if rk == {1, 2} else "3RD PLACE" if rk == {3, 4} else "SEMIFINAL"
    pairs = []
    for g in reg + po:
        w, l = (g["a"], g["b"]) if g["a"]["points"] >= g["b"]["points"] else (g["b"], g["a"])
        pairs.append({"_y": g["season"], "_po": g["type"] == "playoff", "m": w["manager"], "m2": l["manager"],
                      "p": [f2(w["points"]), f2(l["points"])], "s": wk(g["season"], g["week"]), "g": gid(g),
                      "d": (po_round(g) + " \u00b7 " if g["type"] == "playoff" else "") + "%s \u2013 %s" % (f2(w["points"]), f2(l["points"])),
                      "sum": round(w["points"] + l["points"], 2), "mar": round(w["points"] - l["points"], 2)})

    def pair_rows(po_flag, key, low, fmt, wins_only=False):
        items = [dict(p, x=p[key], v=fmt(p[key])) for p in pairs
                 if p["_po"] == po_flag and (not wins_only or p["mar"] > 0)]
        for i in items:
            i.pop("sum"); i.pop("mar")
        return items

    def finish(items):
        for i in items:
            i.pop("_po", None)
        return items

    single = [
        {"key": "most-points", "title": "MOST POINTS", "rows": strip(ranked(team_games, "x"))},
        {"key": "fewest-points", "title": "FEWEST POINTS", "eras": [dict(e, rows=strip(e["rows"])) for e in eras(team_games, "x")]},
        {"key": "high-combined", "title": "HIGHEST COMBINED", "pair": True,
         "rows": strip(finish(ranked(pair_rows(False, "sum", False, f2), "x")))},
        {"key": "low-combined", "title": "LOWEST COMBINED", "pair": True,
         "eras": [dict(e, rows=strip(finish(e["rows"]))) for e in eras(pair_rows(False, "sum", True, f2), "x")]},
        {"key": "blowout", "title": "BIGGEST BLOWOUT", "pair": True,
         "rows": strip(finish(ranked(pair_rows(False, "mar", False, lambda v: "+" + f2(v)), "x")))},
        {"key": "narrow", "title": "NARROWEST WIN", "pair": True,
         "rows": strip(finish(ranked(pair_rows(False, "mar", True, lambda v: "+" + f2(v), True), "x", low=True)))},
    ]

    # ---------------------------------------------------------------- season
    def srec(L):
        return "%d-%d" % (L["w"], L["l"]) + ("-%d" % L["t"] if L["t"] else "")

    def season_list(fn, low=False, fmt=f2, era=False, sub=None, rec=False):
        items = [{"_y": r["_y"], "m": r["m"], "x": fn(r), "v": srec(r) if rec else fmt(fn(r)),
                  "s": (sub(r) if sub else str(r["_y"])),
                  "d": ("" if rec else srec(r) + " \u00b7 ") + "%s PTS FOR \u00b7 %s AGAINST" % (comma(r["pf"]), comma(r["pa"]))} for r in srows]
        if era:
            return {"eras": [dict(e, rows=strip(e["rows"])) for e in eras(items, "x", low)]}
        return {"rows": strip(ranked(items, "x", low))}

    season = [
        dict({"key": "most-wins", "title": "MOST WINS"},
             **season_list(lambda r: r["w"] + 0.5 * r["t"], rec=True)),
        dict({"key": "most-losses", "title": "MOST LOSSES"},
             **season_list(lambda r: r["l"] + 0.5 * r["t"], rec=True)),
        dict({"key": "most-ppg", "title": "MOST POINTS", "unit": "AVG PPG"},
             **season_list(lambda r: round(r["pf"] / r["g"], 2))),
        dict({"key": "fewest-ppg", "title": "FEWEST POINTS", "unit": "AVG PPG"},
             **season_list(lambda r: round(r["pf"] / r["g"], 2), low=True, era=True)),
        dict({"key": "most-pa", "title": "MOST POINTS AGAINST", "unit": "AVG PPG"},
             **season_list(lambda r: round(r["pa"] / r["g"], 2))),
        dict({"key": "fewest-pa", "title": "FEWEST POINTS AGAINST", "unit": "AVG PPG"},
             **season_list(lambda r: round(r["pa"] / r["g"], 2), low=True, era=True)),
        dict({"key": "hardest-sched", "title": "HARDEST SCHEDULE", "unit": "VS LEAGUE AVG"},
             **season_list(lambda r: round(sos(r["_y"], r["m"]), 2), fmt=signed)),
        dict({"key": "easiest-sched", "title": "EASIEST SCHEDULE", "unit": "VS LEAGUE AVG"},
             **season_list(lambda r: round(sos(r["_y"], r["m"]), 2), low=True, fmt=signed)),
        dict({"key": "season-high-scores", "title": "MOST HIGH SCORES"},
             **season_list(lambda r: r["hs"], fmt=str)),
        dict({"key": "season-top3", "title": "MOST TOP 3 SCORES"},
             **season_list(lambda r: r["t3"], fmt=str)),
    ]

    # ---------------------------------------------------------------- career (current ten)
    car = {m: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "g": 0, "hs": 0, "t3": 0, "sos": 0.0} for m in CURRENT}
    for (y, m), L in line.items():
        if m not in car: continue
        c = car[m]
        for k in ("w", "l", "t", "pf", "pa", "g", "hs", "t3"):
            c[k] += L[k]
        c["sos"] += sos(y, m) * L["g"]
    for c in car.values():
        c["pct"] = (c["w"] + 0.5 * c["t"]) / max(c["g"], 1)
        c["sos"] = c["sos"] / max(c["g"], 1)
    # season titles from finished seasons
    titles = {k: defaultdict(list) for k in ("reg", "pts", "win", "lose", "last", "champ", "medal")}
    for s in finished:
        y = s["season"]
        t = s["teams"]
        best = max(t, key=lambda x: (x["wins"] + 0.5 * x["ties"], x["points_for"]))
        titles["reg"][best["manager"]].append(y)
        titles["pts"][max(t, key=lambda x: x["points_for"])["manager"]].append(y)
        for x in t:
            if x["wins"] > x["losses"]: titles["win"][x["manager"]].append(y)
            if x["wins"] < x["losses"]: titles["lose"][x["manager"]].append(y)
            if x["final_rank"] == len(t): titles["last"][x["manager"]].append(y)
            if x["final_rank"] == 1: titles["champ"][x["manager"]].append(y)
            if x["final_rank"] <= 3: titles["medal"][x["manager"]].append(y)

    def yrs(ys):
        ys = sorted(ys)
        return " ".join("'%02d" % (y % 100) for y in ys)

    def career_list(fn, low=False, fmt=str, sub=None):
        items = [{"m": m, "x": fn(m), "v": fmt(fn(m)), "s": sub(m) if sub else ""} for m in CURRENT]
        return ranked(items, "x", low)

    def crec(m):
        c = car[m]
        return "%d-%d" % (c["w"], c["l"]) + ("-%d" % c["t"] if c["t"] else "")

    # weeks #1: finished regular seasons, first in the standings after each week's games
    # (wins, ties as half, then points for). Before week 1 everyone is tied, so it isn't counted.
    wk1, wk1_season = defaultdict(int), defaultdict(int)
    by_week = defaultdict(list)
    for g in reg:
        if g["season"] in done:
            by_week[(g["season"], g["week"])].append(g)
    standing = defaultdict(lambda: [0.0, 0.0])
    for (y, w) in sorted(by_week):
        for g in by_week[(y, w)]:
            for me, opp in sides(g):
                r = standing[(y, me["manager"])]
                r[0] += {"W": 1, "T": 0.5, "L": 0}[res(me, opp)]
                r[1] += me["points"]
        lead = max(((m, r) for (yy, m), r in standing.items() if yy == y), key=lambda kv: (kv[1][0], kv[1][1]))[0]
        wk1[lead] += 1; wk1_season[(y, lead)] += 1

    def wk1_best(m):
        mine = {y: n for (y, mm), n in wk1_season.items() if mm == m}
        if not mine: return ""
        top = max(mine.values())
        ys = sorted(y for y, n in mine.items() if n == top)
        return "BEST: %d IN %s" % (top, " ".join("'%02d" % (y % 100) for y in ys))

    career = [
        {"key": "wins", "title": "MOST WINS", "rows": career_list(lambda m: car[m]["w"])},
        {"key": "losses", "title": "MOST LOSSES", "rows": career_list(lambda m: car[m]["l"])},
        {"key": "win-pct", "title": "WIN PERCENTAGE", "rows": career_list(lambda m: round(car[m]["pct"], 3), fmt=pct, sub=crec)},
        {"key": "points", "title": "MOST POINTS", "unit": "AVG PPG", "valSub": True,
         "rows": career_list(lambda m: round(car[m]["pf"] / car[m]["g"], 2), fmt=f2, sub=lambda m: comma(car[m]["pf"]) + " TOTAL")},
        {"key": "points-against", "title": "MOST POINTS AGAINST", "unit": "AVG PPG", "valSub": True,
         "rows": career_list(lambda m: round(car[m]["pa"] / car[m]["g"], 2), fmt=f2, sub=lambda m: comma(car[m]["pa"]) + " TOTAL")},
        {"key": "hardest-sched", "title": "HARDEST SCHEDULE", "unit": "VS LEAGUE AVG",
         "rows": career_list(lambda m: round(car[m]["sos"], 2), fmt=signed)},
        {"key": "easiest-sched", "title": "EASIEST SCHEDULE", "unit": "VS LEAGUE AVG",
         "rows": career_list(lambda m: round(car[m]["sos"], 2), low=True, fmt=signed)},
        {"key": "high-scores", "title": "HIGH SCORES", "rows": career_list(lambda m: car[m]["hs"])},
        {"key": "top3", "title": "TOP 3 SCORES", "rows": career_list(lambda m: car[m]["t3"])},
        {"key": "weeks-first", "title": "WEEKS AT #1", "note": "1ST IN THE STANDINGS \u00b7 THRU %d" % max(done),
         "rows": career_list(lambda m: wk1[m], sub=wk1_best)},
        {"key": "reg-titles", "title": "REGULAR SEASON TITLES", "note": "BEST RECORD",
         "rows": career_list(lambda m: len(titles["reg"][m]), sub=lambda m: yrs(titles["reg"][m]))},
        {"key": "pts-titles", "title": "POINTS TITLES", "note": "MOST POINTS IN A SEASON",
         "rows": career_list(lambda m: len(titles["pts"][m]), sub=lambda m: yrs(titles["pts"][m]))},
        {"key": "winning-seasons", "title": "WINNING SEASONS", "rows": career_list(lambda m: len(titles["win"][m]))},
        {"key": "losing-seasons", "title": "LOSING SEASONS", "rows": career_list(lambda m: len(titles["lose"][m]))},
        {"key": "last", "title": "LAST PLACE FINISHES", "note": "THE PLATE BEGAN IN 2020",
         "rows": career_list(lambda m: len(titles["last"][m]), sub=lambda m: yrs(titles["last"][m]))},
    ]

    # ---------------------------------------------------------------- playoffs
    pc = {m: {"app": set(), "w": 0, "l": 0, "pf": 0.0, "pa": 0.0, "g": 0} for m in CURRENT}
    po_games = []
    for g in po:
        for me, opp in sides(g):
            po_games.append({"_y": g["season"], "m": me["manager"], "x": me["points"], "v": f2(me["points"]),
                             "s": wk(g["season"], g["week"]), "d": vs(me, opp, po_round(g)), "g": gid(g)})
            c = pc.get(me["manager"])
            if not c: continue
            c["app"].add(g["season"]); c["g"] += 1
            c["pf"] += me["points"]; c["pa"] += opp["points"]
            c["w" if me["points"] > opp["points"] else "l"] += 1
    seasons_played = {m: len({y for (y, mm) in line if mm == m and y in done or (mm == m and y == this_season and seasons[-1]["finished"])})
                      for m in CURRENT}
    playoffs = [
        {"key": "po-most-points", "title": "MOST POINTS", "rows": strip(ranked(po_games, "x"))},
        {"key": "po-fewest-points", "title": "FEWEST POINTS", "eras": [dict(e, rows=strip(e["rows"])) for e in eras(po_games, "x")]},
        {"key": "po-high-combined", "title": "HIGHEST COMBINED", "pair": True,
         "rows": strip(finish(ranked(pair_rows(True, "sum", False, f2), "x")))},
        {"key": "po-low-combined", "title": "LOWEST COMBINED", "pair": True,
         "eras": [dict(e, rows=strip(finish(e["rows"]))) for e in eras(pair_rows(True, "sum", True, f2), "x")]},
        {"key": "po-blowout", "title": "BIGGEST BLOWOUT", "pair": True,
         "rows": strip(finish(ranked(pair_rows(True, "mar", False, lambda v: "+" + f2(v)), "x")))},
        {"key": "po-narrow", "title": "NARROWEST WIN", "pair": True,
         "rows": strip(finish(ranked(pair_rows(True, "mar", True, lambda v: "+" + f2(v), True), "x", low=True)))},
        {"key": "championships", "title": "CHAMPIONSHIPS",
         "rows": career_list(lambda m: len(titles["champ"][m]), sub=lambda m: yrs(titles["champ"][m]))},
        {"key": "medals", "title": "MEDALS", "note": "TOP 3 FINISHES",
         "rows": career_list(lambda m: len(titles["medal"][m]), sub=lambda m: yrs(titles["medal"][m]))},
        {"key": "appearances", "title": "PLAYOFF APPEARANCES", "rows": career_list(lambda m: len(pc[m]["app"]))},
        {"key": "appearance-pct", "title": "PLAYOFF APPEARANCE %",
         "rows": career_list(lambda m: round(len(pc[m]["app"]) / max(seasons_played[m], 1), 3), fmt=lambda v: "%d%%" % round(v * 100),
                             sub=lambda m: "%d OF %d" % (len(pc[m]["app"]), seasons_played[m]))},
        {"key": "po-wins", "title": "PLAYOFF WINS", "rows": career_list(lambda m: pc[m]["w"])},
        {"key": "po-losses", "title": "PLAYOFF LOSSES", "rows": career_list(lambda m: pc[m]["l"])},
        {"key": "po-ppg", "title": "PLAYOFF AVG POINTS", "unit": "PER GAME",
         "rows": career_list(lambda m: round(pc[m]["pf"] / pc[m]["g"], 2) if pc[m]["g"] else 0,
                             fmt=lambda v: f2(v) if v else "–", sub=lambda m: "%d GAMES" % pc[m]["g"] if pc[m]["g"] else "")},
        {"key": "po-pa", "title": "PLAYOFF AVG AGAINST", "unit": "PER GAME",
         "rows": career_list(lambda m: round(pc[m]["pa"] / pc[m]["g"], 2) if pc[m]["g"] else 0,
                             fmt=lambda v: f2(v) if v else "–", sub=lambda m: "%d GAMES" % pc[m]["g"] if pc[m]["g"] else "")},
    ]

    # ---------------------------------------------------------------- streaks
    def runs(seq, hit):
        """seq: [(label, item)] in order -> [(length, first_label, last_label, reaches_end)]"""
        out, n, first = [], 0, None
        for i, (lab, it) in enumerate(seq):
            if hit(it):
                if not n: first = lab
                n += 1; last = lab
            elif n:
                out.append((n, first, last, False)); n = 0
        if n:
            out.append((n, first, last, True))
        return out

    managers = sorted({me["manager"] for g in reg for me, _ in sides(g)})
    # win / loss streaks run through the playoffs too (Schilling's 2013: 14-0 and
    # the title = 16); consolation games are skipped. The high-score drought is
    # regular season only (that's the only week there is a league high score).
    game_seq, all_seq = defaultdict(list), defaultdict(list)
    for g in sorted(reg + po, key=lambda g: (g["season"], g["week"])):
        for me, opp in sides(g):
            all_seq[me["manager"]].append(("WK %d '%02d" % (g["week"], g["season"] % 100), {"r": res(me, opp)}))
    for g in reg:
        for me, opp in sides(g):
            game_seq[me["manager"]].append(("WK %d '%02d" % (g["week"], g["season"] % 100),
                                            {"r": res(me, opp), "top": me["points"] == week_top[(g["season"], g["week"])]}))

    def game_streaks(hit, seqs=game_seq):
        items = []
        for m in managers:
            for n, a, b, end in runs(seqs[m], hit):
                active = end and m in CURRENT
                items.append({"m": m, "x": n, "v": str(n), "s": "%s – %s" % (a, "NOW" if active else b), "on": active})
        return ranked(items, "x")

    season_seq = defaultdict(list)
    for s in seasons:
        apps = {me["manager"] for g in po if g["season"] == s["season"] for me, _ in sides(g)}
        for t in s["teams"]:
            if not s["finished"]: continue
            season_seq[t["manager"]].append((s["season"], {"win": t["wins"] > t["losses"], "po": t["manager"] in apps}))

    def season_streaks(hit):
        items = []
        for m in season_seq:
            seq = season_seq[m]
            # a gap year breaks a streak
            chunks, cur = [], []
            for y, it in seq:
                if cur and y != cur[-1][0] + 1:
                    chunks.append(cur); cur = []
                cur.append((y, it))
            chunks.append(cur)
            for ci, ch in enumerate(chunks):
                for n, a, b, end in runs(ch, hit):
                    active = end and ci == len(chunks) - 1 and m in CURRENT
                    span = str(a) if a == b and not active else "%d–%s" % (a, "NOW" if active else str(b)[2:] if str(a)[:2] == str(b)[:2] else b)
                    items.append({"m": m, "x": n, "v": str(n), "s": span, "on": active})
        return ranked(items, "x")

    streaks = [
        {"key": "win-streak", "title": "WINNING STREAK", "unit": "GAMES", "note": "INCL. PLAYOFFS", "rows": game_streaks(lambda i: i["r"] == "W", all_seq)},
        {"key": "loss-streak", "title": "LOSING STREAK", "unit": "GAMES", "note": "INCL. PLAYOFFS", "rows": game_streaks(lambda i: i["r"] == "L", all_seq)},
        {"key": "hs-drought", "title": "HIGH SCORE DROUGHT", "unit": "WEEKS", "rows": game_streaks(lambda i: not i["top"])},
        {"key": "winning-record-streak", "title": "WINNING RECORD STREAK", "unit": "SEASONS", "rows": season_streaks(lambda i: i["win"])},
        {"key": "winning-record-drought", "title": "WINNING RECORD DROUGHT", "unit": "SEASONS", "rows": season_streaks(lambda i: not i["win"])},
        {"key": "po-streak", "title": "PLAYOFF STREAK", "unit": "SEASONS", "rows": season_streaks(lambda i: i["po"])},
        {"key": "po-drought", "title": "PLAYOFF DROUGHT", "unit": "SEASONS", "rows": season_streaks(lambda i: not i["po"])},
    ]

    # ---------------------------------------------------------------- money & bonuses
    def prof(m, *path, default=0):
        v = profiles.get(m) or {}
        for p in path:
            v = (v or {}).get(p) if isinstance(v, dict) else None
        return default if v is None else v

    def dollars(v):
        return ("−$" if v < 0 else "$") + "{:,.0f}".format(abs(v)) if v == int(v) else \
               ("−$" if v < 0 else "$") + "{:,.2f}".format(abs(v))

    money = [
        {"key": "bank", "title": "CAREER BANK", "note": "WON MINUS DUES & TAX",
         "rows": [dict(r, neg=r["v"].startswith("−")) for r in
                  career_list(lambda m: prof(m, "career", "bank", "net"), fmt=dollars,
                              sub=lambda m: "%s WON" % dollars(prof(m, "career", "bank", "total")))]},
        {"key": "bonus-wins", "title": "WEEKLY BONUS WINS", "rows": career_list(lambda m: prof(m, "career", "bonus", "weekly_total"))},
        {"key": "squid", "title": "SQUID GAME BELTS",
         "rows": career_list(lambda m: len(prof(m, "career", "bonus", "squid", default=[])), sub=lambda m: yrs(prof(m, "career", "bonus", "squid", default=[])))},
        {"key": "dk", "title": "DRAFTKINGS WINS",
         "rows": career_list(lambda m: len(prof(m, "career", "bonus", "draftkings", default=[])), sub=lambda m: yrs(prof(m, "career", "bonus", "draftkings", default=[])))},
    ]

    # ---------------------------------------------------------------- standings (left nav)
    standings = []
    for m in CURRENT:
        c, p = car[m], pc[m]
        standings.append({"m": m, "w": c["w"], "l": c["l"], "t": c["t"], "pct": round(c["pct"], 3),
                          "po_app": len(p["app"]), "po_w": p["w"], "po_l": p["l"],
                          "medals": [sum(1 for s in finished for t in s["teams"] if t["manager"] == m and t["final_rank"] == k)
                                     for k in (1, 2, 3)]})
    standings.sort(key=lambda s: (-s["medals"][0], -s["pct"]))

    dots = dot_pot(CURRENT, managers)

    last_week = max((g["week"] for g in reg if g["season"] == this_season), default=0)
    return {
        "season": this_season, "through": "%d WEEK %d" % (this_season, last_week),
        "names": {m: yc.DISPLAY.get(m, m.upper()) for m in set(managers) | set(CURRENT)},
        "standings": standings,
        "categories": [
            {"key": "game", "label": "SINGLE GAME", "records": single},
            {"key": "season", "label": "SEASON", "records": season},
            {"key": "career", "label": "CAREER", "records": career},
            {"key": "playoffs", "label": "PLAYOFFS", "records": playoffs},
            {"key": "streaks", "label": "STREAKS & DROUGHTS", "records": streaks},
            {"key": "money", "label": "MONEY & BONUSES", "records": money},
        ] + ([{"key": "dots", "label": "DOT POT", "records": dots}] if dots else []),
    }


DOT_POS = ("QB", "RB", "WR", "TE")


def dot_pot(current, managers):
    """DOT POT tab. docs/dots.json only holds the season being played, so each
    run keeps a copy as docs/history/dots-<season>.json and every season saved
    there counts toward the all-time lists."""
    cur = load(REPO / "docs" / "dots.json", {})
    hist_dir = REPO / "docs" / "history"
    if cur.get("season") and cur.get("history"):
        keep = {"season": cur["season"], "dot_value": (cur.get("pot") or {}).get("dot_value"), "history": cur["history"]}
        f = hist_dir / ("dots-%d.json" % cur["season"])
        if load(f, None) != keep:
            f.write_text(json.dumps(keep, separators=(",", ":")) + "\n")
    seasons = [load(f, {}) for f in sorted(hist_dir.glob("dots-*.json"))]
    if not seasons:
        return []
    count = {m: {p: 0 for p in DOT_POS} for m in current}
    money = {m: 0 for m in current}
    best = {m: {p: 0.0 for p in DOT_POS} for m in current}
    wins = []                                        # every dot: one row per manager who got it
    for sz in seasons:
        for wk in sz.get("history", []):
            for pos, d in (wk.get("positions") or {}).items():
                if pos not in DOT_POS:
                    continue
                who = [x.lower() for x in (d.get("managers") or [d.get("manager")]) if x]
                try:
                    val = float(d.get("value"))
                except (TypeError, ValueError):
                    continue
                for m in who:
                    m = m if m in managers or m in current else m
                    if m in count:
                        count[m][pos] += 1
                        money[m] += (sz.get("dot_value") or 0) / max(1, len(who))
                        best[m][pos] = max(best[m][pos], val)
                    wins.append({"m": m, "x": val, "v": "%.2f" % val,
                                 "s": "WK %d, %d" % (wk["week"], sz["season"]),
                                 "d": "%s · %s" % (pos, str(d.get("detail") or "").upper())})
    first = min(sz["season"] for sz in seasons)
    note = "SINCE %d" % first
    usd = lambda v: "$%s" % ("{:,.0f}".format(v) if v == int(v) else "{:,.2f}".format(v))

    def lst(fn, sub=None):
        return ranked([{"m": m, "x": fn(m), "v": str(fn(m)), "s": sub(m) if sub else ""} for m in current if fn(m)], "x")

    recs = [{"key": "dots", "title": "TOTAL DOTS", "note": note,
             "rows": lst(lambda m: sum(count[m].values()), sub=lambda m: "%s WON" % usd(money[m]))}]
    for pos in DOT_POS:
        recs.append({"key": "dots-" + pos.lower(), "title": "%s DOTS" % pos, "note": note,
                     "rows": lst(lambda m, p=pos: count[m][p],
                                 sub=lambda m, p=pos: ("BEST %.2f" % best[m][p]) if count[m][p] else "")})
    recs.append({"key": "dot-scores", "title": "BIGGEST DOT SCORES", "note": "BEST WEEKLY WINNER", "rows": ranked(wins, "x")})
    for pos in DOT_POS:
        recs.append({"key": "dot-best-" + pos.lower(), "title": "BIGGEST %s DOT" % pos, "note": note,
                     "rows": [dict(w, d=w["d"].split(" · ", 1)[1]) for w in
                              ranked([w for w in wins if w["d"].startswith(pos + " ")], "x")]})
    return recs


def main():
    data = build()
    old = load(OUT, {})
    if {k: v for k, v in old.items() if k != "updated"} != data:
        OUT.write_text(json.dumps({"updated": yc.now_iso(), **data}, separators=(",", ":")) + "\n")
        print("records.json updated.")
    else:
        print("records.json up to date.")


if __name__ == "__main__":
    main()
