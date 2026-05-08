# Amateur cricket modelling — top-of-pyramid league scrape

The goal is to build a national dataset of **1st XI Premier-League cricket
in England** that's deep enough to model:

- player-level performance across leagues (RHB / LHB splits, pace vs
  spin, bowler-type matchups);
- match outcome / total prediction at top-tier amateur level;
- coverage gaps — where ball-by-ball + video exist vs where they don't.

The Rainham-CC stack already does this for **one** club + opposition
within a single league (Essex). This plan extends it to **33 ECB
Premier Leagues** at the top of the recreational pyramid.

The dataset is intentionally **public-evidence-only**: Play-Cricket API
JSON, RV/NV ball-by-ball (captured via the same endpoints we already
use), YouTube/Frogbox livestreams, and league websites. No private
sources, no scraping anything paywalled.

## Target dataset

For each of the 33 leagues:

- Top **5 divisions** of the men's Saturday 1st-XI pyramid
  (Premier + Div 1..4, or local equivalent — see "Division selection"
  below).
- Last **10 seasons** (2017 – 2026 with today's date).
- That's **50 division-seasons** per league × **~90 matches** per
  division-season ≈ **4,500 matches** per league.
- × **33 leagues** ≈ **~148k matches**.

For every match we want:

| Field | Source | Notes |
| --- | --- | --- |
| Match summary (teams, ground, date, comp) | `matches.json` | already cached pattern |
| Full scorecard | `match_detail.json` | already cached pattern |
| Ball-by-ball (RV) | `_rv_balls.py` flow | scored via Play-Cricket Scorer |
| Ball-by-ball (NV Play) | `_nvplay_balls.py` flow | livestreamed games |
| Live-stream URL | manual + heuristic | YouTube / Frogbox / NV Play |
| BBB-coverage flag | derived | RV / NV / none / partial |

## Why these 33 leagues

These are the league sites the user supplied as the ECB Premier-League
tier — each is the top of its county or regional pyramid. Site IDs
were resolved by scraping each `*.play-cricket.com/home` page for the
embedded `site_id:` blob, then sanity-checked by hitting
`/api/v2/matches.json?site_id=<id>&season=2025`.

| # | League slug | Site ID | 2025 matches | Notes |
|---|---|---|---|---|
| 1 | bdpcl | 252 | 293 | Birmingham &amp; District |
| 2 | bradfordcl | 259 | 1,148 | |
| 3 | cheshirecountycl | 7246 | 2,010 | |
| 4 | ccl | 285 | 1,605 | Cornwall (?) — verify |
| 5 | derbyscountylge | 296 | 2,722 | Derbyshire CC League |
| 6 | devoncl | 298 | 1,498 | |
| 7 | dorsetcl | 302 | 816 | |
| 8 | eapcl | 305 | 147 | East Anglian Premier — small, 1st XI only? |
| 9 | essexcl | 7300 | 1,860 | **test target** |
| 10 | gtrmcrcricket | 11685 | 4,453 | Greater Manchester |
| 11 | hertspremiercl | 572 | 2,563 | |
| 12 | hcpcl | 342 | 198 | Home Counties Premier — small |
| 13 | huddersfieldcl | 346 | 1,080 | |
| 14 | kcl | 362 | 2,526 | Kent |
| 15 | lancashireleague | 10954 | 867 | |
| 16 | leicestershirescl | 370 | 2,167 | |
| 17 | lincspremiercl | **30316** | 0 in 2025 | only has 2026 data — older seasons live on a different site_id; **needs investigation** |
| 18 | ldcc | 378 | 1,566 | Liverpool &amp; District |
| 19 | middlesexccl | 393 | 2,092 | |
| 20 | nepremierleague | 409 | 1,061 | North East |
| 21 | nssc | 447 | 1,428 | |
| 22 | nwcl | 4655 | 652 | North Wales (cross-border, still ECB-affiliated) |
| 23 | nysdl | 441 | 2,172 | North Yorkshire / South Durham |
| 24 | ncl | 426 | 1,923 | Northern Cricket League |
| 25 | npcl | **7301** | 0 in 2025/26, 195 in 2024 | likely **rebranded onto new site**; **needs investigation** |
| 26 | nottinghamshirecbpl | 443 | 288 | small — 1st XI only? |
| 27 | swpcl | 10196 | 198 | South West Premier — small |
| 28 | spcl | 496 | 429 | Southern Premier |
| 29 | surreycricketchampionship | 29012 | 3,488 | |
| 30 | sussexcricketleague | 16378 | 3,164 | |
| 31 | westofengland | 545 | 726 | |
| 32 | ycspl | 22888 | 1,536 | Yorkshire South Premier |
| 33 | ypln | 8240 | 3,298 | Yorkshire Premier League North |

Two flagged sites (`lincspremiercl`, `npcl`) need a follow-up scrape —
likely a different `site_id` carries the historical matches, or the
league re-platformed mid-decade. Don't block the wider scrape on
these; come back to them once the rest is loaded.

## Division selection

Leagues vary in shape. Some run a single 1st-XI ladder + 2nd/3rd/4th
parallel ladders ("Essex shape" — 5 divisions of 1st XI cricket).
Others run a single 1st-XI division + reserve sides ("EAPL shape").

For each league, pick the top 5 men's Saturday 1st-XI divisions by
discovering competitions from the API:

1. Pull `matches.json` for one recent season (say 2025).
2. Bucket by `(competition_id, competition_name, competition_type)`.
3. Filter to `competition_type = "League"` AND name matches the
   1st-XI / Premier / Senior pattern (excluding `2nd XI`, `3rd XI`,
   `4th XI`, `Sunday`, `T20`, `Twenty20`, `20-over`, `Junior`,
   `U13/U15/U17`, `Women`, `Indoor`).
4. Rank by tier — Premier first, then Div 1 → Div 4. Names are
   human; use a small ordering heuristic with a manual override file
   for awkward leagues.
5. Take the top 5. Record `competition_id`s per season — they change
   every year.

Output of this stage: a CSV / JSON keyed by `(league, season,
tier)` → `competition_id`. Commit it; treat it as the source of
truth for downstream filters.

## BBB coverage classification

For every match in the universe, classify into one of:

- **rv** — `data/raw/balls/<match_id>/<innings>.json` is non-empty
  for at least one innings. PCS-scored.
- **nv** — `data/raw/nv_match/<match_id>.json` exists with
  per-ball commentary. Livestream-scored.
- **none** — neither backend has data for this fixture.
- **partial** — only some innings have BBB (rare; treat as `none`
  for headline coverage but log separately).

We already have `fetch_balls.py` which probes RV first then falls back
to NV. The classification is a thin wrapper that:

1. For each match in the universe, calls `fetch_balls` (if not
   cached).
2. Records `(rv_ok, nv_ok)` per innings into a `match_bbb` table.
3. Aggregates to `coverage(league, season, division)` for the
   league-coverage report.

Expected coverage shape (extrapolating from Essex):
- 2017 – 2020 → very low (paper scoring, occasional PCS).
- 2021 – 2023 → patchy, growing.
- 2024 – 2026 → high in Premier, dropping by division tier.

## Video coverage (Frogbox / YouTube)

Play-Cricket has **no `video_url` field** on a match. Three signals
worth using, in priority order:

1. **NV Play `Match.LiveStreamUrl` / Frogbox embed** — when a match
   was scored via NV Play with the livestream feature, the NV match
   payload often carries the YouTube URL. Free.
2. **YouTube search by `(home, away, date)`** — same heuristic as
   the existing scout-video hunt: 2-7 hour duration, upload date
   within ±2 days, channel matches one of the clubs / Frogbox /
   NV Play / a regional uploader. **Manual confirmation required**
   per the existing CLAUDE.md rules — no speculative search-result
   links in the dataset.
3. **Frogbox channel scraping** —
   `https://www.youtube.com/@FrogBoxLive/videos`. Bulk-pull
   uploads, parse titles / dates, then fuzzy-match against our
   matches universe. Less manual but title formats vary.

For the v1 build I'll only commit **(1)** automatically (it's a
fact embedded in the data we already fetch) and stub the (2) / (3)
flow for later.

Schema:

```
data/metadata/videos/<match_id>.json    (one entry per match)
{
  "match_id": ...,
  "match_date": "dd/mm/yyyy",
  "videos": [
    {"url": "...", "label": "...", "source": "nv-livestream" | "manual" | "frogbox-bulk",
     "verified_at": "iso-ts"}
  ]
}
```

Reuses the schema already roadmap'd in `CLAUDE.md` §4 for the metadata
project — don't introduce a parallel format.

## Storage shape

The Rainham repo's `data/raw/` layout is per-site for the matches
summaries (`matches/<site_id>/<season>.json`) and shared otherwise
(`match_detail/<match_id>.json`, `balls/<match_id>/...`). That's
already correct for multi-league: `match_id` is globally unique
across Play-Cricket, so we just keep adding under the same
`match_detail/`, `balls/`, `nv_match/` directories.

New per-league bookkeeping:

```
data/leagues/
  index.json                   33 leagues + site_ids + status
  divisions/<site_id>.json     {season: [{tier, comp_id, comp_name}]}
  coverage/<site_id>.json      {match_id: {bbb: rv|nv|none, video: bool}}
```

`coverage/<site_id>.json` is generated; never hand-edit. `index.json`
and the per-league override file (for leagues whose division names
don't fit the Essex regex) are hand-curated.

## DB shape

Keep the existing `rainham.db` schema; add three columns and one
table:

- `matches`: add `league_site_id INTEGER` so we can pivot by league
  without joining through `site_id` every time.
- `matches`: add `tier INTEGER` (1 = Premier, 2 = Div 1, …) and
  `is_universe INTEGER` (the universe flag is precomputed once).
- New table:

  ```sql
  CREATE TABLE leagues (
    site_id     INTEGER PRIMARY KEY,
    slug        TEXT,
    league_name TEXT,
    notes       TEXT
  );
  ```

The DB stays club-agnostic (per existing rules); league-agnostic logic
sits on top.

## Build order

1. ✅ **Resolve league site_ids + sanity-check API.** Done above.
2. **Test on Essex.** Fetch all 10 seasons, derive the top 5 1st-XI
   divisions, count matches, classify BBB coverage, count videos.
   This is the test described below.
3. **Generalise division-selection.** Codify the regex / overrides
   into `stats/leagues_universe.py`.
4. **Bulk-fetch matches summaries** for all 33 sites × 10 seasons.
   Idempotent. ~330 API calls.
5. **Bulk-fetch match details** for the universe set
   (~150k calls — needs throttling and a resume cursor).
6. **Bulk-fetch BBB** with `fetch_balls.py` for the universe set.
   Most matches will return empty; that's fine.
7. **Compute coverage** + write `data/leagues/coverage/*.json`.
8. **Build a national report** — coverage % by league/season/tier,
   like the existing `app/index.html` data-repo page, but pivoted
   national.

Each step is independently committable.

## Risks / open questions

- **API rate limit.** 150k match-detail calls is the biggest unknown.
  The existing fetch is single-IP via the shared API token — needs a
  conservative rate (e.g. 4 workers, ≥250ms per call), and the
  cron-style resume cursor that already exists in `fetch.py`. Worst
  case: throttle to overnight runs.
- **Old seasons may be lossy.** Some leagues only published full
  scorecards from ~2020 onwards. `match_detail` returning empty is
  expected; we record `null` rather than skipping.
- **Division-name regex coverage.** A few leagues will need manual
  overrides — flagged as we hit them.
- **NV Play auth.** `_nvplay_balls.py` already handles the auth
  trail; nothing new required, but watch for rate-limiting on a
  bigger fetch.
- **The two flagged sites** (`lincspremiercl`, `npcl`) need their
  historical site_ids tracked down before the bulk fetch.
- **Scope creep.** The user said "amateur-cricket-modelling", not
  "build a model" — this plan is the *data* layer. Modelling sits
  on top.

## Test: Essex (results in `reports/league/essex-survey/v1/`)

The Essex test is run **before** committing to the full 33-league
fetch. Goals:

1. Fetch 10 seasons (2017–2026) of Essex League summary data
   (cached at `data/raw/matches/7300/<season>.json`).
2. Derive the top 5 1st-XI divisions per season (target: Premier +
   Div 1..4 — already named exactly that in 2025).
3. Aggregate match counts: 5 divs × 10 seasons × ~90 matches ≈
   ~4,500 matches expected.
4. Sample BBB coverage: hit `fetch_balls` on a stratified sample
   (e.g. 5 matches × 10 seasons × top 2 divisions = 100) and
   measure RV/NV/none breakdown.
5. Probe video coverage: pull NV Play scorecards for any NV-scored
   matches in the sample and look for `LiveStreamUrl`.
6. Decide whether the full bulk-fetch is feasible.

Findings / numbers go into `reports/league/essex-survey/v1/survey.md`.
