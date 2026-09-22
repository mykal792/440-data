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

    Money comes from three places in the ledger: the weekly High Score and
    category bonus (as before), and now Dot Pot - $5 per position won at
    QB/RB/WR/TE. Playoff payouts, the DraftKings Challenge, and the
    Thanksgiving DK go in the ledger's "awards" array and are picked up
    automatically - do not edit the HTML to add them, or the bank column and the
    ledger will disagree.
    """
    by_week = meta["_by_week"]
    weeks, awards = yc.load_ledger(ledger_path)
    out = {k: [] for k in yc.MANAGER_KEYS}

    def credit(winner, row, amount):
        """Give `row` to every tied manager, dividing the prize between them.

        `managers` is a list of one in the normal case, so this is the same as
        a single append. On a tie it pays each winner an even share - a $25
        category split two ways is $12.50 each, and the pot is never overspent.
        """
        share = yc.split_amount(amount, winner["managers"])
        for key in winner["managers"]:
            entry = dict(row, amount=share)
            if len(winner["managers"]) > 1:
                entry["split"] = len(winner["managers"])
            out[key].append(entry)

    for week, winners in weeks.items():
        hi = winners.get("high")
        if hi:
            credit(hi, {"week": week, "type": "high", "label": "High Score",
                        "short": "HS"}, meta["high_score"]["amount"])

        ct = winners.get("category")
        if ct:
            c = by_week.get(week, {})
            credit(ct, {"week": week, "type": "category",
                        "label": c.get("label", "Week %d" % week),
                        "short": c.get("short", c.get("label", ""))},
                   meta.get("category_amount", 25))

        # Dot Pot: $5 to whoever had the week's top scorer at each of
        # QB/RB/WR/TE. Settled the same way as High Score and the weekly
        # category - only counts once it is actually in the ledger.
        for pos, winner in (winners.get("dots") or {}).items():
            if not winner:
                continue
            credit(winner, {"week": week, "type": "dot", "pos": pos,
                            "label": "Dot Pot - %s" % pos, "short": pos},
                   yc.DOT_VALUE)

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


def reconstruct_through(token, through_week):
    """Standings as of the end of `through_week`, rebuilt from scoreboards.

    Yahoo's standings endpoint only ever returns *current* totals - it can't
    tell you what the table looked like after Week 1 once Week 2 is played.
    But every week's scoreboard is still fetchable, so the history can be
    rebuilt by replaying results: wins, losses, ties and points for, week by
    week. Ranked by wins then points for, which is Yahoo's default tiebreak.

    Used only to fill gaps - a missed Tuesday, or the rows lost to the old
    current_week labelling. The latest week always comes from Yahoo's own
    standings, so its ranks are authoritative.
    """
    tally = {k: {"wins": 0, "losses": 0, "ties": 0, "points_for": 0.0,
                 "points_against": 0.0, "rank": 0} for k in yc.MANAGER_KEYS}
    for wk in range(1, through_week + 1):
        _w, _status, matchups = yc.parse_scoreboard(yc.fetch_scoreboard(token, wk))
        for m in matchups:
            (hm, hs), (am, asc) = m["home"], m["away"]
            if hm not in tally or am not in tally:
                continue
            tally[hm]["points_for"] += hs; tally[hm]["points_against"] += asc
            tally[am]["points_for"] += asc; tally[am]["points_against"] += hs
            if hs > asc:
                tally[hm]["wins"] += 1; tally[am]["losses"] += 1
            elif asc > hs:
                tally[am]["wins"] += 1; tally[hm]["losses"] += 1
            else:
                tally[hm]["ties"] += 1; tally[am]["ties"] += 1
    order = sorted(tally, key=lambda k: (-(tally[k]["wins"] + 0.5 * tally[k]["ties"]),
                                          -tally[k]["points_for"]))
    for i, k in enumerate(order, 1):
        tally[k]["rank"] = i
    return tally


def main(args):
    if not OUT.exists():
        sys.exit("%s is missing. It holds the season's history - restore it from "
                 "the repo rather than starting fresh." % OUT)
    feed = json.loads(OUT.read_text(encoding="utf-8"))
    cat_meta = yc.load_categories(args.categories)

    token = yc.get_token(args.yf_dir)
    standings = yc.parse_standings(yc.fetch_standings(token))

    if len(standings) != 10:
        sys.exit("Resolved %d of 10 managers - refusing to write a partial "
                 "standings row. Check TEAM_MAP in yahoo_common.py."
                 % len(standings))

    # The row is labelled by GAMES PLAYED, not Yahoo's current_week. Yahoo
    # flips current_week to the next week first thing Tuesday, so labelling by
    # it saved the standings *through* Week 1 as "Week 2" - the power-lines
    # chart ran one week ahead and never had a Week 1. Games played is what the
    # records actually describe, and it doesn't care when Yahoo flips.
    played = max((s["wins"] + s["losses"] + s["ties"]) for s in standings.values())
    week = args.week or played

    weeks = feed.get("weeks", [])

    # Self-heal: a row labelled AHEAD of games played is a leftover from the old
    # current_week labelling (standings-through-Week-2 saved as "Week 3").
    # Drop it; the correctly labelled row replaces it below.
    before = len(weeks)
    weeks = [w for w in weeks if int(w.get("week", 0)) <= max(week, 0)]
    healed = before - len(weeks)

    # Self-heal: rebuild any finished week missing from the history, so the
    # power-lines chart never has a gap - whether from the old labelling bug
    # or a Tuesday run that didn't fire.
    have = {int(w.get("week", -1)) for w in weeks}
    rebuilt = []
    for missing in range(1, week):
        if missing not in have:
            tally = reconstruct_through(token, missing)
            weeks.append({"week": missing, "label": "Week %d" % missing,
                          "short": "W%d" % missing, "teams": snapshot(tally)})
            rebuilt.append(missing)

    if week < 1:
        action = "preseason - weeks unchanged"
    else:
        row = {"week": week, "label": "Week %d" % week,
               "short": "W%d" % week, "teams": snapshot(standings)}
        at = next((i for i, w in enumerate(weeks) if w.get("week") == week), None)
        if at is None:
            weeks.append(row)
            action = "added week %d" % week
        else:
            weeks[at] = row
            action = "refreshed week %d" % week
        weeks.sort(key=lambda w: w.get("week", 0))

    weeks.sort(key=lambda w: w.get("week", 0))
    if healed:
        action += "; dropped %d mislabelled row(s)" % healed
    if rebuilt:
        action += "; rebuilt week(s) %s" % ", ".join(map(str, rebuilt))
    original = json.dumps({k: v for k, v in feed.items() if k != "updated"},
                          sort_keys=True)
    feed["weeks"] = weeks
    feed["bonuses"] = bonuses_from_ledger(cat_meta, args.ledger)
    feed.setdefault("league", {})["season"] = yc.SEASON

    # This now runs every 10 minutes alongside the live feeds, so it must not
    # rewrite the file when nothing changed - a fresh "updated" stamp alone
    # would commit season.json every run all season.
    changed = json.dumps({k: v for k, v in feed.items() if k != "updated"},
                         sort_keys=True) != original
    if not changed and not args.dry_run:
        print("season.json unchanged (%s)." % action)
        return
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
