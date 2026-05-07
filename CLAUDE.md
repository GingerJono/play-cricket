# `stats/` — Play-Cricket data + reports

This folder owns Rainham CC's **and any opposition's** Play-Cricket cache.
Built from `php/epizy/cron_api_matches_master.php` + `php/cron_api_matches_listener.php`,
which is the same API the live site uses (see `php/globals.php` for the
canonical token / site_id).

## Publishing

The GitHub Pages workflow (`.github/workflows/static.yml`) only fires
on **push to `main`**, i.e. when a feature branch is merged in. Branch
pushes during development do **not** publish — feel free to push WIP
to a feature branch.

When you do prepare something to merge:

- Run `python3 build_index.py` (or rely on the auto-call from
  `scout.py` / the ad-hoc generators) before merging so the index
  matches what's in the tree.
- Don't commit secrets into the repo — the API token lives in
  `php/globals.php` (not in-tree) and `fetch.py` reads it via
  `PC_API_TOKEN`. Anything that lands in `main` gets deployed.

## When to read this file

- The user asks for any **stat about a Rainham player or match** (runs,
  wickets, streaks, comparisons, history).
- The user asks for an **opposition / scouting report**, or a comparison
  involving a non-Rainham club.
- The user asks for a **new report** that should be repeatable.

If the question is "ad-hoc, one-off SQL against the existing DB" → just
write the query against `stats/data/rainham.db`. If the answer needs the
DB to grow / be refreshed → see _Workflow_ below.

## Companion docs

- **`FRONTEND_DESIGN.md`** — the `frontend-design` skill verbatim, plus
  the project-specific constraints (mobile-first 540px, no build step,
  tabular numerics, contrast floor). Read this before redesigning any
  page in `app/` or any artefact that emits HTML/PNG.
- **`PLAN.md`** — full schema + scoring rules + caveats for the data
  layer.
- **`BALL_BY_BALL.md`** — the auth/endpoint trail for the two BBB
  backends (ResultsVault + NV Play).
- **`PLAN_NEXT.md`** — current iteration plan (visual rebuild of the
  metadata pages, RCC player-stats dashboard, BBB analytics:
  innings buckets, player buckets, bowling spells, vs LHB/RHB).

## What's in the cache

```
stats/
  fetch.py           pulls Play-Cricket JSON; --site-id and --division-ids
  fetch_balls.py     pulls ball-by-ball from BOTH backends Play-Cricket
                     embeds: ResultsVault (PCS-scored matches) and
                     NV Play (live-streamed / NV-scored matches). Tries
                     RV first; falls back to NV Play when RV is empty.
                     Not exposed by the public play-cricket API; see
                     `BALL_BY_BALL.md` for the auth + endpoint trail.
  _rv_token.js       auto-generated node helper used by fetch_balls.py
                     to compute the X-IAS-API-REQUEST auth header.
  _rv_balls.py       builds a per-match {rv_player_id -> pc_player_id}
                     map by matching RV's MatchTeams[].TeamMembers[]
                     names against the PC match_players roster.
  _nvplay_balls.py   parses NV Play's per-ball `C` field
                     ("X to Y, outcome") and resolves names against
                     Match.Team{1,2}Players[].ExternalId.
  _disambig.py       second-pass disambiguation that recovers NULL
                     batter_id / bowler_id rows by cross-referencing
                     the scorecard `batting` / `bowling` tables.
  build_db.py        loads cached JSON into SQLite (data/rainham.db)
  scout.py           generic opposition scouting report (any club).
                     Emits md / html / a long mobile-friendly png inside a
                     versioned `v<N>/` directory so the same club can be
                     reported on multiple times in a single day.
  build_index.py     scans `reports/` and rewrites `reports/index.html`
                     (category-grouped overview of every report).
  top_run_scorers.py     Rainham-specific report (writes to reports/ad-hoc/)
  streaks_and_fifties.py Rainham-specific report (writes to reports/ad-hoc/)
  oneill_vs_hothi.py     example head-to-head    (writes to reports/ad-hoc/)
  PLAN.md            full schema + scoring rules + caveats
  BALL_BY_BALL.md    deep-dive on the ResultsVault BBB scrape
  _app_lib.py        shared chrome / CSS / DB helpers for the two
                     `app/` builders below
  build_data_repo.py generates the `app/index.html` data-repository page
                     (10-year game list with BBB + coverage % columns)
  build_metadata.py  generates the `app/metadata/...` opposition browse
                     / submit pages
  data/
    README.md
    rainham.db       SQLite DB (committed)
    raw/
      matches/<site_id>/<season>.json   per-season fixture lists
      match_detail/<match_id>.json      full scorecards (shared)
      league_table/<division_id>.json   cached league tables (shared)
      rv_match/<match_id>.json          ResultsVault metadata + innings
                                        index (rv_match_id, result_ids)
      balls/<match_id>/<innings>.json   ball-by-ball stream from RV,
                                        untouched upstream payload
      nv_match/<match_id>.json          NV Play full scorecard, fetched
                                        only when RV returns []
    metadata/                        (committed)
      players/<player_id>.json       canonical, player-keyed
      videos/<match_id>.json         match-keyed video evidence
      submissions/<sub_id>.json      pending|approved|rejected log
  app/                               (generated, committed; sibling of `reports/`)
    index.html                       data-repository page (10-year game list)
    data/players.json                bundled per-player data (~3MB,
                                     fetched once by player.html)
    metadata/
      clubs.html                     browse opposition clubs
      club/<club_id>.html            per-club roster (~277 pages)
      player.html                    ONE template that reads ?id=<pid>
                                     and renders from data/players.json
    static/
      app.css                        shared stylesheet
      player.js                      player-page logic + submit form
  reports/
    index.html       generated overview of every report (committed)
    scouting/<YYYY-MM-DD>/<slug>/v<N>/scout.{md,html,png}
    ad-hoc/<name>.md                       (top scorers, streaks, h2h, …)
    league/<YYYY-MM-DD>/<slug>/...         (division standings, round-ups)
```

> **Always re-build the index whenever a new report lands.** Both
> `scout.py` and the ad-hoc generators (`top_run_scorers.py`,
> `streaks_and_fifties.py`, `oneill_vs_hothi.py`) call
> `build_index.build()` automatically after writing, so a normal report
> run keeps `reports/index.html` in sync. If you hand-author a report
> (e.g. drop a new file into `reports/league/...`), run
> `python3 build_index.py` before committing.

## Schema cheat sheet

The DB is **club-agnostic** — every batting / bowling row carries
`team_batting_club_id` / `team_bowling_club_id`, so any report can pivot
to any club just by changing the WHERE clause. There is no "is rainham"
column.

| Table | One row per | Key columns |
| --- | --- | --- |
| `clubs` | club seen in any match | `club_id` |
| `matches` | match | `match_id`, `home_club_id`, `away_club_id`, `home/away_team_id` |
| `match_players` | (match, side, player) | `match_id`, `team_side`, `club_id`, `player_id` |
| `innings` | (match, innings_seq) | `match_id`, `innings_seq`, `team_batting_club_id` |
| `batting` | (match, innings_seq, position) | + `team_batting_club_id` |
| `bowling` | (match, innings_seq, bowl_position) | + `team_bowling_club_id` |
| `fall_of_wickets` | (match, innings_seq, wicket) | |

> **Always use `innings_seq`, never `innings_number`.** The Play-Cricket
> API returns `innings_number = 1` for both sides of a limited-overs match
> in ~99% of fixtures. `innings_seq` is the array-position ordinal we
> assign at load time. See PLAN.md.

Common filters:

```sql
-- Rainham batting
WHERE team_batting_club_id = '5251'
-- Spartans batting
WHERE team_batting_club_id = '14366'
-- A specific player across all clubs they've played for
WHERE batsman_id = 20972
-- Played matches only (exclude future fixtures)
WHERE result <> ''   -- or join on a date <= today filter
-- Senior 1st-XI only
WHERE team_batting_id IN (SELECT team_id FROM ... WHERE team_name='1st XI' AND club_id='14366')
```

Innings-counting convention used throughout the reports:
- Excludes `how_out` IN (`did not bat`, `absent`).
- Not-outs = `not out` / `retired not out`.
- Duck = `runs = 0` AND `how_out` is one of the dismissal modes (NOT
  `not out`, `retired not out`, empty, `did not bat`, `absent`).

## Workflow for new questions

### Ad-hoc Rainham question
1. Open `stats/data/rainham.db` with `sqlite3` and answer.
2. If the question is interesting and likely to be re-asked, promote it
   into a new `stats/<name>.py` script + committed `<name>.md` report.

### "Top X" / streak / head-to-head report
Pattern: `top_run_scorers.py`, `streaks_and_fifties.py`, `oneill_vs_hothi.py`.
Read one of them as a template. Always:
- Filter by `team_batting_club_id` (or `team_bowling_club_id`).
- Use `innings_seq`, not `innings_number`.
- Excludes `did not bat` / `absent`.
- Pre-canonicalise player names from the most-recent non-empty
  `batsman_name` for each `batsman_id`.

### Scouting an opposition club

The output is dated, namespaced by club, **and versioned per day** so a
club can have multiple iterations on the same day (e.g. v1 first cut, v2
after the user asks for tweaks):
```
stats/reports/scouting/<YYYY-MM-DD>/<slug>/v1/scout.md
stats/reports/scouting/<YYYY-MM-DD>/<slug>/v1/scout.html  ← mobile-optimised
stats/reports/scouting/<YYYY-MM-DD>/<slug>/v1/scout.png   ← long single-image PNG
stats/reports/scouting/<YYYY-MM-DD>/<slug>/v2/...
stats/reports/scouting/<YYYY-MM-DD>/<slug>/latest  → symlink to highest v
stats/reports/index.html                           ← refreshed automatically
```

`scout.py` auto-increments the `vN` folder each run; pass `--version N`
to overwrite a specific version, or `--no-png` to skip the PNG render.

The PNG is what the user shares — it's already a single mobile-friendly
"scrolling screenshot" rendered straight from the HTML, so just attach it
to WhatsApp.

The user will name a club ("Spartans", "Hornchurch", etc.). Steps:

1. **Find the club_id** in the existing DB:
   ```sql
   SELECT club_id, club_name FROM clubs WHERE club_name LIKE '%Spartan%';
   ```
   If the club is not yet in `clubs`, you almost certainly haven't
   fetched their site yet.

2. **Confirm site_id**. Most clubs use `site_id == club_id` on
   Play-Cricket, but verify with one curl call:
   ```bash
   curl -s "https://play-cricket.com/api/v2/matches.json?site_id=<id>&api_token=$TOKEN&season=2026" \
     | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('matches',[])))"
   ```

3. **Fetch their data + league tables**:
   ```bash
   python3 stats/fetch.py --site-id <id> --first-season 2018 --workers 12
   python3 stats/build_db.py
   # find division_ids the 1st XI played in
   sqlite3 stats/data/rainham.db "
     SELECT DISTINCT season, league_name, competition_id, competition_name
     FROM matches
     WHERE (home_team_id IN (<1st XI team_ids>) OR away_team_id IN (...))
       AND competition_type='League'
       AND season BETWEEN 2023 AND 2026
     ORDER BY season"
   # cache the league tables
   python3 stats/fetch.py --site-id <id> --first-season 2026 --last-season 2026 \
     --division-ids <id1>,<id2>,<id3>,<id4>
   ```
   Expect ~30 KB of JSON per match. The fetch is idempotent — only new
   matches will hit the API.

4. **Generate the scout report**:
   ```bash
   python3 stats/scout.py --club-name "Spartans" --season 2026
   ```
   Sections (1st XI only, League + Cup throughout unless noted):
   - **0. Current league table** — position, P, W, Pts + last-5 form pills
     (their row highlighted). Falls back to last completed season if the
     current is empty.
   - **1. Last 3 seasons finishing positions** — League games only
     (so P aligns with the published league table). Includes league
     name + division, finishing pos and W/L/D/NR.
   - **2/3. Top run scorers / wicket takers** across the last 3 seasons,
     1st XI L+C.
   - **4. Head-to-head vs Rainham 1st XI** in cache, with upcoming
     fixtures listed.
   - **5. Last 20 1st XI played matches** with innings scores and
     a Play-Cricket match link on each date. (This is the list we loop
     through when hunting for match videos — see step 5 below.)
   - **6. Patterns** — bar charts comparing them vs Rainham 1st XI:
     bat 1st vs 2nd, home vs away, when winning the toss; plus a
     per-season batting / bowling avg comparison.
   - **7. Web / video links** — `scout.py` only renders **verified**
     match videos here (from `CLUB_LINKS[club_id]["verified_videos"]`).
     **The script does NOT auto-generate YouTube search-probe links** —
     the Play-Cricket API has no `video_url` field, and clickable
     search-result pages do nothing in a screenshot. Surfacing playable
     videos is an **AI step you do by hand** before re-running scout.py
     (see "Hunt for match videos" immediately below). Until you've
     verified anything, the section says so explicitly.

5. **Hunt for match videos** — this part of the report cannot be
   generated by Python; it requires you (Claude) to fetch search
   results, read titles/dates, and decide whether each candidate is
   actually the match. Loop the 20 played fixtures from section 5 and
   for each one:

   1. **Run the YouTube search via WebFetch / WebSearch.** Build the
      query `"<club>" "<opponent>" cricket <year>`; the YouTube URL is
      `https://www.youtube.com/results?search_query=<urlencoded>`.
      You can also `curl` it directly if you want to grep the raw HTML —
      titles, channel names, durations and upload dates all live in
      the embedded `ytInitialData` JSON in the page.
   2. **Pull the top 2-3 results.** Don't trust a single title — the
      same fixture can show up under several uploads (full stream,
      highlights cut, post-match interview).
   3. **Decide whether it's the match.** A real hit looks like:
      - **Duration 2-7 hours** (full innings, or whole-day livestream).
        Highlights packages can be 5-15 min and are also acceptable
        *if* the title cites the same fixture.
      - **Upload date within ~2 days of `match_date`** — usually the
        same day or the next morning.
      - **Title / description** names both teams, or the ground, or
        the league/division.
      - **Channel** is one of the two clubs (or a known feeder
        channel like NV Play / Spencer Cricket / a regional league
        channel).
      Reject: unrelated highlights compilations, T20-night reels,
      videos from a different `match_date`, anything < 30 min unless
      it's clearly a highlights upload tied to **this** fixture.
   4. **For every confirmed hit**, paste the URL into
      `CLUB_LINKS["<club_id>"]["verified_videos"]` in `scout.py` with
      a short label that includes the date (e.g.
      `"Wickford v Brentwood (League, 02/05/2026)"`).
   5. **Re-run scout.py.** That bumps the version (v1 → v2 …) and
      bakes the verified links into both `scout.html` and `scout.png`.
   6. If a fixture has no plausible video, just move on — silence is
      better than a speculative link in the report.

   Do not paste search-results URLs into the report. They expire from
   the user's perspective the moment they look at the screenshot, and
   the user explicitly does not want them. Either you've found and
   verified the playable video → it goes in `verified_videos`, or you
   haven't → leave it out. The report's "Verified match videos" section
   will say "no videos verified yet" and that's fine.

6. Commit the new raw JSON + the rebuilt DB + the report.

### Mobile-friendly HTML / PNG for sharing

`scout.py` writes three files per run:

- `scout.md` — markdown for skim-reading in the terminal.
- `scout.html` — single self-contained file (inline CSS, tiny inline JS
  used only to record render height for the PNG step). Designed for a
  ~540 px viewport.
- `scout.png` — a long single-image render of the HTML (rendered via
  headless Chromium at exactly the document's `scrollHeight`). This is
  the artefact the user actually shares.

The PNG renderer looks for Chromium in this order: `$CHROMIUM_BINARY`,
`/opt/pw-browsers/chromium`, then `chromium` / `chromium-browser` /
`google-chrome` on `PATH`. If none are present, pass `--no-png` and the
md / html are still emitted.

If you need to tweak the look (fonts, table density, colour pills, hero
tiles), edit the `CSS` constant and `render_html()` in `scout.py`.

### Adding a video link manually

When you've eyeballed a YouTube link / NV Play stream and confirmed
it's the right match (see "Hunt for match videos" above for the
verification heuristic), paste it into the `CLUB_LINKS` table in
`scout.py` under that club's `verified_videos` entry, then re-run —
this generates the next `vN`. The Play-Cricket API does **not** expose
per-match video URLs, so this is a manual, human-curated list.

### Comparing players across clubs
Use `oneill_vs_hothi.py` as the template. The bow/bat queries already
take `team_batting_club_id` / `team_bowling_club_id`, so you can compare
any two players regardless of which clubs they play for.

### The reports index

Every report under `reports/` is surfaced from a single
`reports/index.html`, grouped by category (`scouting`, `ad-hoc`,
`league`, …). It's regenerated by `build_index.py`, which is called
automatically at the end of `scout.py`, `top_run_scorers.py`,
`streaks_and_fifties.py`, and `oneill_vs_hothi.py` — so a normal report
run keeps the index in sync.

If you write a report by hand (e.g. drop a markdown file straight into
`reports/league/...`, or hand-author an ad-hoc note), **always run
`python3 build_index.py` before committing** so the index lists it.

To add a new category, just create the folder under `reports/` (e.g.
`reports/season-review/`). `build_index.py` discovers categories from
the directory listing, and any folder with a `<name>.md` / `<slug>/...`
inside will be picked up. Add the category's friendly label and blurb
to `CATEGORY_LABELS` / `CATEGORY_BLURBS` in `build_index.py` if you
want it to look polished.

## Cache refresh rhythm

- Run `python3 stats/fetch.py --only-recent 1 --force-seasons` at the
  start of any session that asks for a "current season" or "latest"
  question. This re-fetches just the most recent season summary, then
  pulls any new match details that have appeared.
- Run `python3 stats/build_db.py` after any fetch.
- Don't refetch the entire history — the cache is exhaustive.

## Validation discipline

Before quoting any headline number from a v1 query, sanity-check it
against the equivalent on `rainhamcc.play-cricket.com` (or the opposition
club's Play-Cricket page). The first build of this DB came in at ~50% of
the correct totals because of the `innings_number` bug. One reference
comparison would have caught it. Don't skip that step.

## Don't

- Don't add the API token to a new file unless you really need to. It's
  in `php/globals.php` as `pcapikey` and `fetch.py` reads it via
  `PC_API_TOKEN` env var (with `globals.php` fallback).
- Don't bypass the cache. If a stat needs a new field, **extend
  `build_db.py`** to populate it from the raw JSON, then rebuild — don't
  call the API from a report.
- Don't reintroduce `is_rainham_player` / `rainham_team_side`. The DB is
  club-agnostic.
- Don't use string-style season comparisons. `match_date` is `dd/mm/yyyy`;
  convert to `yyyymmdd` for ordering / filtering.
- Don't trust `result` alone — also check `result_applied_to` to know
  whose perspective the W/L is from. See `result_for(...)` in `scout.py`.

## Ball-by-ball player attribution

Both backends (RV and NV Play) emit per-ball commentary text along
with numeric IDs:

  * **RV** → `l_desc = " <bowler> to <batter>: <outcome>"` plus
    numeric `batter_id` / `bowler_id` in **RV's own player-id
    namespace** (11M-range ints, distinct from PC's 4M-range).
  * **NV Play** → `C = "<bowler> to <striker>, <outcome>"` (only
    when the request includes `&commentary=true`); no numeric ids
    on the ball, but `Match.Team{1,2}Players[].ExternalId` is
    already the PC `player_id`.

Per-ball IDs land in the `balls` table as **PC `player_id`s** via:

1. **First pass — roster match.**
   * RV: `_rv_balls.build_rv_to_pc_map()` matches each RV
     `TeamMember` against PC `match_players` by name (full /
     "Last, First" / "F Last" / last-only fallbacks). Returns NULL
     for ambiguous names.
   * NV: `_nvplay_balls.build_team_index()` indexes
     `Match.Team{1,2}Players[]` by surname and `(initial, surname)`;
     each ball's bowler/striker text is resolved against the
     bowling-team / batting-team index. NULL on ambiguity.

2. **Second pass — scorecard disambiguation** (`_disambig.py`).
   For any ball that ended up with `batter_id IS NULL` or
   `bowler_id IS NULL`:
   1. Re-parse `l_desc` to extract the unresolved name text.
   2. Group balls in this innings by that name text.
   3. Aggregate BBB statistics for the group (runs / balls /
      wickets / overs).
   4. Look up scorecard rows for every PC candidate that the name
      could refer to (in `batting` / `bowling` for this
      `match_id, innings_seq`).
   5. Score each candidate against the BBB aggregate (absolute
      deviations, with wickets weighted 3×).
   6. Tie-breakers for bowlers (Jono's heuristics):
      * Higher position in `bowling.bowl_position` ⇒ bowled the
        earlier overs (compares `bowl_position` to the BBB group's
        average `over_no`).
      * Spells alternate ends — bowlers whose BBB overs are mostly
        odd-indexed *or* mostly even-indexed get a small bonus,
        bowlers with 50/50 mixes get nothing.
   7. Require a strict score margin (≥ 0.5) between best and
      runner-up — leaves NULL otherwise. We never guess.

   The pass runs once per innings, after the loaders have inserted
   the balls. It only ever **assigns** NULLs; never overwrites a
   non-NULL id.

Validation (BBB-aggregate runs vs `batting` / `bowling` totals over
all 445 BBB-loaded matches):

  RV    batter exact 95.7%  ≤5  98.8%   bowler exact 94.0%
  NV    batter exact 92.7%  ≤5  99.1%   bowler exact 88.0%
  Attribution    RV 90.3% bat / 89.4% bowl
                 NV 95.7% bat / 94.7% bowl

The pass added ~7 percentage points of NV attribution at the cost of
~14 wrongly-assigned batter-innings (out of +81 newly-attributed) —
net positive both for accuracy and for downstream coverage %, since
wrong attributions don't appear in committed metadata files and
therefore don't inflate coverage.

If you tighten the disambiguation further, do it via `_disambig.py`
— don't push that logic into the loaders.

## Roadmap: opposition metadata + BBB coverage

Goal: capture batting / bowling profiles for opposition players (so we
can slice Rainham performance by RHB vs LHB, pace vs spin, left-arm vs
right-arm, etc.) and expose ball-by-ball coverage on a public page.

**No web backend.** Everything is committed JSON / SQLite. Submissions
are `mailto:` / WhatsApp links built in pure JS — the static site never
POSTs anywhere. The user (Jono) receives the message, pastes it into
Claude Code (Android), and Claude writes the JSON.

### Build order (status: 1–4 ✅, 5 in progress)

1. **Balls into the DB** ✅ — `build_db.py` now loads
   `data/raw/balls/<match_id>/<innings_order>.json` into a `balls`
   table + `match_bbb` lookup, with per-innings `SUM(runs)` validation.
2. **Bulk-fetch BBB** ✅ — `fetch_balls.py --site-id 5251 --season N`
   over 2017–2026 cached ~95k balls from ~376 matches (~29 % of
   played fixtures; 2022 onwards averages ~17 %, 2017–2019 ~1 %).
3. **Static `app/index.html`** ✅ — `build_data_repo.py` writes the
   10-year game list with BBB + coverage columns. Coverage starts at
   0 % everywhere; that's expected.
4. **Metadata model + pages** ✅ — `build_metadata.py` writes
   `clubs.html`, the 277 `club/<id>.html` pages, the `player.html`
   template + bundled `data/players.json`, and the mailto / WhatsApp
   form (destinations baked from env vars at build time).
5. **Bootstrap metadata** — Jono works through the queue manually via
   Claude Code: Claude writes `data/metadata/players/<id>.json` for
   players Jono already knows, then re-runs `build_metadata.py`.

Each step is independently committable; nothing in step N blocks N+1
from being designed.

### 1. Balls in SQLite

Add to `build_db.py`:

```sql
CREATE TABLE balls (
  match_id              INTEGER,
  innings_seq           INTEGER,    -- our existing innings_seq, NOT innings_number
  ball_no               INTEGER,    -- raw sequence (counts NB/wides)
  ball_no_disp          INTEGER,    -- legal-balls-only display number
  over_no               INTEGER,
  batter_id             INTEGER,
  non_striker_id        INTEGER,
  bowler_id             INTEGER,
  team_batting_club_id  TEXT,
  team_bowling_club_id  TEXT,
  runs_bat              INTEGER,
  runs_extra            INTEGER,
  extras_type           INTEGER,    -- 1=NB 2=Wide 3=B 4=LB 5=NB+B 6=NB+LB
  is_legal_ball         INTEGER,    -- 0 if extras_type IN (1,2)
  dismissed_batter_id   INTEGER,
  s_desc                TEXT,
  l_desc                TEXT,
  PRIMARY KEY (match_id, innings_seq, ball_no)
);
CREATE INDEX idx_balls_bowler ON balls(bowler_id, match_id);
CREATE INDEX idx_balls_batter ON balls(batter_id, match_id);
CREATE INDEX idx_balls_match  ON balls(match_id, innings_seq, over_no);
```

Loader rules:
- Walk `data/raw/balls/<match_id>/<innings_order>.json`. The file's
  `innings_order` IS our `innings_seq` (1-based, in playing order).
- Resolve `team_batting_club_id` / `team_bowling_club_id` from
  `data/raw/rv_match/<match_id>.json` (`innings[].is_home`) cross-
  referenced with `matches.home_club_id` / `away_club_id`.
- `is_legal_ball = 0 if extras_type IN (1, 2) else 1`.
- Validation: `SUM(runs_bat + runs_extra)` per innings should equal
  `innings.runs - innings.penalty_runs`. Log the worst delta on load,
  abort if any delta > 5%.

### 2. Bulk-fetch BBB

```bash
for s in 2017 2018 2019 2020 2021 2022 2023 2024 2025 2026; do
  python3 fetch_balls.py --site-id 5251 --season "$s" --workers 6
done
python3 build_db.py
```

Older seasons trail off — paper-scoring was more common pre-2021. The
data-repo page surfaces this honestly (BBB column = ✓ / ✗).

### 3. Player metadata model (player-keyed)

`player_id` is stable across clubs; the metadata follows the player.
A player who has played for 3 clubs has **one** record, not three.

```
data/metadata/players/<player_id>.json
{
  "player_id":    11189264,
  "display_name": "M Barber",
  "aliases":      ["M Barber", "Matthew Barber"],   // history of names seen
  "seen_clubs":   [{"club_id": "113390", "club_name": "Upminster CC"}],
  "metadata": {
    "batting_hand":  "right" | "left" | "unknown",
    "bowling_type":  "pace" | "spin" | "none" | "unknown",
    "pace_type":     "fast" | "medium" | "slow" | "unknown" | null,
    "spin_type":     "wrist" | "finger" | "unknown" | null,
    "bowling_arm":   "right" | "left" | "unknown" | null,
    "angle_to_rhb":  "over" | "round" | "varies" | "unknown" | null,
    "notes":         ""
  },
  "approved_at":   "2026-05-04T12:34:56Z",
  "approved_from": "sub-uuid",
  "approved_by":   "jono"
}
```

`aliases` and `seen_clubs` are derived (rebuilt by `build_app.py` from
`match_players`); never hand-edit them.

**Status rules**:
- **complete** — all required fields populated (literal `"unknown"`
  counts as populated). Required = `batting_hand`, `bowling_type`,
  plus `bowling_arm` + `angle_to_rhb` + (`pace_type` if pace, else
  `spin_type` if spin) when `bowling_type ∈ {pace, spin}`.
- **partial** — any required field missing.
- **not captured** — no file exists.
- **needs review** — at least one submission with `status = "pending"`.
- **conflicting** — ≥ 2 pending submissions disagree on any field.

### 4. Video evidence (match-keyed)

```
data/metadata/videos/<match_id>.json
{
  "match_id": 7674154,
  "match_date": "03/05/2026",
  "home_club_name": "Upminster CC",
  "away_club_name": "Rainham CC, Essex",
  "videos": [
    {"url": "...", "label": "Full innings, 1st XI v Upminster",
     "verified_at": "2026-05-04T..."}
  ]
}
```

Migrate `CLUB_LINKS[*]["verified_videos"]` in `scout.py` into these
files (one entry per match), so videos are facts about a game and
reusable across reports / metadata pages. Surface them on a player page
by joining `match_players` → `videos`.

### 5. Submission flow (mailto / WhatsApp)

The page `app/metadata/player/<player_id>.html`:
- Shows current metadata + every match the player appears in that has
  video evidence.
- Has a form with all metadata fields + free-text notes + a multi-pick
  for `evidence_match_ids`.
- Submit is **plain JS** — no `fetch`, no backend. The button builds a
  structured plain-text body and opens either:
    - `mailto:<jono>?subject=Player+metadata...&body=<urlencoded>`
    - `https://wa.me/<number>?text=<urlencoded>`

Body format (stable, easy for Claude to parse):

```
PLAYER METADATA SUBMISSION
player_id: 11189264
player_name: M Barber
batting_hand: right
bowling_type: pace
pace_type: medium
spin_type:
bowling_arm: right
angle_to_rhb: over
evidence_match_ids: 7674154, 7677034
notes: Saw him in the U13 game, RHB, RA medium-fast
submitted_by: <name>
```

Jono pastes this into Claude Code on Android: "process this submission".
Claude writes:

```
data/metadata/submissions/<uuid>.json
{
  "id": "sub-...",
  "submitted_at": "2026-05-04T...",
  "submitted_by": "<from message>",
  "via": "mailto" | "whatsapp" | "claude-code",
  "player_id": 11189264,
  "metadata": { ...same shape as player file... },
  "evidence_match_ids": [7674154, 7677034],
  "notes": "...",
  "status": "pending",
  "reviewed_at": null,
  "reviewer_note": null
}
```

Why this works without infra: every submitter has email or WhatsApp.
The static site only needs to *generate* a link; sending and receiving
go through standard apps. Jono is the single trust boundary.

### 6. Approval flow (manual, Claude-Code-driven)

No static admin UI. The flow is:

1. Jono lists pending: "show pending submissions". Claude reads
   `data/metadata/submissions/*.json` where `status = "pending"`.
2. Jono says: `approve sub-xyz` (or `approve all from <person>`).
   Claude merges `submission.metadata` into the canonical
   `data/metadata/players/<player_id>.json` (creating it if missing),
   sets `status = "approved"`, fills `approved_at` / `approved_from`,
   and commits.
3. Jono says: `reject sub-xyz with note "wrong player"`. Claude flips
   `status = "rejected"`, writes the reviewer note, and commits.
4. After every batch, run `python3 build_app.py` so the static pages
   reflect the new state.

### 7. Static frontend (`app/`)

Pure HTML + vanilla JS. No build step, no Node, no React. Sibling of
`reports/`, **not** a child. **Two builders** + a shared library — they
share the DB connection, the CSS, the page chrome, and the metadata
status function, but otherwise have nothing in common, so splitting
them keeps each script focused:

```bash
# Always rebuild the DB first (loader picks up fresh raw/balls/ data)
python3 build_db.py

# Page 1 — data repository
python3 build_data_repo.py

# Pages 2..N — metadata browse + submit
RCC_SUBMIT_EMAIL=jono@example.com \
RCC_SUBMIT_WHATSAPP=447700900123 \
  python3 build_metadata.py
```

`_app_lib.py` is shared: CSS, page wrapper + hero, `metadata_status()`,
the SQLite helper, and the **`first_xi_fixture_where()` /
`first_xi_match_ids()` helpers** that encode the universe filter (see
below). **Don't** inline app-specific logic into it; keep it "chrome +
shared filters only".

#### Visual design (mobile-first)

The `app/` styling is a deliberate sibling of the `reports/` look —
same gradient hero, white cards on a muted backdrop, tabular numerics,
W/L pills using the same colours. CSS lives in `_app_lib.CSS` and is
written once to `app/static/app.css`; pages link to it.

- **Mobile-first**, `max-width: 540px`, single column. The page chrome
  is `header.hero` (gradient, breadcrumbs, h1, lead, stats chips)
  followed by `<main>` containing `.card` blocks. Don't add a sticky
  top-bar — the hero IS the header on every page.
- **Status pills** use `.tag.complete / .partial / .notcap / .review /
  .conflict`; result pills use `.pill.W / .L / .D / .NR`.
- **Lists are cards, not tables** on the data-repo + metadata pages
  (there's a `.fix-list` / `.row-list` pattern). Tables are reserved
  for dense numeric grids and are wrapped in `.table-wrap` for
  horizontal scrolling.

#### Universe filter

**Only these fixtures count** for *both* builders:

  * Rainham 1st XI on either side (`team_id = '51207'`)
  * League games (always)  OR  Cup games whose `competition_name`
    does NOT match T20 / Twenty20 / 20-20 / 20/20 / "Smash" patterns
  * Played, in the last 10 seasons (today's date is the cutoff)

Encoded once in `_app_lib.first_xi_fixture_where()`:

```python
sql_filter, params = L.first_xi_fixture_where(alias="m")
sql = f"SELECT ... FROM matches m WHERE {sql_filter} AND m.season >= ?"
```

**Excluded everywhere**: 2nd / 3rd / 4th / Sunday / U13 / U15 / Indoor
fixtures, Friendlies, T20 / 20-over Cup competitions. The metadata
pages only surface clubs / players that appear on a `match_players`
row for one of these matches — so a 2nd-XI-only opposition won't show
up at all.

#### Build outputs

Every page starts with a hero summary (see "Visual design" above).
Hero stat chips on each screen:
- `index.html`:        fixtures · with-BBB count · BBB coverage %
- `clubs.html`:        fixtures · clubs · players · complete count
- `club/<id>.html`:    players · complete · partial · missing
- `player.html`:       (no stats — the hero is one row of breadcrumbs +
                        the player's name + status pill)

`app/index.html` — **data repository / 10-year game list**.
- One card per played 1st-XI fixture (filter as above).
- Each card: date · season · opposition · competition · format chip ·
  W/L pill · BBB ✓/✗ · `opp bat` cov bar · `opp bowl` cov bar.
- Cards with no BBB hide the coverage rows entirely (don't show
  em-dashes — they add noise).
- Coverage % uses the SQL in §coverage formulas below.

`app/metadata/clubs.html` — every relevant opposition club, sorted by
squad size. Each row shows the rollup chips
(`N✓ / N partial / N missing [/ N pending]`) and total player count.

`app/metadata/club/<club_id>.html` — per-club roster (~43 small
pages, one per relevant club). Each player is a row-link with their
status pill, `N apps vs us · last seen <date>`, and a 🎬 chip if any
of their matches in our cache has video evidence.

`app/metadata/player.html` + `app/data/players.json` —
- ONE static template (~700 bytes) reads `?id=<player_id>` from the
  URL, fetches the bundled `app/data/players.json` (~200 KB after the
  filter, cached by the browser after the first hit), and renders the
  player record + the mailto / WhatsApp submit form.
- Why bundled instead of per-player files? Even 800-odd tiny files
  burn ~3 MB in 4 KB filesystem blocks and are a chore to navigate in
  git. The bundle gzips to ~50 KB over HTTP.

`app/static/app.css` and `app/static/player.js` — written once by the
builders. The submit form's destinations (`SUBMIT_EMAIL`,
`SUBMIT_WHATSAPP`) are baked into `player.js` from the env vars at
build time, **not** stored in committed source.

#### Coverage formulas

```sql
-- batting coverage for the OPPOSITION on a single Rainham match
SELECT
  1.0 * SUM(CASE WHEN batter_id IN <covered_player_ids> THEN 1 ELSE 0 END)
      / COUNT(*) AS bat_cov
FROM balls
WHERE match_id = :mid
  AND team_batting_club_id <> '5251'
  AND is_legal_ball = 1;

-- bowling coverage: same WHERE, swap batter_id -> bowler_id and
-- team_batting_club_id -> team_bowling_club_id.
```

`<covered_player_ids>` = the set of `player_id`s whose
`data/metadata/players/<id>.json` resolves to status `complete`.

#### GitHub Pages deploy

`.github/workflows/static.yml` deploys the whole repo to Pages on
**push to `main` only** (no PR / branch builds). To preview locally,
just `python3 -m http.server` from the repo root and browse
`http://localhost:8000/app/`. Branch pushes don't redeploy — merge to
`main` when you want to ship.

### Don'ts (metadata-specific)

- **Don't** key player metadata by club. Same `player_id` ⇒ one record,
  no matter how many clubs they've played for. The "browse by club"
  view is just an axis on top of the same player records.
- **Don't** key video evidence by player. It's a fact about a *match*;
  surface per-player by joining through `match_players`.
- **Don't** add a backend, even a "small one". The mailto / WhatsApp
  trick is the architecture, not a workaround.
- **Don't** auto-merge submissions. Approval is always Jono via Claude
  Code.
- **Don't** put any of the `app/` content under `reports/`. Different
  category, different generators (`build_data_repo.py` /
  `build_metadata.py`, not `build_index.py`).
- **Don't** widen the universe filter without checking with Jono first.
  The point is to keep this focused on opposition we'll actually
  scout. Excluded fixtures (2nd XI, friendlies, T20 cups, etc.) stay
  excluded.
- **Don't** desktop-ify the layout. `max-width: 540px` is deliberate —
  the user reads everything from his phone.
