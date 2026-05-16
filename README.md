# Cricket stats — Play-Cricket pipeline

A small, self-contained pipeline that pulls Play-Cricket public-API data for
any club, builds an SQLite database, and produces match / scouting reports
(both Markdown and mobile-friendly HTML).

Originally built for **Rainham CC, Essex** (`site_id=5251`); the schema and
scripts are club-agnostic so any opposition can be scouted with one
`fetch.py --site-id <id>` call.

---

## TL;DR

```bash
# One-time set-up (or whenever the API token rotates)
export PC_API_TOKEN=<your-play-cricket-token>

# Refresh Rainham's data + rebuild DB
python3 fetch.py --site-id 5251 --only-recent 1
python3 build_db.py

# Generate the standard Rainham reports
python3 top_run_scorers.py
python3 streaks_and_fifties.py

# Scout an opposition club (fetch first, then report)
python3 fetch.py --site-id <opp-site-id> --first-season 2018 --workers 12
python3 build_db.py
python3 scout.py --club-name "Wickford"
```

Reports land in `reports/<YYYY-MM-DD>/<slug>/scout.{md,html}`.

---

## Repo layout

```
fetch.py                pulls Play-Cricket JSON; --site-id, --division-ids
build_db.py             loads cached JSON into SQLite
scout.py                opposition scouting report (any club)
top_run_scorers.py      Rainham — top run scorers report
streaks_and_fifties.py  Rainham — 50+, duck streaks, 10+ streaks
oneill_vs_hothi.py      example head-to-head player comparison

PLAN.md                 schema, scoring rules, source pipeline, caveats
CLAUDE.md               codebase guide for future Claude sessions

data/
  README.md             schema cheat-sheet
  rainham.db            SQLite DB (committed)
  raw/
    matches/<site_id>/<season>.json    per-season fixture lists
    match_detail/<match_id>.json       full scorecards (shared)
    league_table/<division_id>.json    cached league tables (shared)

reports/<YYYY-MM-DD>/<slug>/scout.{md,html}
```

The DB and the raw JSON cache are both committed. That makes the database
fully reproducible without hitting the API again, and lets reports run with
no toolchain beyond stock Python.

---

## Application components

The project has grown from a reporting script into four layers. Each is
independently runnable; later layers consume the SQLite DB built by the
first.

### 1. Data layer — fetch + cache + DB

| Component | Role |
| -- | -- |
| `fetch.py` | Pulls Play-Cricket JSON (`matches`, `match_detail`, `league_table`) for any `site_id`. Idempotent — only downloads what's missing. |
| `fetch_balls.py` | Pulls **ball-by-ball** from two backends — ResultsVault (PCS-scored matches) and NV Play (live-streamed). Not part of the public API; see `BALL_BY_BALL.md`. |
| `build_db.py` | Loads all cached JSON into `data/rainham.db` (SQLite), including a `balls` table for BBB. Club-agnostic schema — every row carries `team_*_club_id`. |
| `data/rainham.db` | The committed SQLite DB. Schema cheat-sheet in `data/README.md`, full spec in `PLAN.md`. |

### 2. Reports — Markdown + mobile HTML/PNG (`reports/`)

| Component | Output |
| -- | -- |
| `scout.py` | Opposition scouting report for any club — league table, finishing positions, top scorers/wicket-takers, head-to-head, patterns, verified videos. Emits `md` / `html` / long-PNG into a dated, versioned folder. |
| `top_run_scorers.py`, `streaks_and_fifties.py`, `oneill_vs_hothi.py` | Ad-hoc Rainham reports (top scorers, 50+/duck streaks, head-to-head). |
| `build_index.py` | Scans `reports/` and rewrites `reports/index.html` — the category-grouped overview. Auto-called by the report generators. |

### 3. Static web app — GitHub Pages (`app/`)

Pure HTML + vanilla JS, no build step. Mobile-first (`max-width: 540px`).
`_app_lib.py` holds the shared CSS, page chrome and universe filter.

| Builder | Page(s) |
| -- | -- |
| `build_data_repo.py` | `app/index.html` — 10-year 1st-XI game list with BBB + coverage columns. |
| `build_metadata.py` | `app/metadata/` — browse opposition clubs / per-club rosters / per-player metadata pages, with a `mailto:` / WhatsApp submission form (no backend). |
| `build_rcc_dashboard.py` + `build_rcc_js.py` | `app/rcc/` — RCC player-stats dashboard. Per-player batting/bowling tabs with season splits, home/away, position, BBB phase/spell slicers, and a **"Last 20 innings / spells"** visual bar exhibit. |
| `build_matchweek.py` | `app/matchweek.html` — **live fixture view**: 25 Essex 1st-XI matches (5 per top-5 division) for a Saturday, with scores, top performers, result, and a **win-probability chip** on games that have ball-by-ball data. |

### 4. Win-probability models (`model/`)

LightGBM ball-by-ball win-probability models, isotonic-calibrated.

| Component | Role |
| -- | -- |
| `model/poc/run_winprob_combined.py` | Trains two models — innings 1 (`P(team batting first wins)`, Brier 0.20) and the innings-2 chase (`P(chase succeeds)`, Brier 0.12). Fits isotonic calibration on a held-out split. Saves artefacts to `model/poc/winprob/`. |
| `model/poc/predict_winprob.py` | Inference — loads the saved models + calibrators and produces a per-ball win-prob trajectory for any match with BBB coverage. Consumed by `build_matchweek.py`. |

### Live data — Cloudflare Worker (`cf_worker/`)

`app/matchweek.html` defaults to **static JSON snapshots** committed under
`app/data/matchweek/`. Browsers can't call the Play-Cricket API directly
(CORS + a `403` on unknown origins), so for **truly-live** scores there's
a Cloudflare Worker (`cf_worker/`) that proxies the API, injects the token
server-side, adds CORS headers and a 60s edge cache. Append
`?worker=<worker-url>` to the matchweek URL to enable live mode. See
`cf_worker/README.md` for deployment.

### Deployment

`.github/workflows/static.yml` publishes the site to GitHub Pages on
**push to `main`** only. It stages just `app/` + `reports/` (the 18 GB
committed `data/` cache is excluded, and the `reports/scouting/.../latest`
symlinks are dereferenced) so the artefact stays under the 10 GB Pages cap.

---

## Updating the Rainham CC database with new data

The cache is **idempotent** — running a fetch only downloads matches it
doesn't already have on disk.

### Day-to-day (mid-season): pull the latest results

```bash
# Re-fetch the current season's fixture summary so newly-uploaded matches
# get added to the queue, then download any new match details.
python3 fetch.py --site-id 5251 --only-recent 1

# Rebuild the SQLite DB from whatever's now on disk.
python3 build_db.py

# Regenerate the Rainham-specific reports
python3 top_run_scorers.py
python3 streaks_and_fifties.py

# (Optional) refresh the Rainham 1st XI's current league table
python3 fetch.py --site-id 5251 --first-season 2026 --last-season 2026 \
                 --division-ids <current-division-id> --force-league-tables
```

Find the current division-id with:

```sql
SELECT competition_id, competition_name FROM matches
WHERE (home_team_id='51207' OR away_team_id='51207')
  AND competition_type='League' AND season=2026
LIMIT 1;
```

### Re-fetch a specific older season

```bash
python3 fetch.py --site-id 5251 --first-season 2024 --last-season 2024 \
                 --force-seasons --force-details
python3 build_db.py
```

`--force-seasons` re-pulls the season summary; `--force-details` re-pulls
**every** match detail in that season. Both are slow — only use when you
suspect the underlying data has been edited.

### Backfill from earlier history

The cache currently covers 1990–latest. To add older or newly-published
seasons, drop them into `--first-season` / `--last-season` and run.

---

## Generating a scouting report for an opponent

### Step 1 — find their `site_id`

If we've already played them, their club_id is in the DB:

```sql
SELECT club_id, club_name FROM clubs WHERE club_name LIKE '%Wickford%';
```

For most clubs, `site_id == club_id`. Confirm with one curl:

```bash
curl -s "https://play-cricket.com/api/v2/matches.json?site_id=<id>&api_token=$PC_API_TOKEN&season=2026" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('matches',[])))"
```

If that returns a number > 0, the site_id is correct.

### Step 2 — fetch their data

```bash
python3 fetch.py --site-id <id> --first-season 2018 --workers 12
```

About 30 KB of JSON per match; a typical 8-season club takes a couple of
minutes to fully cache.

### Step 3 — find their league division-ids and cache the tables

```sql
SELECT season, competition_id, competition_name FROM matches
WHERE (home_club_id='<id>' OR away_club_id='<id>')
  AND competition_type='League' AND season BETWEEN 2023 AND 2026
GROUP BY season, competition_id, competition_name
ORDER BY season;
```

Then:

```bash
python3 fetch.py --site-id <id> --first-season 2026 --last-season 2026 \
                 --division-ids <id1>,<id2>,<id3>,<id4>
```

(The `--first/--last-season 2026` is just to satisfy the matches loop —
no new matches will be fetched if they're already cached.)

### Step 4 — rebuild and report

```bash
python3 build_db.py
python3 scout.py --club-name "Wickford"        # or --club-id <id>
```

Produces:

```
reports/<today>/wickford_cc/scout.md     # full report
reports/<today>/wickford_cc/scout.html   # mobile-optimised; long-screenshot for WhatsApp
```

### What the scout report contains

All sections are **1st XI only** (Saturday team), **League + Cup**
(Friendlies excluded), unless noted.

| § | Contents |
| -- | -- |
| 0 | Current league table with W, Pts and last-5 form pills (their row highlighted). Falls back to last completed season if the current is empty. |
| 1 | Last 3 seasons' finishing positions (League games only — P aligns with the published league table). |
| 2 | Top run scorers (last 3 seasons). |
| 3 | Top wicket takers (last 3 seasons). |
| 4 | Head-to-head vs Rainham 1st XI in the cache + upcoming fixtures. |
| 5 | Last 10 1st XI played matches with innings scores and a Play-Cricket match link on each date. |
| 6 | Patterns: bat 1st vs 2nd W/L/D · home vs away W/L/D · what they do when they win the toss (bat/field choice and outcome of each) · per-season batting + bowling avg vs Rainham. All as inline HTML/CSS bar charts so the screenshot stays readable. |
| 7 | Web / video links — split into _Official_, _Verified videos_, _Per-match YouTube search probes_ (one click per fixture), and _Speculative — name match only_. The API doesn't expose video URLs so the per-match links are search probes, not direct hits. |

### Sharing the HTML on WhatsApp

Open the HTML on a phone (Chrome / Safari), use the **scrolling screenshot**
feature to capture the whole page as one tall image, paste into the chat.

- Pixel: regular screenshot → "Capture more" → scroll → save
- iPhone: regular screenshot → tap the preview → "Full Page" → save as PDF/PNG
- Samsung: palmswipe screenshot → tap "Scroll capture" repeatedly

---

## Extracting this folder into its own repo

This whole pipeline is self-contained — no imports / paths reach outside
`stats/`. Splitting it out preserves history and gives you a smaller,
cleaner repo for future work.

### Easy way (desktop or Termux on Android)

You need: a clean clone of the source repo, a fresh empty repo on GitHub,
and `git`. On Android, install [Termux](https://termux.dev/) then
`pkg install git`.

```bash
# 1. Clone the source repo and split out the stats history
git clone https://github.com/gingerjono/rcc-2020-site.git work
cd work
git subtree split --prefix=stats -b stats-only

# 2. Create the new empty repo on GitHub (web / mobile app — name it
#    e.g. "rcc-cricket-stats", no README/license/.gitignore — must be empty).

# 3. Push the split branch as the new repo's main
git push https://github.com/<you>/rcc-cricket-stats.git stats-only:main
```

That's it. The new repo will have only the `stats/` files, but with full
commit history scoped to those files.

### Optional cleanups in the new repo

```bash
git clone https://github.com/<you>/rcc-cricket-stats.git
cd rcc-cricket-stats

# (a) Move everything up out of the stats/ subfolder (your call — paths in
# the scripts are all relative to __file__ so they keep working either way).
# If you do flatten, just `git mv stats/* .` then commit.

# (b) Rotate the API token. Right now fetch.py has a default that matches
# the rcc-2020-site/php/globals.php token. For the new repo, set
# PC_API_TOKEN in your shell/CI rather than committing a token:
unset PC_API_TOKEN  # or export the new one
# ...and edit fetch.py to drop the literal default if you want it strict.
```

### Optional cleanup in `rcc-2020-site` after extraction

If you no longer want the duplicated copy in the original repo:

```bash
# Back in rcc-2020-site
git rm -r stats
git commit -m "stats: extracted to its own repo (https://github.com/<you>/rcc-cricket-stats)"
git push
```

### Pre-prepared branch

For convenience, a `stats-only` subtree branch has been pushed to
`origin` in this repo. You can clone the new repo and pull straight from
that branch:

```bash
# After creating the empty rcc-cricket-stats repo on GitHub:
git clone https://github.com/<you>/rcc-cricket-stats.git
cd rcc-cricket-stats
git pull https://github.com/gingerjono/rcc-2020-site.git stats-only
git push origin main
```

If you re-run `git subtree split --prefix=stats -b stats-only --rejoin` in
the original repo you can keep this branch updated incrementally, though
in practice splitting once and committing to the new repo from then on is
simpler.

### What about Android-only?

If you don't have a desktop available:
- Install **Termux** from F-Droid (the Play Store version is unmaintained).
- `pkg install git python` then follow the "Easy way" above. Termux has
  `git subtree`, parallel `urlopen`, SQLite, and Python all out of the box,
  so you can also re-run `fetch.py` and `scout.py` straight from your phone.
- Sign into GitHub via `gh auth login` (`pkg install gh`) or use a
  [personal access token](https://github.com/settings/tokens) with `repo`
  scope when pushing.
- For the "create empty repo" step, the GitHub mobile app does NOT support
  repo creation. Use mobile Chrome/Firefox on github.com (works fine).

---

## Validation discipline

Before quoting any headline number, sanity-check it against
`<club>.play-cricket.com`'s own leaderboard. The first build of this DB
came in at ~50% of the correct totals because of a duplicate-`innings_number`
bug. One reference comparison would have caught it.

---

## Provenance

- API: `https://play-cricket.com/api/v2/{matches,match_detail,league_table}.json`
- Reverse-engineered from `php/epizy/cron_api_matches_master.php` and
  `php/cron_api_matches_listener.php` in the rcc-2020-site repo.
- Schema documented in `PLAN.md` and `data/README.md`.
- Codebase guide for future Claude sessions in `CLAUDE.md`.
