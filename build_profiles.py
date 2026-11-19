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
PRIZES = REPO / "prize-history.json"     # hand-owned money 2013-2025
PPR_FROM = 2018

CURRENT = list(yc.MANAGER_KEYS)
FORMER_NAMES = {"cam": "CAM", "steve": "STEVE", "patrick": "PATRICK", "chipper": "CHIPPER"}
# Preview profiles for players joining next season (Michael, 2026-11-19): Cam
# returns in 2027 with his 2013-2017 history; Chipper is brand new. They get a
# profile file but aren't in the league's order, ranks or anyone's Versus.
# When 2027 starts, move them into yc.MANAGER_KEYS and delete them here.
PREVIEW = {"cam": {"team_name": "Cindy Gricar", "season": 2027},
           "chipper": {"team_name": "TBD", "season": 2027}}


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

def award_group(b):
    """Which bank category a ledger award belongs to, from its kind or label."""
    t = ("%s %s %s" % (b.get("kind") or "", b.get("label") or "", b.get("short") or "")).lower()
    if "squid" in t: return "squid_game"
    if "thanksgiving" in t: return "thanksgiving"
    if "draft" in t or "dk" in t.split(): return "draftkings"
    return "playoffs"


def build():
    games = load(HISTORY / "matchups.json")["games"]
    seasons = {s["season"]: s for s in load(HISTORY / "seasons.json")["seasons"]}
    baseline = load(BASELINE, {})
    season_feed = load(REPO / "docs" / "season.json", {})
    dots_feed = load(REPO / "docs" / "dots.json", {})

    this_season = max(seasons)
    regular = [g for g in games if g["type"] == "regular"]

    # ---- this season's money, all automatic --------------------------------
    # Weekly bonus, high score and Dot Pot come from bank-ledger.json, which
    # settle_week.py fills every Tuesday. Squid Game, DraftKings and
    # Thanksgiving winners are the ledger's "awards". Playoff payouts are added
    # here from Yahoo's final standings once the season is over, unless the
    # ledger already has them. Waiver tax comes from the DraftKings feed.
    live_bonuses = {m: list(v) for m, v in season_feed.get("bonuses", {}).items()}
    cur = seasons[this_season]
    payout = baseline.get("playoff_payouts", {"1": 500, "2": 300, "3": 200})
    if cur.get("finished"):
        for t in cur["teams"]:
            amt = payout.get(str(t.get("final_rank")))
            rows = live_bonuses.setdefault(t["manager"], [])
            if amt and not any(award_group(b) == "playoffs" for b in rows if b.get("type") == "award"):
                rows.append({"type": "playoff", "label": "Playoffs - %s place" % t["final_rank"], "amount": amt})
    dk_feed = load(REPO / "docs" / "dk.json", {})
    tax_now = defaultdict(float)
    if dk_feed.get("season", this_season) == this_season:
        for r in dk_feed.get("managers", []):
            tax_now[r["name"].lower()] = round(r.get("moves", 0) * dk_feed.get("tax_per_move", 0.5), 2)
    playoff = [g for g in games if g["type"] == "playoff"]

    # weekly top score, regular season
    week_top = defaultdict(float)
    for g, me, opp in sides(regular):
        k = (g["season"], g["week"])
        week_top[k] = max(week_top[k], me["points"])

    first = weeks_at_first(regular, seasons)

    # Per-season numbers for every team that season (former managers too), so
    # the season history can rank a year's average, high and low among that
    # year's ten teams.
    season_stat = defaultdict(dict)
    for g, me, opp in sides(regular):
        d = season_stat[g["season"]].setdefault(me["manager"], {"pf": [], "pa": [], "hi": None, "lo": None})
        d["pf"].append(me["points"]); d["pa"].append(opp["points"])
        if d["hi"] is None or me["points"] > d["hi"][1]["points"]: d["hi"] = (g, me, opp)
        if d["lo"] is None or me["points"] < d["lo"][1]["points"]: d["lo"] = (g, me, opp)
    season_rank = {}
    for season, teams in season_stat.items():
        vals = {k: {"avg": sum(v["pf"]) / len(v["pf"]), "avg_against": sum(v["pa"]) / len(v["pa"]),
                    "high": v["hi"][1]["points"], "low": v["lo"][1]["points"]} for k, v in teams.items()}
        season_rank[season] = {
            f: rank_all({k: v[f] for k, v in vals.items()}, lowest_first=(f == "low"))
            for f in ("avg", "avg_against", "high", "low")}

    # past seasons' money (prize-history.json): buy-in, each prize, bank rank
    prize_hist = load(PRIZES, {})
    PTYPE = {"weekly_bonus": "category", "high_score": "high", "playoff": "playoff",
             "squid_game": "squid", "draftkings": "dk", "thanksgiving": "thanksgiving"}
    past_prizes = defaultdict(list)
    for r in prize_hist.get("prizes", []):
        past_prizes[(r["year"], r["winner"])].append({
            "week": r.get("week"), "type": PTYPE.get(r["type"], r["type"]),
            "label": r.get("label"), "key": r.get("key"), "icon": r.get("icon"),
            "amount": r["amount"]})
    past_buy = {int(y): v for y, v in prize_hist.get("buy_in", {}).items()}
    past_bank_rank = {
        y: rank_all({m: round(sum(q["amount"] for q in past_prizes[(y, m)]), 2) for m in teams})
        for y, teams in past_buy.items()}

    BANK_GROUP = {"category": "weekly_bonus", "high": "high_score", "playoff": "playoffs",
                  "squid": "squid_game", "dk": "draftkings", "thanksgiving": "draftkings",
                  "dot": "dot_pot"}

    def career_bank(m):
        won = {k: 0.0 for k in ("weekly_bonus", "high_score", "squid_game", "draftkings", "dot_pot", "playoffs")}
        rows = [q for (y, who), qs in past_prizes.items() if who == m for q in qs]
        rows += live_bonuses.get(m, []) if this_season > max([y for (y, _), qs in past_prizes.items() if qs] or [0]) else []
        for q in rows:
            k = BANK_GROUP.get(q.get("type")) if q.get("type") != "award" else \
                {"thanksgiving": "draftkings"}.get(award_group(q), award_group(q))
            if k: won[k] += q.get("amount", 0)
        won = {k: round(v, 2) for k, v in won.items()}
        return {"total": round(sum(won.values()), 2), "won": won,
                "since": min([y for (y, who) in past_prizes if who == m] or [this_season])}

    # career bonus stats: weekly category wins by category, Squid Game and
    # DraftKings titles, Dot Pot dots by position
    cat_week = {c["week"]: c["key"] for c in load(REPO / "bonus-categories.json", {}).get("categories", [])}

    def career_bonus(m):
        wins, names, squid, dk = defaultdict(int), {}, [], []
        for (y, who), qs in past_prizes.items():
            if who != m: continue
            for q in qs:
                if q["type"] == "category" and q.get("key"):
                    wins[q["key"]] += 1; names[q["key"]] = q["label"]
                elif q["type"] == "squid": squid.append(y)
                elif q["type"] == "dk": dk.append(y)
        if this_season > max([y for (y, _), qs in past_prizes.items() if qs] or [0]):
            for b in live_bonuses.get(m, []):
                if b.get("type") == "category" and cat_week.get(b.get("week")):
                    wins[cat_week[b["week"]]] += 1
                elif b.get("type") == "award" and award_group(b) == "squid_game": squid.append(this_season)
                elif b.get("type") == "award" and award_group(b) == "draftkings": dk.append(this_season)
        dots = {pos: (dots_feed.get("totals", {}).get(m) or {}).get(pos, 0) for pos in ("QB", "RB", "WR", "TE")}
        return {"weekly": dict(wins), "weekly_total": sum(wins.values()),
                "retired_names": {k: v for k, v in names.items() if k not in cat_week.values()},
                "squid": sorted(squid), "draftkings": sorted(dk),
                "dots": dots, "dots_total": sum(dots.values()), "dots_since": 2026}

    # this season's money and dots
    bonuses = live_bonuses
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
    for m in CURRENT + [k for k in PREVIEW if k not in CURRENT]:
        pv = PREVIEW.get(m) if m not in CURRENT else None
        mine = [(g, me, opp) for g, me, opp in sides(regular) if me["manager"] == m]
        ppr = [(g, me, opp) for g, me, opp in mine if g["season"] >= PPR_FROM]
        # a preview with only full-PPR seasons (Cam, 2013-17): averages and low
        # from those years, unranked since they don't compare with half-PPR
        if pv and not ppr: ppr = mine
        w = sum(1 for g, me, opp in mine if result(me, opp) == "W")
        l = sum(1 for g, me, opp in mine if result(me, opp) == "L")
        t = sum(1 for g, me, opp in mine if result(me, opp) == "T")

        hi = max(mine, key=lambda x: x[1]["points"]) if mine else None
        lo = min(ppr, key=lambda x: x[1]["points"]) if ppr else None
        blow = max(mine, key=lambda x: x[1]["points"] - x[2]["points"]) if mine else None
        both = max(mine, key=lambda x: x[1]["points"] + x[2]["points"]) if mine else None

        earnings = baseline.get("earnings", {}).get(m)
        if pv:
            # preview: past winnings minus past dues and tax (prize-history.json)
            paid = sum(v[m]["dues"] + v[m]["tax"] for v in past_buy.values() if m in v)
            won = sum(q["amount"] for (y, who), qs in past_prizes.items() if who == m for q in qs)
            earnings = round(won - paid, 2)
        elif earnings is not None and add_current:
            earnings = round(earnings + bank_now[m] - dues - tax_now[m], 2)
        category = baseline.get("weekly_category", {}).get(m)
        if category is not None and add_current:
            category += category_now[m]

        career = {
            "wins": w, "losses": l, "ties": t,
            "win_pct": round((w + 0.5 * t) / max(w + l + t, 1), 3),
            "high_scores": sum(1 for g, me, opp in mine
                               if me["points"] == week_top[(g["season"], g["week"])]),
            "weeks_first": first.get(m, 0),
            "blowout": ref(*blow, value=blow[1]["points"] - blow[2]["points"]) if blow else None,
            "earnings": earnings,
            # highest combined score (both teams) in one regular-season game
            "combined": dict(ref(*both, value=both[1]["points"] + both[2]["points"]),
                             points=both[1]["points"], opp_points=both[2]["points"]) if both else None,
            # every dollar won, 2013 to now, by where it came from (no buy-ins)
            "bank": career_bank(m),
            "bonus": career_bonus(m),
            "avg": round(sum(me["points"] for _, me, _ in ppr) / len(ppr), 2) if ppr else None,
            "avg_against": round(sum(o["points"] for _, _, o in ppr) / len(ppr), 2) if ppr else None,
            "high": ref(*hi) if hi else None,
            "low": ref(*lo) if lo else None,
        }
        if pv:
            career["last_season"] = max([g["season"] for g, _, _ in mine] or [None], key=lambda v: v or 0)
            career["full_ppr_only"] = bool(mine) and all(g["season"] < PPR_FROM for g, _, _ in mine)

        # this season
        now_rows = [(g, me, opp) for g, me, opp in mine if g["season"] == this_season]
        now_team = next((t for t in seasons[this_season]["teams"] if t["manager"] == m), {})
        if pv: now_team = {"team_name": pv["team_name"]}
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
            "high": ref(*max(now_rows, key=lambda x: x[1]["points"])) if now_rows else None,
            "low": ref(*min(now_rows, key=lambda x: x[1]["points"])) if now_rows else None,
            "dots": dots_now.get(m, 0),
            "bank": bank_now.get(m, 0),
            "buy_in": None if pv else baseline.get("season_dues"),
            # for the 2026 section's graphics
            "dots_by_pos": {pos: (dots_feed.get("totals", {}).get(m) or {}).get(pos, 0)
                            for pos in ("QB", "RB", "WR", "TE")},
            "bonuses": [{"week": b.get("week"), "type": b.get("type"),
                         "pos": b.get("pos"), "label": b.get("label"),
                         "amount": b.get("amount", 0)}
                        for b in bonuses.get(m, [])],
            "regular_weeks": seasons[this_season].get("regular_weeks") or 15,
            **({"season": pv["season"], "preview": True} if pv else {}),
            "results": [{"week": g["week"], "result": result(me, o),
                         "points": me["points"], "opp_points": o["points"],
                         "opponent_name": name_of(o["manager"])}
                        for g, me, o in sorted(now_rows, key=lambda x: x[0]["week"])],
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
                "champion": seasons[season]["finished"] and team["final_rank"] == 1,
                "high": ref(*season_stat[season][m]["hi"]) if m in season_stat.get(season, {}) else None,
                "low": ref(*season_stat[season][m]["lo"]) if m in season_stat.get(season, {}) else None,
                "ranks": dict({f: season_rank.get(season, {}).get(f, {}).get(m)
                               for f in ("avg", "avg_against", "high", "low")},
                              **({"bank": past_bank_rank[season].get(m)} if season in past_buy else {})),
                **({"buy_in": round(past_buy[season][m]["dues"] + past_buy[season][m]["tax"], 2),
                    "dues": past_buy[season][m]["dues"], "waiver_tax": past_buy[season][m]["tax"],
                    "bank": round(sum(q["amount"] for q in past_prizes[(season, m)]), 2),
                    "bonuses": sorted(past_prizes[(season, m)],
                                      key=lambda q: (q["week"] or 99, q["type"]))}
                   if m in past_buy.get(season, {}) else {}),
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
        if pv and not po_ppr: po_ppr = po        # full-PPR-only preview (Cam): his own years
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
        vs = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0, "games": []})
        for g, me, o in sides(games):
            if me["manager"] == m:
                r = result(me, o)
                vs[o["manager"]][{"W": "wins", "L": "losses", "T": "ties"}[r]] += 1
                fr = {t["manager"]: t.get("final_rank") for t in seasons[g["season"]]["teams"]}
                last_po = max((x["week"] for x in games if x["season"] == g["season"] and x["type"] == "playoff"), default=None)
                champ = (g["type"] == "playoff" and g["week"] == last_po and seasons[g["season"]].get("finished")
                         and {fr.get(me["manager"]), fr.get(o["manager"])} == {1, 2})
                # 4-team bracket: first playoff week is the semifinal; the last
                # week is the final (champ) or the 3rd place game
                rnd = (("final" if champ else "third") if g["week"] == last_po else "semi") \
                    if g["type"] == "playoff" else None
                vs[o["manager"]]["games"].append({
                    "season": g["season"], "week": g["week"], "type": g["type"], "result": r,
                    "champ": bool(champ), "round": rnd,
                    "points": round(me["points"], 2), "opp_points": round(o["points"], 2)})
        for k, v in vs.items():
            # head-to-head totals for the Versus block, newest game first
            gl = sorted(v["games"], key=lambda x: (x["season"], x["week"]), reverse=True)
            n = len(gl) or 1
            pf, pa = sum(x["points"] for x in gl), sum(x["opp_points"] for x in gl)
            v["games"] = gl
            v["points_for"], v["points_against"] = round(pf, 2), round(pa, 2)
            v["avg_for"], v["avg_against"] = round(pf / n, 2), round(pa / n, 2)
            v["diff"] = round(pf - pa, 2)
            wins = [x for x in gl if x["result"] == "W"]
            losses = [x for x in gl if x["result"] == "L"]
            margin = lambda x: x["points"] - x["opp_points"]
            v["biggest_win"] = max(wins, key=margin) if wins else None
            v["worst_loss"] = min(losses, key=margin) if losses else None
            v["closest"] = min(gl, key=lambda x: abs(margin(x))) if gl else None
            v["high"] = max(gl, key=lambda x: x["points"]) if gl else None
            st = 0
            for x in gl:
                if not st or (x["result"] == gl[0]["result"]): st += 1
                if x["result"] != gl[0]["result"]: break
            v["streak"] = (gl[0]["result"] + str(st)) if gl else None
        versus = [dict(opponent=k, opponent_name=name_of(k), **v)
                  for k, v in vs.items() if k in CURRENT]
        versus.sort(key=lambda v: CURRENT.index(v["opponent"]))
        former = [dict(opponent=k, opponent_name=name_of(k), **v)
                  for k, v in vs.items() if k not in CURRENT]

        P[m] = {"key": m, "name": name_of(m),
                "team_name": now_team.get("team_name"),
                # Yahoo team page; the profile's team name links here
                "team_url": ("https://football.fantasysports.yahoo.com/f1/%s/%s"
                             % (seasons[this_season]["league_key"].split(".l.")[-1],
                                now_team.get("team_id")) if now_team.get("team_id") else None),
                "season": cur, "career": career, "seasons": history,
                "playoffs": playoffs, "versus": versus, "versus_former": former,
                "bonus": {"weekly_category": category},
                **({"preview": True} if pv else {})}

    # ranks
    previews = [m for m in P if m not in CURRENT]

    def put(section, field, getter, lowest_first=False):
        def val(m):
            try: return getter(P[m])
            except (TypeError, KeyError, AttributeError): return None
        ranks = rank_all({m: val(m) for m in CURRENT}, lowest_first)
        for m in CURRENT:
            P[m][section].setdefault("ranks", {})[field] = ranks[m]
        # a preview's rank is where it would sit among the ten (the ten keep theirs)
        for m in previews:
            if section == "season": continue
            if not P[m]["career"]["wins"] + P[m]["career"]["losses"] + P[m]["career"]["ties"]:
                P[m][section].setdefault("ranks", {})[field] = None; continue      # no games yet
            if (P[m]["career"].get("full_ppr_only") and field in ("avg", "avg_against", "low")) or field == "bonus_dots":
                P[m][section].setdefault("ranks", {})[field] = None; continue
            r = rank_all(dict({k: val(k) for k in CURRENT}, **{m: val(m)}), lowest_first)
            P[m][section].setdefault("ranks", {})[field] = r[m]

    put("career", "win_pct", lambda p: p["career"]["win_pct"])
    put("career", "high_scores", lambda p: p["career"]["high_scores"])
    put("career", "weeks_first", lambda p: p["career"]["weeks_first"])
    put("career", "blowout", lambda p: p["career"]["blowout"]["value"])
    put("career", "earnings", lambda p: p["career"]["earnings"])
    # profit = everything won minus every dollar paid in (dues + waiver tax)
    for m in P:
        c = P[m]["career"]
        if c.get("earnings") is not None:
            c["bank"]["net"] = c["earnings"]
            c["bank"]["paid"] = round(c["bank"]["total"] - c["earnings"], 2)
    put("career", "bank", lambda p: p["career"]["bank"].get("net"))
    put("career", "bonus_weekly", lambda p: p["career"]["bonus"]["weekly_total"])
    put("career", "bonus_dots", lambda p: p["career"]["bonus"]["dots_total"])
    # combined score ranks the GAME among every regular-season game ever played
    # (the league's Highest Combined Scores list), not among the ten players
    all_combined = sorted((round(g["a"]["points"] + g["b"]["points"], 2) for g in regular), reverse=True)
    for m in P:
        if not P[m]["career"]["combined"]:
            P[m]["career"].setdefault("ranks", {})["combined"] = None; continue
        v = P[m]["career"]["combined"]["value"]
        better = sum(1 for o in all_combined if o > v)
        P[m]["career"].setdefault("ranks", {})["combined"] = ("T-%d" if all_combined.count(v) > 1 else "%d") % (better + 1)
    put("career", "avg", lambda p: p["career"]["avg"])
    put("career", "avg_against", lambda p: p["career"]["avg_against"])
    put("career", "high", lambda p: p["career"]["high"]["value"])
    put("career", "low", lambda p: (p["career"]["low"] or {}).get("value"), lowest_first=True)
    put("season", "avg", lambda p: p["season"]["avg"])
    put("season", "avg_against", lambda p: p["season"]["avg_against"])
    put("season", "high_scores", lambda p: p["season"]["high_scores"])
    put("season", "dots", lambda p: p["season"]["dots"])
    put("season", "high", lambda p: (p["season"]["high"] or {}).get("value"))
    put("season", "low", lambda p: (p["season"]["low"] or {}).get("value"), lowest_first=True)
    put("season", "bank", lambda p: p["season"]["bank"])
    put("bonus", "weekly_category", lambda p: p["bonus"]["weekly_category"])

    last = max(((g["season"], g["week"]) for g in games), default=(this_season, 0))
    return {"through": {"season": last[0], "week": last[1]},
            "rules": {"ppr_from": PPR_FROM},
            "order": CURRENT, "previews": previews, "managers": P}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = build()
    for m in data["order"] + data["previews"]:
        p = data["managers"][m]
        c, s, po = p["career"], p["season"], p["playoffs"]
        print("%-10s %3d-%-3d %.3f  avg %6s  ag %6s  hi %6.2f  lo %6s  HS %2d  #1 %2d  $%s"
              "  | %d now: #%s %d-%d %s  | PO %d-%d  %d/%d/%d"
              % (m, c["wins"], c["losses"], c["win_pct"], c["avg"], c["avg_against"],
                 (c["high"] or {}).get("value") or 0, (c["low"] or {}).get("value"), c["high_scores"],
                 c["weeks_first"], c["earnings"], s["season"], s["standing"],
                 s["wins"], s["losses"], s["streak"], po["wins"], po["losses"],
                 po["trophies"]["first"], po["trophies"]["second"], po["trophies"]["third"]))

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for m in data["order"] + data["previews"]:
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
