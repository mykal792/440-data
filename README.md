# 440 & Friends — site data

This repo does one job: put two files on the internet at a fixed address.

```
docs/standings.json     records, averages, bank
docs/bonus.json         the live bonus board
```

Your website reads those two addresses. It never talks to Yahoo. A script here
talks to Yahoo and writes the files; the site reads whatever it finds.

That separation is the point — if Yahoo is unavailable, the last good file is
still sitting there and the site keeps working.

---

## Part 1 — prove the delivery works (do this now, no Yahoo needed)

The files in `docs/` are already filled in with preseason placeholders, so you
can prove the whole path works before the API is open.

**1. Make a GitHub repo** and upload everything in this folder. Keep the folder
structure exactly as-is — `docs/` and `.github/workflows/` matter.

**2. Turn on GitHub Pages.** Repo → Settings → Pages → Source: *Deploy from a
branch* → Branch: `main`, folder: `/docs` → Save. Wait a minute.

**3. Open the address it gives you, with `/standings.json` on the end:**

```
https://YOURNAME.github.io/YOURREPO/standings.json
```

You should see the JSON in your browser. **If this doesn't load, stop —
nothing downstream can work.**

**4. Point the blocks at it.** In each block's HTML, near the top of the
`<script>`, change the `SOURCE` line:

```js
const SOURCE = 'standings.json';
```
becomes
```js
const SOURCE = 'https://YOURNAME.github.io/YOURREPO/standings.json';
```

The bonus board uses `bonus.json` instead. Re-paste the blocks into Wix.

**5. Check the bottom-left of the standings.** It reads `PRESEASON · OFFLINE
COPY` in amber when it isn't connected, and `Updated <date>` in gray when it
is. **Gray means the delivery path works.**

At that point everything is built except the Yahoo half. Editing
`docs/standings.json` by hand on GitHub changes your live site within a minute.

---

## Part 2 — when Yahoo enables the scope

**1. Confirm it's really on.** Open the create-app form at
developer.yahoo.com/apps/create/ and check that *Fantasy Sports* now appears
under API Permissions. An email saying "you're all set" is not confirmation.

**2. Check the field paths:**

```bash
python3 pull_standings.py --inspect
```

Anything reading `NOT FOUND` gets corrected in the `FIELD_PATHS` dict at the
top of that file. Also confirm Yahoo's manager nicknames match the keys in
`AVATAR_BY_MANAGER` and in `bank-ledger.json`.

**3. Find out whether your token rotates.** This decides whether the automation
can run unattended:

```bash
cp ~/.yahoofantasy /tmp/tok-before
python3 pull_standings.py
diff <(xxd /tmp/tok-before) <(xxd ~/.yahoofantasy) && echo STABLE || echo ROTATES
```

**STABLE** — store the token once and forget it.
**ROTATES** — the stored secret goes stale after one run and the job will fail
until you update it. Worth knowing before Week 1, not during it.

**4. Store the token as a secret:**

```bash
base64 -w0 < ~/.yahoofantasy      # macOS: base64 -i ~/.yahoofantasy
```

Repo → Settings → Secrets and variables → Actions → New repository secret,
named `YAHOO_TOKEN_B64`.

**5. Test the job by hand** before trusting the schedule. Actions tab → *Weekly
league update* → **Run workflow**. A green run and a new commit to
`docs/standings.json` means the chain works end to end.

---

## The two jobs

**`weekly-update.yml`** — Tuesday 5am, plus any time you edit
`bank-ledger.json`. Writes `docs/standings.json`. One API call, cheap.

Cron is UTC and ignores daylight saving, so Tuesday 5am ET is listed twice —
`0 9 * * 2` for EDT and `0 10 * * 2` for EST. The pull is idempotent, so a
double run on one Tuesday just rewrites the same file.

**`live-update.yml`** — every 10 minutes during game windows only. Writes
`docs/bonus.json`. Needs `pull_live.py`, which can't be finished until the
real API response shape is visible.

Two honest caveats on the live job. GitHub's scheduled runs are queued, not
guaranteed — under load they arrive late and are occasionally skipped, so
"every 10 minutes" is realistically every 10–25. And the roster-based bonus
categories cost ten API calls per run against an undocumented rate limit.
If either becomes a problem, a small always-on host is the fix.

---

## Weekly rhythm, once it's all running

Records and averages update by themselves. Bonus money doesn't — who won a
bonus is a judgment call, so it stays yours.

```bash
python3 settle_week.py 3
```

Paste the result into `bank-ledger.json` and commit. The weekly workflow
watches that file, so committing it regenerates the site data straight away.
You can do this from GitHub's web editor on your phone.

**Health check:** load the homepage and look at the bottom-left of the
standings. Gray, and it's fine.
