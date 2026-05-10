# RUNBOOK — fetch + clean + model from scratch

The end-to-end recipe to take a clean clone of the repo and arrive at a
working v3d projection model. Includes every data-quality fix we've
discovered along the way.

For session-recovery (mid-flight rebuild), see `RECOVERY.md`. For modelling
plans see `model/PLAN.md`. For BBB scope findings see
`model/FINDINGS.md` and the per-survey reports under `reports/`.

## TL;DR — the commands, in order

```bash
# 1. clone + install
git clone <repo>
cd play-cricket
pip install numpy pandas scikit-learn lightgbm boto3 pyarrow

# 2. discover universe (15s; scrapes 33 league homepages)
python3 model/bench/scrape_universe.py

# 3. fetch summaries + match details + BBB for all 32 active leagues
#    (~5-10h wall, idempotent — re-runs skip cached files)
python3 model/bench/run_all.py

# 4. build the SQLite DB from cached JSON (~30 min on full corpus)
python3 build_db.py
# → automatically applies the innings_seq realignment fixup

# 5. build the per-month skill snapshot (1-2 min)
python3 model/skill/build_monthly_snapshots.py

# 6. build per-ground rolling 2y stats (10s)
python3 model/skill/build_ground_stats.py

# 7. train + evaluate the v3d projection model (~5 min)
python3 model/poc/run_poc_v3d.py

# 8. (optional) headline reports + ad-hoc analyses
python3 model/poc/surprise_at_10ov.py
python3 model/bench/aggregate.py
```

End state:
- `data/raw/` (committed) — source-of-truth JSON cache
- `data/rainham.db` (gitignored) — derived SQLite, ~5 GB after full build
- `data/skill_monthly.parquet`, `data/ground_stats.parquet` — committed feature snapshots
- `model/poc/results_v3d.{md,json}` — model evaluation
- `model/bench/<slug>.json` — per-league fetch timings

## Data-quality gotchas we've learned (and fixed)

These are all baked into the scripts above. Documented here so a fresh
contributor knows *why* each step exists.

### 1. Homepage-scraped universe (not regex on competition names)

The Play-Cricket public API has no `tier` / `display_order` field on
competitions. The league name regex (`1st XI|Premier|Division N`) is too
fragile — most ECB Premier League sites name divisions plainly
(`Division 1`, `Division 2 A/B`) and there are sponsor prefixes
(`Beechwood Mazda - ECB Premier Division`).

**Fix:** `scrape_universe.py` pulls each league's homepage and parses the
`<ul class="list-comp">` accordion in HTML order. That ordering is the
admin-set display order. Take the top N senior-men's tiers (after killing
2nd XI / Junior / Women / T20 / Indoor via EXCL).

### 2. `match_type = 'Limited Overs'` only

`Declaration` (= timed cricket with possible declarations) plays
fundamentally differently. Drop it. Essex's BBB era (2021+) is 100%
Limited Overs anyway, but other leagues still have a Declaration tail.

### 3. Drop abandoned / cancelled matches

`result IN ('A','C')` or `result_description` matching
`Abandon|No Result|Cancelled|No decision` → about 15% of matches in any
season. These don't have meaningful y targets (no real "innings final
score") and corrupt model training.

Wired into `run_poc_v3d.load_balls()`.

### 4. **`ball_no` resets per over** (don't sort by it!)

The single biggest bug in the early POCs. `ball_no` is the *within-over*
ball number (1..6, more if there are wides). Sorting by `(match_id,
ball_no)` interleaves all "1st-balls-of-overs", then all "2nd-balls",
etc. — completely scrambling the actual delivery order.

**Fix:** sort by `(match_id, over_no, ball_no)`. Wired into v3c's
`add_state()`. Same correction needed in any new ad-hoc query that walks
balls in order.

### 5. Innings-seq transposition (~13% of matches)

For ~13% of matches, the `balls.innings_seq` and `innings.innings_seq`
labels disagree — the BBB stream sometimes labels the chase as innings 1
when the home team batted second.

Symptom: `balls.team_batting_club_id` for innings_seq=1 doesn't match
`innings.team_batting_club_id` for innings_seq=1.

**Fix (in `build_db.py`):** `realign_balls_innings_seq()` runs after the
ball-loading phase. For each affected match, swaps innings_seq labels
1↔2 (and 3↔4 etc. if needed) on the `balls` table directly.

### 6. Truncated BBB streams

Sometimes a match's BBB cut off partway through the innings (live
scorer disconnected etc.). `SUM(balls.runs_bat + balls.runs_extra)`
would give the wrong final.

**Fix:** in v3d, derive `y_final_innings_runs` by joining
`balls.team_batting_club_id` to `innings.team_batting_club_id` and
reading `innings.runs` (the scorecard total). The 10-over snapshot is
still from BBB — we just don't trust BBB for the *final* total.

### 7. Float64 player_id silent join failures

The skill parquet was written with `player_id` as float64. `.loc[int(pid)]`
silently failed on the float index — every skill lookup returned NaN. The
v3a POC trained on a "skill-feature" set that was 100% NaN.

**Fix:** cast both `player_id` and `snapshot_yyyymm` to `Int64` (nullable)
on both sides before the merge. Wired into v3c's `join_skill()`.

### 8. LGBM importance: gain, not split

`importance_type='split'` (the default) counts how many times a feature
was the split variable. Wide-range continuous features (like
`match_date_yyyymmdd`) get picked many times for marginal-gain splits
and look dominant. Use `importance_type='gain'` (sum of loss reduction)
for the real ranking.

### 9. Universe filter at *query* time too

The bulk-fetch only fetches matches in the universe. But the DB also
contains historical "off-universe" matches from older crawls (Rainham's
2nd XI fixtures, U13 Indoor, etc.). Any analysis using `match_type =
'Limited Overs'` alone will pick those up.

**Fix:** intersect `match_id` with the union of cohort `competition_id`s
from `model/bench/<slug>.json` files. Wired into v3d's
`load_universe_match_ids_from_db()`.

### 10. 12-month rolling skill window with per-month snapshots

Single-snapshot-as-of-today gave essentially zero skill signal because
2021-2024 matches had stale or NaN player skills. Per-(year, month)
snapshots, joined on the match's calendar month, give the right
"who-was-good-then" view for every ball.

The window is **12 months**, sliding monthly. Built by
`build_monthly_snapshots.py`.

## Model architecture (current)

Three trained variants:

- **v1**: state only (runs, wickets, balls left, run rate, intra-match
  per-batter and per-bowler stats). The fair baseline. ~30.3 MAE.
- **v3c**: v1 + per-month player skill features (5 metrics × 3 roles =
  15 columns) + season + match_month. ~28.6 MAE (−1.7 vs v1).
- **v3d**: v3c + ground rolling 2y stats (3 columns) + remaining-batters
  skill aggregates sliced by wickets-down (5 columns) + bowling-team
  strength (7 columns, static per match). **~26.8 MAE (−3.5 vs v1)**.

Skills are five separate `/100` percentiles per player per month,
**never blended**: `bat_avg_skill`, `bat_sr_skill`, `bowl_econ_skill`,
`bowl_avg_skill`, `bowl_sr_skill`.

## Backups

- Raw JSON: committed to git (~30 GB after full fetch — watch GitHub's
  5 GB soft cap).
- DB (`rainham.db`): gitignored. Backup to S3 with
  `model/ops/backup_db.py` if you want off-machine durability.
- Supabase Postgres host noted in `RECOVERY.md` for slim derived data
  (skill snapshots etc.) — not yet wired.

## Next-phase TODOs

Big things, ordered roughly by lift-per-effort:

1. **Win probability conversion.** Convert v3d's predicted-final into
   `P(team A wins)` for the 1st innings, given a chase model for the
   2nd. Brier score + calibration plots as the metric. The v3d
   prediction interval (p10/p90) is the natural input.
2. **Timed games and draw potential.** Re-introduce `match_type =
   'Declaration'` games as a separate model. Predict probability of
   draw vs decisive result, plus expected runs in remaining overs given
   declaration patterns.
3. **Per-player per-ball contribution.** Model the *delta* in projection
   credited to each ball, then sum across all balls a player participated
   in. Gives a single-number "player-of-the-match" contribution score
   straight from the model.
4. **Sync to Supabase.** Push the slim derived tables (player skill
   snapshots, ground stats, match summaries) to the Postgres at
   `db.pfmajgetbhdcerkqswkc.supabase.co` so they're queryable by a
   future web app. Stay under the 500 MB free-tier cap by NOT pushing
   raw balls.
5. **Live match centre.** Live page consuming Play-Cricket's BBB feed,
   running v3d ball-by-ball, showing live projection + win probability.
6. **Static pages for the ad-hoc requests** so far:
   - top-5 highest 1st innings · highest individual scores by position
   - biggest comebacks / collapses
   - RCC bat-first vs bowl-first by season (home + away splits)
   - opposition scouting refresh against the new corpus
7. **Strategy calculator.** Given the current state, suggest the optimal
   declaration / batting order / bowling rotation by counterfactual
   simulation against the v3d model.
8. **More ad-hocs to come** — keep them in `reports/ad-hoc/` with the
   committed query script alongside.
9. **"Greatest last-over wins" with video.** From BBB, find finishes
   where the chasing side won off the last over with one of: a six off
   the last ball, a wicket-then-six, a 4+ to win etc. Cross-reference
   with NV Play match payloads (which carry per-ball `Video.VideoUrl`)
   to surface the actual clip.

Open architectural questions:

- **DB choice**: stay on SQLite or migrate to DuckDB for analytics? The
  ball-by-ball workload is very columnar; DuckDB would be 10-100×
  faster on the OLAP queries. SQLite is fine for now.
- **Train/val/test discipline**: currently a random 25% split by
  match_id within the universe. For deployment we'd want a temporal
  split (train ≤ 2024, val 2025, test 2026) to honestly measure
  generalisation to a new season.
- **Bayesian shrinkage on skill**: players with <100 balls in the
  trailing 12 months currently get NaN. The PLAN calls for shrinkage to
  the cohort median; not yet implemented.
