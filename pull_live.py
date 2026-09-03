#!/usr/bin/env python3
"""
pull_live.py - 440 & Friends, live board.

Writes THREE files, all overwritten every run:

    docs/scoreboard.json    current week's matchups and scores
    docs/bonus.json         current week's bonus + high score, live top 3
    docs/top-players.json   every starter's points, highest to lowest, plus
                             a short per-player stat line (yards, TD, etc.)

It does NOT touch season.json. That belongs to pull_standings.py, which also
merges the bank ledger into it - two scripts writing one file would clobber
each other.

top-players.json rides on the same ten roster calls the bonus board already
makes - no new PER-RUN API traffic. It only lists starters: bench
performances already have a home in the Bench Warmer bonus category, and
mixing the two would just duplicate it. The stat line comes from the SAME
roster payload too (the "stats" subresource already returns the raw
per-category breakdown alongside the point total, parse_rosters just wasn't
reading it before).

The one real addition: mapping a raw stat_id to "PASS YD" etc. needs the
season's stat category table, which costs one extra call the very first
time this ever runs (or if stat-categories.json goes missing) and is then
read from that local file - see yc.load_stat_categories().

Twelve API calls per run in steady state (ten rosters, one scoreboard, one
standings), so a 10-minute cadence during games is comfortable.

    python3 pull_live.py                           # current week -> docs/
    python3 pull_live.py --week 3                  # force a week
    python3 pull_live.py --dry-run                 # print, write nothing
    python3 pull_live.py --fixtures ~/440-samples  # offline, saved JSON

The bonus board answers "do I have a shot at this week's bonus". Weekly winners
are settled separately with settle_week.py once a week is final.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc


def build_scoreboard(week, status, matchups, projected):
    """`score` is always the truth; `projected` sits alongside it.

    Before kickoff every score is 0.00, which reads as if nobody is playing.
    Yahoo's projected total gives the cards a real number to show instead, and
    it stays useful during games because Yahoo updates it live - a team on 40
    with six starters left still projects near 110.

    The block decides what to display: projections while `status` is
    "pregame", actual scores once it is "live" or "final". Keeping both in the
    feed means that choice can change without touching this script.
    """
    def side(key):
        manager = key[0]
        return {"name": yc.DISPLAY.get(manager, ""),
                "face": manager,
                "score": round(key[1], 2),
                "projected": projected.get(manager, 0.0)}

    return {
        "week": week,
        "status": status,
        "updated": yc.now_iso(),
        "matchups": [{"home": side(m["home"]), "away": side(m["away"])}
                     for m in matchups],
    }


def build_bonus(week, status, rosters, matchups, standings, meta, show_pregame):
    cat = meta["_by_week"].get(week, {})
    rows, ascending = yc.compute_bonus(week, rosters, matchups, standings)

    # ALWAYS exactly three rows in both lists. The bonus board sizes itself to
    # its content and the HTML blocks can't talk to each other, so a list that
    # shrinks leaves white space on the page. Before kickoff every value is
    # 0.00 and all ten managers would tie, so pregame shows placeholders only.
    if status == "pregame" and not show_pregame:
        cat_leaders, high_leaders = [], []
    else:
        cat_leaders = yc.rank_rows(rows, ascending=ascending)
        high_leaders = yc.rank_rows(
            yc.high_score_rows(rosters, matchups, standings))

    cat_leaders = yc.pad_leaders(cat_leaders)
    high_leaders = yc.pad_leaders(high_leaders)

    return {
        "league": {"season": yc.SEASON},
        "live": {
            "state": status,
            "week": week,
            "updated": yc.now_iso(),
            "category": {
                "week": week,
                "key": cat.get("key", "no-bonus"),
                "label": cat.get("label", "No Bonus"),
                "description": cat.get("description", ""),
                "amount": meta.get("category_amount", 25),
                "leaders": cat_leaders,
            },
            "high": {
                "key": "high-score",
                "label": meta["high_score"]["label"],
                "description": meta["high_score"]["description"],
                "amount": meta["high_score"]["amount"],
                "leaders": high_leaders,
            },
        },
    }


def build_top_players(week, status, rosters, stat_buckets):
    """Every starter's points this week, highest to lowest.

    One flat list, not pre-sliced by position - the block filters client-side
    the same way the draft-selections page does, so a design change there
    doesn't require touching this script.

    stat_buckets is yc.resolve_stat_buckets(...) - already resolved once in
    main() so this doesn't redo that work per player.
    """
    rows = []
    for manager, team in rosters.items():
        for p in team["players"]:
            if p["slot"] not in yc.STARTING_SLOTS:
                continue
            rows.append({
                "name": p["name"],
                "position": p["display_position"],
                "team": p.get("team", ""),
                "manager": manager,
                "manager_display": yc.DISPLAY.get(manager, manager),
                "points": round(p["points"], 2),
                "stats": yc.summarize_stats(p.get("raw_stats"), stat_buckets),
            })
    rows.sort(key=lambda r: -r["points"])
    return {
        "week": week,
        "status": status,
        "updated": yc.now_iso(),
        "players": rows,
    }


def write_json(path, payload, dry_run):
    text = json.dumps(payload, indent=2) + "\n"
    if dry_run:
        print("--- would write %s ---" % path)
        print(text[:1800])
        return
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)
    print("wrote %s" % path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="override the week number")
    ap.add_argument("--out-dir", default="docs", help="where the json files live")
    ap.add_argument("--yf-dir", help="directory holding .yahoofantasy")
    ap.add_argument("--fixtures", help="parse saved JSON from this dir, no network")
    ap.add_argument("--categories", help="path to bonus-categories.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show-pregame", action="store_true",
                    help="list leaders even before kickoff (all zeros)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    meta = yc.load_categories(args.categories)

    if args.fixtures:
        fx = Path(args.fixtures).expanduser()
        rosters_raw = {tid: json.loads((fx / ("roster_t%s.json" % tid)).read_text())
                       for tid in yc.TEAM_MAP
                       if (fx / ("roster_t%s.json" % tid)).exists()}
        standings_raw = json.loads(
            (fx / ("league_%s_standings.json" % yc.LEAGUE_KEY)).read_text())
        sb_path = fx / "scoreboard_sample.json"
        sb_raw = json.loads(sb_path.read_text()) if sb_path.exists() else None
        stat_categories = yc.load_stat_categories(fixtures_dir=args.fixtures)
    else:
        token = yc.get_token(args.yf_dir)
        week = args.week or yc.current_week(token)
        rosters_raw = yc.fetch_rosters(token, week)
        sb_raw = yc.fetch_scoreboard(token, week)
        standings_raw = yc.fetch_standings(token)
        stat_categories = yc.load_stat_categories(token=token)

    stat_buckets = yc.resolve_stat_buckets(stat_categories)
    if not stat_categories:
        print("  ! no stat-categories.json and no fixture for it - "
              "top-players.json stat lines will come back empty")

    rosters = yc.parse_rosters(rosters_raw)
    standings = yc.parse_standings(standings_raw)

    if sb_raw:
        sb_week, status, matchups = yc.parse_scoreboard(sb_raw)
        projected = yc.parse_projected(sb_raw)
    else:
        sb_week, status, matchups = (args.week or 1), "pregame", []
        projected = {}

    week = args.week or sb_week or 1

    print("week %d | %s | %d managers | %d matchups"
          % (week, status, len(rosters), len(matchups)))
    if len(rosters) != 10:
        print("  ! expected 10 managers - check TEAM_MAP in yahoo_common.py")

    write_json(out_dir / "scoreboard.json",
               build_scoreboard(week, status, matchups, projected), args.dry_run)
    write_json(out_dir / "bonus.json",
               build_bonus(week, status, rosters, matchups, standings, meta,
                           args.show_pregame),
               args.dry_run)
    write_json(out_dir / "top-players.json",
               build_top_players(week, status, rosters, stat_buckets), args.dry_run)


if __name__ == "__main__":
    main()
