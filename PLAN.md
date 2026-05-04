# Rainham CC Cricket Stats — PLAN

## Question
Build a queryable, source-controlled dataset of every Rainham CC match recorded
on Play-Cricket so we can answer questions like "top 10 run scorers across all
formats / teams" without re-scraping each time.

First concrete report: **top 10 run scorers across all formats/teams** for
Rainham CC.

## Source
Play-Cricket public API (reverse-engineered from `php/epizy/cron_api_matches_master.php`
and `php/cron_api_matches_listener.php`).

- Base URL: `https://play-cricket.com/api/v2/`
- Auth: `api_token` query param (held in `php/globals.php`)
- Site ID: `5251` (Rainham CC, Essex)

### Endpoints used

| Endpoint | Params | Purpose |
| --- | --- | --- |
| `matches.json` | `site_id`, `api_token`, `season` | Per-season list of fixtures (basic metadata, no scorecard). |
| `match_detail.json` | `match_id`, `api_token` | Full scorecard: innings, bat, bowl, fow, players. |
| `league_table.json` | `division_id`, `api_token` | League standings (not used for this report; here for completeness). |

Other endpoints exist (`registered_players.json`, `match_results.json`,
`results_summary.json`) but the two above are sufficient for the run-scorer
question.

### Documentation
Official: https://play-cricket.ecb.co.uk/hc/en-us/articles/360000141669-Match-Detail-API
(blocked from this sandbox — schema below was reverse-engineered from sample
responses.)

## Scope
- All seasons available via the API for `site_id=5251`. Probing the API
  shows data from 1990 through 2026 inclusive, totalling ~2,500 matches.
- Senior + junior, all formats, home + away, all team variants.
- Both Rainham home matches and Rainham away matches (the API returns the
  match for the queried site whether home or away — `home_club_id` /
  `away_club_id` will be `5251` on the Rainham side).

## Atomic grain
The dataset has multiple grains. Each gets one SQLite table.

The schema is **club-agnostic**: every batting / bowling row carries the
club_id of the team it belongs to (`team_batting_club_id`,
`team_bowling_club_id`), and the `clubs` table catalogues every club seen
in any cached match. Rainham-specific reports filter on
`team_batting_club_id = '5251'`. Opposition scouting (e.g. `scout.py`)
filters on the opposition's club_id.

| Table | One row per | Composite key |
| --- | --- | --- |
| `clubs` | club | `club_id` |
| `matches` | match | `match_id` |
| `match_players` | (match, team_side, player) | `match_id, team_side, player_id` |
| `innings` | (match, innings_seq) | `match_id, innings_seq` |
| `batting` | (match, innings_seq, batting position) | `match_id, innings_seq, position` |
| `bowling` | (match, innings_seq, bowling position) | `match_id, innings_seq, bowl_position` |
| `fall_of_wickets` | (match, innings_seq, wicket number) | `match_id, innings_seq, wicket` |

> **Why `innings_seq`, not `innings_number`?** The Play-Cricket API returns
> `innings_number = 1` for both sides of a limited-overs match in 2,252 of
> 2,254 cached matches with innings data. Using `innings_number` as a key
> silently overwrites one team's innings with the other's — which is exactly
> the bug that made the v1 totals come out at ~50% of the correct figures
> when validated against rainhamcc.play-cricket.com. `innings_seq` is the
> 1-based ordinal of the innings within the match payload (1, 2, 3, …);
> `innings_number` is preserved as a non-key reference column.

Reports query these. Never mix grains.

## Columns (canonical, post-cleanup)

### `matches`
`match_id, season, match_date, match_time, league_id, league_name,
competition_id, competition_name, competition_type, match_type, game_type,
ground_id, ground_name, home_club_id, home_club_name, home_team_id,
home_team_name, away_club_id, away_club_name, away_team_id, away_team_name,
toss_won_by_team_id, toss, batted_first, no_of_overs, no_of_innings,
no_of_players, result, result_description, result_applied_to,
rainham_team_side` (derived: `home` / `away` / `both` / `none`)

### `batting`
`match_id, innings_number, position, batsman_name, batsman_id, how_out,
fielder_name, fielder_id, bowler_name, bowler_id, runs, fours, sixes, balls,
team_batting_id, team_batting_name, is_rainham_player` (derived)

### `bowling`
`match_id, innings_number, bowl_position, bowler_name, bowler_id, overs,
maidens, runs, wides, no_balls, wickets, team_bowling_id, team_bowling_name,
is_rainham_player` (derived)

### `innings`
`match_id, innings_number, team_batting_id, team_batting_name, runs, wickets,
overs, balls, total_extras, extra_byes, extra_leg_byes, extra_wides,
extra_no_balls, extra_penalty_runs, declared, forfeited_innings,
revised_target_runs, revised_target_overs`

### `fall_of_wickets`
`match_id, innings_number, wicket, runs, batsman_out_id, batsman_out_name,
batsman_in_id, batsman_in_name, batsman_in_runs`

### `match_players`
`match_id, team_side ('home' | 'away'), position, player_id, player_name,
captain, wicket_keeper, club_id`

## Metric: "Top 10 run scorers across all formats / teams"

### Formula
For each unique batsman who played for **Rainham CC, Essex** (any team, any
format):
- `innings = count(distinct (match_id, innings_number)) where the batsman batted`
- `not_outs = count(...) where how_out IN ('not out', 'no', 'retired not out', '', null) and runs were scored`
- `runs = sum(batting.runs)` over those innings
- `balls = sum(batting.balls)`
- `fours, sixes = sum`
- `highest_score = max(batting.runs)` with `*` if the highest was a not-out
- `average = runs / (innings - not_outs)` (∞ if no dismissals)
- `strike_rate = 100 * runs / balls`
- `matches = count(distinct match_id)` where batsman appears in batting OR
  match_players for Rainham

### Defining "Rainham player" for the batting card
A batting row is a Rainham row when `team_batting_name` belongs to Rainham,
i.e. `team_batting_id` is one of the Rainham-side team IDs from the matches
table where `home_club_id = 5251` (use `home_team_id`) or `away_club_id = 5251`
(use `away_team_id`).

Player IDs on Play-Cricket are stable — same `player_id` across teams and
seasons — so we aggregate on `batsman_id`.

### Player name canonicalisation
Use the most-recent non-empty `batsman_name` per `batsman_id` as the canonical
display name (player names get edited over time).

### Tie-break / ranking
Order by `runs` desc, then `average` desc, then `innings` asc.

## Pipeline

1. `fetch.py` — pulls `matches.json` per season (`data/raw/matches/<year>.json`)
   then `match_detail.json` per match (`data/raw/match_detail/<match_id>.json`).
   Idempotent: skips files that already exist on disk. Rate-limit aware
   (concurrent with cap, retry-with-backoff on 429/5xx).

2. `build_db.py` — loads cached JSON into SQLite at `data/rainham.db`. Drops
   and rebuilds tables every run; fast (purely local). Computes the
   derived columns.

3. `top_run_scorers.py` — queries SQLite, writes
   `top_run_scorers.md`. **Reports never re-scrape.**

4. `streaks_and_fifties.py` — queries SQLite, writes
   `streaks_and_fifties.md`: most 50+ scores per player, longest streaks of
   consecutive ducks (out for 0), longest streaks of consecutive 10+ scores.

If a future report needs a column not in the DB:
1. extend `build_db.py`,
2. update this PLAN and `data/README.md`,
3. rebuild,
4. then write the report.

## What is NOT in the cache
- League tables (`league_table.json`) — not needed for run scorers.
- News/articles, media, kit orders, the `inc_*.php` HTML fragments.
- Live in-flight matches mid-innings — we only see whatever's been published
  to Play-Cricket at fetch time. The latest 2026 season is partial (it's
  2026-05-03 today) and will become more complete on subsequent fetches.
- Match notes longer than ~64 KB are truncated by SQLite TEXT (none observed).

## Validation
v1 of this pipeline came out at ~50% of the correct run totals. Cross-checking
the top 10 against `rainhamcc.play-cricket.com`'s own leaderboard surfaced the
duplicate-`innings_number` bug above. After the fix, the order of the top 10
matches the reference exactly, and the totals agree to within ±0.5%
(remaining variance reflects the reference snapshot being from a slightly
different date than the local fetch).

**Lesson:** always sanity-check the headline number against the canonical
site before trusting any derived stat.

## Frictions to flag up front
- **Sandbox egress.** ECB docs page is 403 from this sandbox; the API itself
  is reachable. The reverse-engineered schema below is from sample responses,
  not the official doc.
- **Duplicate `innings_number`.** Resolved by using a 1-based array-position
  `innings_seq` as the innings key (see schema note above). Without this fix,
  one of the two innings of every limited-overs match got silently
  overwritten and totals were ~50% of reality.
- **Format drift.** Pre-2005 matches are sparse (1–3/year). Some have no
  `innings` data at all. Treat absent `innings` as "no scorecard recorded";
  these rows still go into `matches` but contribute no batting/bowling rows.
- **Player ID stability.** Play-Cricket player IDs are stable but the same
  human can have multiple IDs if registered separately as a junior and senior.
  We do not attempt manual reconciliation in v1; flagged as a known limitation.
- **Junior matches.** Some junior age-group matches use `match_type=Pairs` and
  may not have full bat cards. They're included; their batting rows are
  sparse but valid.
