#!/usr/bin/env python3
"""
pull_standings.py - append this week's snapshot to docs/season.json.

The standings block draws a rank-over-time chart, so it needs every week that
has happened, not just the current one. This script READS the existing file,
replaces or appends the row for the current week, and writes it back.

It never discards history. If a week is already recorded, earlier weeks stay
exactly as they were.

    python3 pull_standings.py              # normal run
    python3 pull_standings.py --week 4     # force a week label
    python3 pull_standings.py --dry-run    # print, write nothing
    python3 pull_standings.py --inspect    # show what Yahoo returns, then exit

This is the ONLY script that writes season.json. pull_live.py writes
scoreboard.json and bonus.json; it deliberately leaves this file alone.

FIXED 2026-09-01:
  - was calling Context().get_leagues(nfl, 2026), which raises ValueError before
    any network call because the library's season table stops at 2025. Now
    addresses the league by key through yahoo_common.
  - was mapping teams on manager nickname. Yahoo returns "M", "Mark", "Michael",
    "I'm Embarrassed" - six of ten managers would have silently vanished from
    the standings. Now maps on team_id, which is stable for the season.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc

REPO = Path(__file__).resolve().parent
OUT = REPO / "docs" / "season.json"
LEDGER = REPO / "bank-ledger.json"


def bonuses_from_ledger(meta, ledger_path=None):
    """bank-ledger.json -> {manager_key: [{week,type,label,short,amount}, ...]}

    Money only comes from here. Playoff payouts, the DraftKings Challenge, and
    the Thanksgiving DK go in the ledger's "awards" array and are picked up
    automatically - do not edit the HTML to add them, or the bank column and the
    ledger will disagree.
    """
    by_week = meta["_by_week"]
    weeks, awards = yc.load_ledger(ledger_path)
    out = {k: [] for k in yc.MANAGER_KEYS}

    for week, winners in weeks.items():
        hi = winners.get("high")
        if hi:
            out[hi["manager"]].append({
                "week": week, "type": "high", "label": "High Score",
                "short": "HS", "amount": meta["high_score"]["amount"]})

        ct = winners.get("category")
        if ct:
            c = by_week.get(week, {})
            out[ct["manager"]].append({
                "week": week, "type": "category",
                "label": c.get("label", "Week %d" % week),
                "short": c.get("short", c.get("label", "")),
                "amount": meta.get("category_amount", 25)})

    for a in awards:
        out[a["manager"]].append({"type": "award", "label": a["label"],
                                  "short": a["short"], "amount": a["amount"]})

    for v in out.values():
        v.sort(key=lambda b: (b.get("week") or 99, b["type"]))
    return out


def snapshot(standings):
    """This week's row for the weeks array, in rank order."""
    teams = []
    for manager, s in standings.items():
        played = s["wins"] + s["losses"] + s["ties"]
        teams.append({
            "key": manager,
            "rank": s["rank"],
            "wins": s["wins"],
            "losses": s["losses"],
            "ties": s["ties"],
            "avg_per_week": round(s["points_for"] / played, 2) if played else 0.0,
        })
    teams.sort(key=lambda t: t["rank"] or 99)
    return teams


def inspect(args):
    token = yc.get_token(args.yf_dir)
    meta = yc.fetch_league_meta(token)
    print("league:      %s" % meta.get("name"))
    print("season:      %s" % meta.get("season"))
    print("current_week %s" % meta.get("current_week"))
    print("start_week   %s   end_week %s" % (meta.get("start_week"),
                                             meta.get("end_week")))
    standings = yc.parse_standings(yc.fetch_standings(token))
    print("\n-- standings, %d teams --" % len(standings))
    for manager, s in sorted(standings.items(), key=lambda kv: kv[1]["rank"]):
        print("  %2d  %-10s %d-%d-%d  PF %7.2f  PA %7.2f"
              % (s["rank"], manager, s["wins"], s["losses"], s["ties"],
                 s["points_for"], s["points_against"]))
    missing = set(yc.MANAGER_KEYS) - set(standings)
    if missing:
        print("\n  ! not resolved: %s - check TEAM_MAP" % ", ".join(sorted(missing)))


def main(args):
    if not OUT.exists():
        sys.exit("%s is missing. It holds the season's history - restore it from "
                 "the repo rather than starting fresh." % OUT)
    feed = json.loads(OUT.read_text(encoding="utf-8"))
    cat_meta = yc.load_categories(args.categories)

    token = yc.get_token(args.yf_dir)
    week = args.week or yc.current_week(token)
    standings = yc.parse_standings(yc.fetch_standings(token))

    if len(standings) != 10:
        sys.exit("Resolved %d of 10 managers - refusing to write a partial "
                 "standings row. Check TEAM_MAP in yahoo_common.py."
                 % len(standings))

    row = {"week": week, "label": "Week %d" % week,
           "short": "W%d" % week, "teams": snapshot(standings)}

    weeks = feed.get("weeks", [])
    at = next((i for i, w in enumerate(weeks) if w.get("week") == week), None)
    if at is None:
        weeks.append(row)
        action = "added week %d" % week
    else:
        weeks[at] = row
        action = "refreshed week %d" % week
    weeks.sort(key=lambda w: w.get("week", 0))

    feed["weeks"] = weeks
    feed["bonuses"] = bonuses_from_ledger(cat_meta, args.ledger)
    feed.setdefault("league", {})["season"] = yc.SEASON
    feed["updated"] = yc.now_iso()

    banked = sum(b["amount"] for v in feed["bonuses"].values() for b in v)
    text = json.dumps(feed, indent=2) + "\n"

    if args.dry_run:
        print("--- would write %s ---" % OUT)
        print("%s. %d weeks on file, $%d banked." % (action, len(weeks), banked))
        print(text[:1200])
        return

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(OUT)
    print("%s: %s. %d weeks on file, $%d banked."
          % (OUT, action, len(weeks), banked))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="override the week number")
    ap.add_argument("--yf-dir", help="directory holding .yahoofantasy")
    ap.add_argument("--categories", help="path to bonus-categories.json")
    ap.add_argument("--ledger", help="path to bank-ledger.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inspect", action="store_true",
                    help="show what Yahoo returns, then exit")
    parsed = ap.parse_args()
    if parsed.inspect:
        inspect(parsed)
    else:
        main(parsed)
