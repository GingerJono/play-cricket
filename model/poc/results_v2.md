# POC v2 — skill + ground/league features

- Train: **1,513** matches / 353,889 balls
- Val:   **504** matches / 119,542 balls
- Skill snapshot: 5,549 players (last 12 months)

## Headline (held-out val)

| Model | MAE | Δ vs v1 |
|---|---:|---:|
| naive RR | 55.3 | — |
| v1 (state only) | 31.2 | (baseline) |
| v2 (+ skill + context) | **30.4** | **-0.8** |

v2 80% interval (p10-p90) coverage: **0.71**

## Per-ball-position MAE

| balls in | naive | v1 | v2 | v2 vs v1 |
|---|---:|---:|---:|---:|
| 0-29 | 100.2 | 42.1 | 40.6 | -1.5 |
| 30-59 | 64.7 | 39.6 | 38.1 | -1.5 |
| 60-89 | 57.3 | 38.0 | 36.5 | -1.5 |
| 90-119 | 53.8 | 35.9 | 33.8 | -2.1 |
| 120-149 | 52.0 | 32.3 | 31.1 | -1.2 |
| 150-179 | 49.4 | 28.5 | 28.4 | -0.1 |
| 180-209 | 45.9 | 25.5 | 25.7 | +0.2 |
| 210-239 | 40.4 | 21.3 | 21.4 | +0.2 |
| 240-269 | 32.6 | 16.9 | 17.4 | +0.5 |
| 270+ | 21.0 | 12.9 | 13.7 | +0.8 |

## Top 20 feature importances (v2 p50)

| feature | importance |
|---|---:|
| `overs_per_innings` | 2573 |
| `match_date_yyyymmdd` | 1849 |
| `run_rate` | 902 |
| `wickets` | 604 |
| `runs` | 583 |
| `match_month` | 520 |
| `balls_gone` | 240 |
| `batters_remaining_count` | 168 |
| `season` | 163 |
| `frac_innings` | 147 |
| `balls_left` | 111 |
| `ns_runs_so_far` | 81 |
| `striker_runs_so_far` | 66 |
| `ns_balls_so_far` | 54 |
| `ns_intra_sr` | 36 |
| `bowler_balls_in_innings` | 35 |
| `striker_intra_sr` | 23 |
| `striker_balls_so_far` | 22 |
| `bowler_runs_in_innings` | 4 |
| `bowler_econ_so_far` | 3 |

## Honest reading

The 0.8-run gain over v1 is **almost entirely from `match_date_yyyymmdd`,
`match_month` and `season`** as time-context features. Look at the
importances table: not a single skill column appears in the top 20.
That's not because skill is unimportant — it's because the snapshot
has a temporal-mismatch problem.

The skill snapshot is computed once over the trailing 12 months ending
**today (2026-05-08)**. The training data spans 2021-2026. So:

- For 2025-2026 matches: skill values are roughly correct.
- For 2021-2024 matches: skill values are either stale (player is rated
  on their 2025-26 form not their 2022 form) or NaN (player retired
  before May 2025).

LightGBM handles NaNs natively, but the model can't learn anything
useful from a feature whose value is mostly meaningless for 70% of
the training data. Hence skill features fall to the bottom of the
importance ranking.

## Fix planned for v3

- **Per-season snapshots.** Build six snapshots (`as_of` = end of
  Apr 2021, 2022, 2023, 2024, 2025, 2026); join each ball to the
  snapshot of `season - 1`. Same logic, just temporally aligned.
- **Drop `match_date_yyyymmdd` from features once skill is fixed**
  — once skill encodes "what kind of player is this", the date
  feature should stop being a top-3 driver.

## Other work still missing (per PLAN.md)

- Remaining-batters / remaining-bowlers skill aggregates (need batting
  order parsed from BBB stream).
- Ground rolling stats (avg first-innings total, avg overs).
- Right-censored regression for innings 2.
- Calibration pass on the (p10, p90) interval — currently 71%
  coverage vs 80% target.
