# TURN THIS ON when Yahoo access is live

Nothing in this repo runs on a schedule right now. Both workflows were disabled
on 2026-09-01 because there is no Yahoo token yet, and a scheduled run that can
only fail sends an email every ten minutes.

Work down this list once Yahoo enables the Fantasy Sports scope.

---

## 1. Confirm the scope is really on

Open <https://developer.yahoo.com/apps/create/> and check that **Fantasy Sports**
now appears under API Permissions. An email saying "you're all set" is not
confirmation — the checkbox is.

Then, locally:

```bash
python3 -c "from yahoofantasy import Context; print(Context().get_leagues('nfl', 2026))"
```

A list of leagues means you're through. Another 401 means stop here.

## 2. Check the field paths and nicknames

```bash
python3 pull_standings.py --inspect
```

Fix anything reading `NOT FOUND` in the `FIELD_PATHS` dict at the top of that
file. Also check the nickname list at the bottom of the output — any manager
showing `UNMAPPED` will silently vanish from that week's snapshot rather than
raising an error. Correct them in `KEY_BY_NICKNAME`.

While you're in there, look at what `league.transactions()` returns. If it
carries the added and dropped players with their team and position, the
DraftKings ticker can be automated too.

## 3. Find out whether the token rotates

This decides whether the automation can run unattended.

```bash
cp ~/.yahoofantasy /tmp/tok-before
python3 pull_standings.py
diff <(xxd /tmp/tok-before) <(xxd ~/.yahoofantasy) && echo STABLE || echo ROTATES
```

**STABLE** — store the token once and forget it.
**ROTATES** — the stored secret goes stale after the first run and the job dies
until you update it. Better to know now than in week 4.

## 4. Store the token as a secret

```bash
base64 -w0 < ~/.yahoofantasy      # macOS: base64 -i ~/.yahoofantasy
```

Repo → Settings → Secrets and variables → Actions → New repository secret,
named `YAHOO_TOKEN_B64`.

## 5. Write pull_live.py

It does not exist yet. It has to write `docs/scoreboard.json` and
`docs/bonus.json`, and it could not be written earlier because it depends on the
exact shape of a live Yahoo response.

The scoreboard half is easy — `league.scoreboard(week)` gives every matchup's
score in one call. The bonus half is harder: six of the fifteen categories come
off the scoreboard, the other nine need a roster call per team.

**Two rules it must follow, or it will destroy work:**

- `season.json` — read and append. Never overwrite. It holds every past week and
  the rank chart is drawn from that history. Yahoo cannot give week 3 back later.
- `squid.json` — preserve the `duel` key. No API knows about duels; they are set
  by hand in the commish page, and a scores refresh must not wipe one.

## 6. Switch the schedules back on

In **`.github/workflows/weekly-update.yml`** — uncomment the `schedule:` block
above `on:` and move it under `on:`. Also restore the push trigger, which is what
lets settling a week's bonuses regenerate the site straight away:

```yaml
on:
  schedule:
    - cron: '0 9 * * 2'    # 5am EDT  (Sep - early Nov)
    - cron: '0 10 * * 2'   # 5am EST  (Nov - Jan)
  push:
    paths:
      - 'bank-ledger.json'
      - 'bonus-categories.json'
  workflow_dispatch:
```

In **`.github/workflows/live-update.yml`** — same idea:

```yaml
on:
  schedule:
    - cron: '*/10 17-23 * * 0'   # Sunday 1pm - 8pm ET
    - cron: '*/10 0-4 * * 1'     # Sunday night into Monday ET
    - cron: '*/10 0-4 * * 2'     # Monday Night Football
    - cron: '*/10 0-4 * * 5'     # Thursday Night Football
  workflow_dispatch:
```

**Do not comment out just the cron lines to disable these again.** An empty
`schedule:` key still fires the workflow and produces a "No jobs were run" email
every few minutes. Remove the whole key, as it is now.

## 7. Test before trusting the schedule

Actions tab → pick the workflow → **Run workflow**. A green run and a new commit
to `docs/` means the chain works end to end. Don't wait until Tuesday, or until
Sunday kickoff, to find out.

---

## Daylight saving

Cron in GitHub Actions is UTC and does not follow DST. The times above are for
EDT. When the clocks change in early November, shift every hour figure one later
or the jobs run an hour early.

## The weekly rhythm, once it's running

Records and averages update themselves. Bonus money doesn't — who won a category
needs a human.

```bash
python3 settle_week.py 3
```

Paste the result into `bank-ledger.json` and commit. The push trigger picks it up.

**Health check:** load the homepage and look at the standings caption. It should
name the current week and show no amber "Offline" tag.
