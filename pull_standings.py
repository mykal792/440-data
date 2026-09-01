#!/usr/bin/env python3
"""
pull_standings.py — write standings.json for the standings page.

Cheap and frequent. Reads the league standings for records and average score,
then merges in bank-ledger.json for the bonus money. It does NOT decide who won
a bonus — that's settle_week.py, once a week. Keeping them apart means the
5-minute poll stays a single API call and a bonus can never silently change.

    python3 pull_standings.py             # normal run
    python3 pull_standings.py --inspect   # dump the raw shape and exit

Run --inspect once the Fantasy Sports scope is enabled. The paths in FIELD_PATHS
are the documented ones for yahoofantasy 1.4.9; if any resolve to NOT FOUND,
correct them there rather than hunting through the file.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from yahoofantasy import Context

# ── Configure ────────────────────────────────────────────────────────────────
GAME       = "nfl"
SEASON     = 2026
LEAGUE_ID  = ""          # e.g. "123456" — from your Yahoo league URL
OUT        = Path("standings.json")
LEDGER     = Path("bank-ledger.json")
CATEGORIES = Path("bonus-categories.json")

# Yahoo knows nothing about the custom faces. Map Yahoo nickname -> avatar slug.
AVATAR_BY_MANAGER = {
    "greg": "greg", "gangus": "gangus", "schilling": "schilling",
    "graham": "graham", "scotty": "scotty", "hayden": "hayden",
    "anthony": "anthony", "coyne": "coyne", "scarpitti": "scarpitti",
    "garrett": "garrett",
}

FIELD_PATHS = {
    "rank":           "team_standings.rank",
    "wins":           "team_standings.outcome_totals.wins",
    "losses":         "team_standings.outcome_totals.losses",
    "ties":           "team_standings.outcome_totals.ties",
    "points_for":     "team_standings.points_for",
}


def dig(obj, path, default=None):
    """Walk a dotted path across objects or dicts, tolerating either."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
    return default if cur is None else cur


def manager_name(team) -> str:
    managers = getattr(team, "managers", None) or []
    if isinstance(managers, list) and managers:
        return str(dig(managers[0], "nickname", "")
                   or dig(managers[0], "manager.nickname", ""))
    return str(dig(team, "managers.manager.nickname", "") or "")


def get_league():
    leagues = Context().get_leagues(GAME, SEASON)
    if not leagues:
        sys.exit(f"No {GAME} leagues found for {SEASON}.")
    if LEAGUE_ID:
        for lg in leagues:
            if str(LEAGUE_ID) in str(getattr(lg, "league_id", "")):
                return lg
        sys.exit(f"League {LEAGUE_ID} not in {[getattr(l,'league_id','?') for l in leagues]}")
    if len(leagues) > 1:
        print(f"Note: {len(leagues)} leagues found, using the first. Set LEAGUE_ID to pin it.")
    return leagues[0]


def inspect():
    league = get_league()
    team = league.standings()[0]
    print("── league attrs ──\n", [a for a in dir(league) if not a.startswith("_")])
    print("\n── team attrs ──\n", [a for a in dir(team) if not a.startswith("_")])
    print("\n── resolved via FIELD_PATHS ──")
    for key, path in FIELD_PATHS.items():
        print(f"  {key:12} {path:38} -> {dig(team, path, 'NOT FOUND')!r}")
    print(f"  {'manager':12} {'(managers[0].nickname)':38} -> {manager_name(team)!r}")


def bonuses_for(manager, ledger, meta):
    """Turn ledger entries into this manager's bonus list."""
    by_week = {c["week"]: c for c in meta["categories"]}
    key = manager.strip().upper()
    out = []
    for week_str, winners in ledger.get(str(SEASON), {}).items():
        week = int(week_str)
        if str(winners.get("high", "")).strip().upper() == key:
            out.append({"week": week, "type": "high", "key": "high-score",
                        "label": meta["high_score"]["label"],
                        "amount": meta["high_score"]["amount"]})
        if str(winners.get("category", "")).strip().upper() == key:
            cat = by_week.get(week, {})
            out.append({"week": week, "type": "category",
                        "key": cat.get("key", f"week-{week}"),
                        "label": cat.get("label", f"Week {week}"),
                        "amount": meta["category_amount"]})
    return sorted(out, key=lambda b: (b["week"], b["type"]))


def build():
    meta = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    settled = {int(w) for w in ledger.get(str(SEASON), {})}
    weeks_complete = max(settled) if settled else 0

    league = get_league()
    teams = []
    for t in league.standings():
        mgr = manager_name(t)
        wins   = int(dig(t, FIELD_PATHS["wins"], 0))
        losses = int(dig(t, FIELD_PATHS["losses"], 0))
        ties   = int(dig(t, FIELD_PATHS["ties"], 0))
        played = wins + losses + ties
        pf     = float(dig(t, FIELD_PATHS["points_for"], 0) or 0)

        teams.append({
            "rank":         int(dig(t, FIELD_PATHS["rank"], 0)),
            "avatar":       AVATAR_BY_MANAGER.get(mgr.strip().lower()),
            "manager":      mgr.upper(),
            "wins":         wins,
            "losses":       losses,
            "ties":         ties,
            "avg_per_week": round(pf / played, 2) if played else 0.0,
            "bonuses":      bonuses_for(mgr, ledger, meta),
        })

    teams.sort(key=lambda x: x["rank"] or 99)

    missing = [t["manager"] for t in teams if not t["avatar"]]
    if missing:
        print(f"No avatar mapped for: {', '.join(missing)} — add to AVATAR_BY_MANAGER.")

    return {
        "league": {
            "name":           str(getattr(league, "name", "440 & Friends")),
            "season":         SEASON,
            "week":           int(getattr(league, "current_week", 0) or 0),
            "weeks_complete": weeks_complete,
            "playoff_teams":  6,
        },
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "bonuses_meta": meta,
        "teams": teams,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", action="store_true", help="dump the raw shape and exit")
    if ap.parse_args().inspect:
        inspect()
        raise SystemExit

    payload = build()
    if OUT.exists():
        shutil.copy2(OUT, OUT.with_name("standings-prev.json"))

    # Write then swap, so the page can never fetch a half-written file.
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(OUT)

    banked = sum(b["amount"] for t in payload["teams"] for b in t["bonuses"])
    print(f"Wrote {OUT} — week {payload['league']['week']}, "
          f"{len(payload['teams'])} teams, ${banked} banked")
