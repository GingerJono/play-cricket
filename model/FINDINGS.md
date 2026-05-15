# Findings — BBB at the top of the amateur pyramid

What the Essex test (see `reports/league/essex-survey/v1/survey.md` and
`PLAN_AMATEUR_MODELLING.md`) showed us, distilled to what matters for
the modelling project.

## 1. Ball-by-ball is dense and well-covered

Across a 50-match Essex 1st-XI sample (2021–2025, top 5 divisions):

- **96 % BBB coverage** — 40 % via ResultsVault (PCS-scored), 56 % via
  NV Play (livestream-scored), 4 % no data.
- Coverage is highest in **Premier + Div 1**; tails off mildly through
  Div 2 / 3 / 4 but still very usable.
- Pre-2021 coverage is materially worse (paper scoring still common)
  and is **out of scope** for the modelling work.

Scaling assumption: the same shape will hold across the other 32 ECB
Premier Leagues. So the modellable BBB universe is something like:
**32 × ~3.5k matches × ~0.95 BBB rate ≈ 100k matches × ~250 deliveries
≈ ~25M balls** for 2021–2025. Plenty.

## 2. The ball schema is rich enough for game-state modelling

Every RV ball row carries:

| Field | Use |
|---|---|
| `match_id`, `innings_number`, `over_no`, `ball_no`, `ball_no_disp` | positional state |
| `batter_id`, `batter_id_ns`, `bowler_id` | player identifiers (PC namespace, after `_disambig.py`) |
| `runs_bat`, `runs_extra`, `extras_type` | runs decomposition |
| `dismissed_batter_id` | wicket tracking |
| `ball_spot_x`, `ball_spot_y`, `shot_angle`, `shot_length` | wagon-wheel + length-on-pitch (PCS only) |
| `ProgScore` | running team score, useful as a sanity check |
| `s_desc`, `l_desc` | commentary text (auto-generated) |

NV Play balls carry the same essentials plus `Video.VideoUrl`
per-delivery (see survey).

The shot-spot / pitch-map fields come for free on PCS-scored matches
and are a strong feature class for the projection model later (shot
selection patterns by batter, length tendencies by bowler).

## 3. Match-type filtering is trivial

`match_type` on the matches summary is one of:

- `Limited Overs` — what we want.
- `Declaration` — timed cricket; **excluded**.
- (`Twenty20` / similar may appear in T20 cup competitions — we
  already exclude those at the universe-filter step.)

Essex switched to majority `Limited Overs` in 2019 and is 100 % from
2020 onwards, so the BBB-rich era (2021+) is naturally all overs
cricket. Other leagues to verify but expected to be similar at the
top of the pyramid.

## 4. Player IDs are stable and cross-club

The Play-Cricket `player_id` is the canonical key (already established
by the metadata project — see CLAUDE.md §"Roadmap: opposition
metadata"). It follows the player across clubs, so a batter's career
record is one record per player_id, not per (player, club).

Disambiguation between RV's player-id namespace and NV's name-only
attribution → PC IDs is already solved by `_rv_balls.py` /
`_nvplay_balls.py` / `_disambig.py`. Per CLAUDE.md the union pass
hits ~95 % batter / ~92 % bowler attribution accuracy. **NULL is
acceptable** — the modelling pipeline can drop balls with
unattributed batter/bowler when computing player-skill features
without losing meaningful signal at scale.

## 5. Ground identifiers are populated

`ground_id` + `ground_name` + `ground_latitude` / `ground_longitude`
are present on every match. Two grounds-per-club is common (1st XI
ground vs 2nd XI ground), and we already see this on the Rainham
side. Multiple pitches per ground are NOT distinguished by the API
— a `ground_id` is a venue, not a strip. That's a known limitation
to flag for ground-effect modelling.

## 6. What's missing / worth flagging

- **No weather data on the API.** If we want temperature / humidity /
  recent rainfall we'll need to join externally (Met Office or
  open-meteo). Probably v2; date + lat/long is enough to retrieve.
- **No toss data** on `match_detail` reliably. We can sometimes infer
  who batted first from `innings[].is_home`, but the toss-winner is
  not consistently exposed. Treatment: derive "batted first" rather
  than "won the toss"; they correlate.
- **DLS / interrupted matches** are not flagged at the match level.
  We can detect them downstream via `(target_runs, balls_faced,
  result_description)` heuristics but it adds complexity. v1 plan:
  drop matches whose `result_description` mentions D/L or rain.
- **Duplicate / inflated divisions.** Some Essex divisions show 160
  or 190 fixtures where 90 is expected — likely playoff brackets
  re-tagged as League. Filter at universe level.
- **Innings-2 chase truncation.** The actual final score for a
  successfully-chased innings is right-censored — they stopped at
  target+1, not at the all-out total. This is _the_ headline
  modelling consideration; see PLAN.md §"2nd-innings handling".

## 7. Scale — feasibility check

For the modelling pipeline:

- Universe: ~100k matches × ~250 balls ≈ ~25M ball rows for
  2021-2025 across 33 leagues.
- That fits comfortably in SQLite on a single laptop (the existing
  `rainham.db` is ~200MB for the Rainham subset; 100× scale → ~20GB,
  manageable). DuckDB/Parquet is the obvious upgrade if SQLite gets
  slow.
- Player-skill iteration over ~10-50k unique players × ~30 deliveries
  per match × dependent updates: easily hours, not days.
- GBM training on 25M rows × 30-50 features → minutes per fit on
  modern CPU.

No blocker. Time to plan the model.
