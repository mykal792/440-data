#!/usr/bin/env python3
"""
pull_dk.py - 440 & Friends, DraftKings Challenge + High Score board.

Writes docs/dk.json, overwritten every run:

    managers      per-team move count and tax owed
    transactions  the scrolling ticker, newest first
    high_scores   weeks 1-15, filled from bank-ledger.json

THE TAX RULE
    50 cents per TRANSACTION, not per player. A waiver claim that adds one
    player and drops another is one move, not two. Trades are not taxed.
    Commissioner actions are not taxed. Only status 'successful' counts.

    Verified against the board on 2026-09-01: three add/drop transactions,
    six players, Schilling shown as 3 moves / $1.50.

    python3 pull_dk.py                          # -> docs/dk.json
    python3 pull_dk.py --dry-run
    python3 pull_dk.py --fixtures ~/440-samples # offline

PAGINATION
    Yahoo returns at most 25 transactions per request. We page until a short
    page comes back. Without this the tally silently undercounts from about
    week 10 onward - which looks plausible, and is the worst kind of wrong.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_common as yc

try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:                                  # pragma: no cover
    EASTERN = timezone.utc

TAXABLE_TYPES = {"add", "drop", "add/drop"}
PAGE = 25


def fetch_all_transactions(token):
    """Page through every transaction in the league."""
    pages = []
    start = 0
    while True:
        payload = yc.fetch("league/%s/transactions;start=%d;count=%d"
                           % (yc.LEAGUE_KEY, start, PAGE), token)
        container = _container(payload)
        n = int(yc.fnum(container.get("count")))
        pages.append(payload)
        if n < PAGE:
            break
        start += PAGE
        if start > 1000:                            # paranoia
            break
    return pages


def _container(payload):
    league = payload["fantasy_content"]["league"]
    return (yc.find_key(league[1:], "transactions")
            or league[1].get("transactions") or {})


def team_key_to_manager(team_key):
    if not team_key:
        return None
    return yc.TEAM_MAP.get(str(team_key).split(".")[-1])


def fmt_date(timestamp):
    try:
        dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(EASTERN)
    except (TypeError, ValueError):
        return ""
    return "%s %d" % (dt.strftime("%b"), dt.day)


def parse_transactions(pages):
    """-> (moves_by_manager, ticker_rows)

    ticker row: {manager, added, add_pos, dropped, drop_pos, date, _ts}
    """
    moves, rows, seen = {}, [], set()

    for payload in pages:
        container = _container(payload)
        for wrapper in yc.numbered(container):
            t = wrapper.get("transaction")
            if t is None:
                continue
            meta = yc.merge_meta(t[0] if isinstance(t, list) else t)

            if meta.get("status") != "successful":
                continue
            if meta.get("type") not in TAXABLE_TYPES:
                continue                            # trades, commish actions

            tkey = meta.get("transaction_key")
            if tkey in seen:
                continue                            # page overlap
            seen.add(tkey)

            players = {}
            if isinstance(t, list) and len(t) > 1:
                players = yc.find_key(t[1:], "players") or {}

            added = dropped = None
            add_pos = drop_pos = ""
            manager = None

            for pw in yc.numbered(players):
                p = pw.get("player")
                if p is None:
                    continue
                pmeta = yc.merge_meta(p[0] if isinstance(p[0], list) else p)
                td = yc.find_key(p[1:], "transaction_data")
                # NOTE: this is a LIST on an add and a BARE DICT on a drop.
                # Same payload, same transaction. Handle both or lose half.
                if isinstance(td, list):
                    td = td[0] if td else {}
                td = td or {}

                name = yc.clean((pmeta.get("name") or {}).get("full", ""))
                pos = "%s - %s" % (pmeta.get("editorial_team_abbr", ""),
                                   pmeta.get("display_position", ""))

                if td.get("type") == "add":
                    added, add_pos = name, pos
                    manager = manager or team_key_to_manager(
                        td.get("destination_team_key"))
                elif td.get("type") == "drop":
                    dropped, drop_pos = name, pos
                    manager = manager or team_key_to_manager(
                        td.get("source_team_key"))

            if manager is None:
                continue

            # ONE move per transaction, regardless of how many players moved.
            moves[manager] = moves.get(manager, 0) + 1
            rows.append({
                "manager": yc.DISPLAY[manager],
                "added": added or "",
                "add_pos": add_pos if added else "",
                "dropped": dropped or "",
                "drop_pos": drop_pos if dropped else "",
                "date": fmt_date(meta.get("timestamp")),
                "_ts": int(yc.fnum(meta.get("timestamp"))),
            })

    rows.sort(key=lambda r: -r["_ts"])
    return moves, rows


def week_window(sb_payload):
    """-> (start_epoch, end_epoch) for the fantasy week, Eastern time.

    Yahoo puts week_start / week_end on each matchup (Wed-Mon for week 1).
    Returns None if they can't be read, in which case we don't filter.
    """
    league = sb_payload["fantasy_content"]["league"]
    sb = yc.find_key(league[1:], "scoreboard") or league[1].get("scoreboard")
    container = (sb or {}).get("0", {}).get("matchups") or (sb or {}).get("matchups")
    for wrapper in yc.numbered(container or {}):
        m = wrapper.get("matchup")
        mm = yc.merge_meta(m) if isinstance(m, list) else (m or {})
        start, end = mm.get("week_start"), mm.get("week_end")
        if start and end:
            try:
                s = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=EASTERN)
                e = datetime.strptime(end, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=EASTERN)
                return int(s.timestamp()), int(e.timestamp())
            except ValueError:
                return None
    return None


def filter_ticker(rows, window):
    """Scope the ticker to one fantasy week.

    The move counts and the pot stay season-long - those are cumulative on the
    board. Only the scrolling ticker is scoped, so it shows this week's waiver
    activity rather than every move since August.

    Before the week has started (preseason), there is nothing inside the window
    yet, so we show everything up to now instead of an empty ticker.
    """
    if not window:
        return rows
    start, end = window
    now = int(datetime.now(tz=timezone.utc).timestamp())
    if now < start:
        return [r for r in rows if r["_ts"] <= now]
    return [r for r in rows if start <= r["_ts"] <= end]


def build_high_scores(ledger_weeks, last_week=15):
    out = []
    for week in range(1, last_week + 1):
        entry = (ledger_weeks.get(week) or {}).get("high")
        out.append({
            "week": week,
            "manager": yc.DISPLAY.get(entry["manager"], "") if entry else "",
            "score": entry["value"] if entry and entry["value"] else "0.00",
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="docs")
    ap.add_argument("--week", type=int, help="which week's ticker to show")
    ap.add_argument("--all-transactions", action="store_true",
                    help="show every move this season, not just this week")
    ap.add_argument("--yf-dir", help="directory holding .yahoofantasy")
    ap.add_argument("--fixtures", help="parse saved JSON from this dir, no network")
    ap.add_argument("--ledger", help="path to bank-ledger.json")
    ap.add_argument("--categories", help="path to bonus-categories.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    meta = yc.load_categories(args.categories)
    ledger_weeks, _awards = yc.load_ledger(args.ledger)

    if args.fixtures:
        fx = Path(args.fixtures).expanduser()
        pages = [json.loads((fx / "transactions_sample.json").read_text())]
        sb_path = fx / "scoreboard_sample.json"
        sb_raw = json.loads(sb_path.read_text()) if sb_path.exists() else None
    else:
        token = yc.get_token(args.yf_dir)
        week = args.week or yc.current_week(token)
        pages = fetch_all_transactions(token)
        sb_raw = yc.fetch_scoreboard(token, week)

    moves, all_rows = parse_transactions(pages)

    # Move counts and the pot are season-long; only the ticker is scoped.
    window = week_window(sb_raw) if (sb_raw and not args.all_transactions) else None
    ticker = filter_ticker(all_rows, window)
    for r in ticker:
        r.pop("_ts", None)

    payload = {
        "season": yc.SEASON,
        "tax_per_move": 0.5,
        "updated": yc.now_iso(),
        "high_score_bonus": "%.2f" % meta["high_score"]["amount"],
        "managers": sorted(
            [{"name": yc.DISPLAY[k], "moves": moves.get(k, 0)}
             for k in yc.MANAGER_KEYS],
            key=lambda m: (-m["moves"], m["name"])),
        "transactions": ticker,
        "high_scores": build_high_scores(ledger_weeks),
    }

    total_moves = sum(moves.values())
    print("%d taxable moves season-to-date, $%.2f pot, %d ticker rows (%s)"
          % (total_moves, total_moves * 0.5, len(ticker),
             "all season" if window is None else "this week"))

    text = json.dumps(payload, indent=2) + "\n"
    out = Path(args.out_dir).expanduser().resolve() / "dk.json"
    if args.dry_run:
        print("--- would write %s ---" % out)
        print(text[:1800])
        return
    tmp = str(out) + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, out)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
