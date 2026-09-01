#!/usr/bin/env python3
"""
pull_standings.py — append this week's snapshot to docs/season.json.

The standings block draws a rank-over-time chart, so it needs every week that
has happened, not just the current one. This script READS the existing file,
replaces or appends the row for the current week, and writes it back.

It never discards history. If a week is already recorded, earlier weeks stay
exactly as they were.

    python3 pull_standings.py             # normal run
    python3 pull_standings.py --inspect   # dump the raw shape and exit

Run --inspect first, once the Fantasy Sports scope is enabled, and correct
anything in FIELD_PATHS that reports NOT FOUND.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from yahoofantasy import Context

GAME       = "nfl"
SEASON     = 2026
LEAGUE_ID  = ""                       # e.g. "123456", from your league URL
OUT        = Path("docs/season.json")
LEDGER     = Path("bank-ledger.json")
CATEGORIES = Path("bonus-categories.json")

# Yahoo's manager nickname (lowercased) -> the key used throughout the feed.
KEY_BY_NICKNAME = {
    "greg": "greg", "graham": "graham", "anthony": "anthony", "scotty": "scotty",
    "gangus": "gangus", "hayden": "hayden", "garrett": "garrett",
    "coyne": "coyne", "scarpitti": "scarpitti", "schilling": "schilling",
}

FIELD_PATHS = {
    "rank":       "team_standings.rank",
    "wins":       "team_standings.outcome_totals.wins",
    "losses":     "team_standings.outcome_totals.losses",
    "ties":       "team_standings.outcome_totals.ties",
    "points_for": "team_standings.points_for",
}


def dig(obj, path, default=None):
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
    return default if cur is None else cur


def nickname(team) -> str:
    managers = getattr(team, "managers", None) or []
    if isinstance(managers, list) and managers:
        return str(dig(managers[0], "nickname", "")
                   or dig(managers[0], "manager.nickname", ""))
    return str(dig(team, "managers.manager.nickname", "") or "")


def get_league():
    leagues = Context().get_leagues(GAME, SEASON)
    if not leagues:
        sys.exit("No " + GAME + " leagues found for " + str(SEASON) + ".")
    if LEAGUE_ID:
        for lg in leagues:
            if str(LEAGUE_ID) in str(getattr(lg, "league_id", "")):
                return lg
        sys.exit("League " + str(LEAGUE_ID) + " not found.")
    if len(leagues) > 1:
        print("Note: several leagues found, using the first. Set LEAGUE_ID to pin it.")
    return leagues[0]


def inspect():
    league = get_league()
    team = league.standings()[0]
    print("-- league attrs --")
    print([a for a in dir(league) if not a.startswith("_")])
    print("\n-- team attrs --")
    print([a for a in dir(team) if not a.startswith("_")])
    print("\n-- resolved via FIELD_PATHS --")
    for k, p in FIELD_PATHS.items():
        print("  %-12s %-38s -> %r" % (k, p, dig(team, p, "NOT FOUND")))
    print("  %-12s %-38s -> %r" % ("nickname", "(managers[0].nickname)", nickname(team)))
    print("\n-- nicknames vs KEY_BY_NICKNAME --")
    for t in league.standings():
        n = nickname(t).strip().lower()
        print("  %-20s -> %s" % (n, KEY_BY_NICKNAME.get(n) or "UNMAPPED"))


def bonuses_from_ledger():
    """Ledger -> { manager_key: [ {week,type,label,short,amount}, ... ] }"""
    meta = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    by_week = {c["week"]: c for c in meta["categories"]}
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    season = ledger.get(str(SEASON), {})

    out = {k: [] for k in KEY_BY_NICKNAME.values()}
    for week_str, winners in season.items():
        if not str(week_str).isdigit():
            continue
        week = int(week_str)
        hi = KEY_BY_NICKNAME.get(str(winners.get("high", "")).strip().lower())
        if hi:
            out[hi].append({"week": week, "type": "high", "label": "High Score",
                            "short": "HS", "amount": meta["high_score"]["amount"]})
        ct = KEY_BY_NICKNAME.get(str(winners.get("category", "")).strip().lower())
        if ct:
            c = by_week.get(week, {})
            out[ct].append({"week": week, "type": "category",
                            "label": c.get("label", "Week " + str(week)),
                            "short": c.get("short", c.get("label", "")),
                            "amount": meta["category_amount"]})

    # One-off awards: "awards": [{"manager","label","short","amount"}]
    for a in season.get("awards", []) or []:
        slug = KEY_BY_NICKNAME.get(str(a.get("manager", "")).strip().lower())
        if slug:
            out[slug].append({"type": "award", "label": a.get("label", "Award"),
                              "short": a.get("short", a.get("label", "")),
                              "amount": int(a.get("amount", 0))})

    for v in out.values():
        v.sort(key=lambda b: (b.get("week") or 99, b["type"]))
    return out


def snapshot(league):
    """This week's row for the weeks array, in rank order."""
    teams = []
    for t in league.standings():
        nick = nickname(t).strip().lower()
        key = KEY_BY_NICKNAME.get(nick)
        if not key:
            print("  ! nickname %r isn't in KEY_BY_NICKNAME - skipped" % nick)
            continue
        wins = int(dig(t, FIELD_PATHS["wins"], 0))
        losses = int(dig(t, FIELD_PATHS["losses"], 0))
        ties = int(dig(t, FIELD_PATHS["ties"], 0))
        played = wins + losses + ties
        pf = float(dig(t, FIELD_PATHS["points_for"], 0) or 0)
        teams.append({"key": key, "rank": int(dig(t, FIELD_PATHS["rank"], 0)),
                      "wins": wins, "losses": losses, "ties": ties,
                      "avg_per_week": round(pf / played, 2) if played else 0.0})
    teams.sort(key=lambda x: x["rank"] or 99)
    return teams


def main():
    if not OUT.exists():
        sys.exit(str(OUT) + " is missing. It holds the season's history - "
                 "restore it from the repo rather than starting fresh.")
    feed = json.loads(OUT.read_text(encoding="utf-8"))

    league = get_league()
    week = int(getattr(league, "current_week", 0) or 0)
    teams = snapshot(league)
    if not teams:
        sys.exit("No teams resolved - check KEY_BY_NICKNAME against --inspect.")

    row = {"week": week, "label": "Week " + str(week),
           "short": "W" + str(week), "teams": teams}

    weeks = feed.get("weeks", [])
    at = next((i for i, w in enumerate(weeks) if w.get("week") == week), None)
    if at is None:
        weeks.append(row)
        action = "added week " + str(week)
    else:
        weeks[at] = row
        action = "refreshed week " + str(week)
    weeks.sort(key=lambda w: w.get("week", 0))

    feed["weeks"] = weeks
    feed["bonuses"] = bonuses_from_ledger()
    feed["league"]["season"] = SEASON
    feed["updated"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(feed, indent=2), encoding="utf-8")
    tmp.replace(OUT)

    banked = sum(b["amount"] for v in feed["bonuses"].values() for b in v)
    print("%s: %s. %d weeks on file, $%d banked." % (OUT, action, len(weeks), banked))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true", help="dump the raw shape and exit")
    if ap.parse_args().inspect:
        inspect()
    else:
        main()
