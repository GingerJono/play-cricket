# POC results — Essex 1st XI BBB

State-only feature set (no external player skill snapshots).
Innings 1 only. Train/val split is by match_id; the table below
reports MAE on held-out matches.

## Headline

- Train matches: **1510** (351374 ball rows)
- Val matches:   **503** (121144 ball rows)
- LGBM p50 MAE:  **31.5 runs**
- Naive RR MAE:  **56.3 runs**
- 80% interval (p10-p90) empirical coverage: **0.73**

## Per-ball-position MAE (val)

| balls in | LGBM p50 | naive RR |
|---|---:|---:|
| 0-29 | 42.0 | 99.2 |
| 30-59 | 39.5 | 64.5 |
| 60-89 | 38.1 | 58.6 |
| 90-119 | 36.0 | 55.6 |
| 120-149 | 33.8 | 54.9 |
| 150-179 | 30.1 | 51.5 |
| 180-209 | 25.8 | 46.8 |
| 210-239 | 20.6 | 41.2 |
| 240-269 | 15.6 | 32.9 |
| 270+ | 11.1 | 19.9 |

## Example projection trajectories

Each row shows the feature state at that ball position and
the model's predicted final innings score (p10 / p50 / p90)
alongside the naive run-rate extrapolation.

### match_id 6741081 — Cranleigh CC v Walton on Thames CC (02/08/2025)
_Division 1_  ·  actual final innings 1 score: **290**

| balls in | runs | wkts | naive | p10 | p50 | p90 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 10 | 0 | 103 | 176 | 221 | 279 |
| 60 | 35 | 0 | 180 | 180 | 222 | 282 |
| 90 | 68 | 0 | 233 | 198 | 242 | 287 |
| 120 | 102 | 0 | 262 | 206 | 271 | 316 |
| 150 | 125 | 3 | 257 | 206 | 241 | 297 |
| 180 | 146 | 3 | 250 | 213 | 242 | 293 |
| 210 | 168 | 3 | 246 | 225 | 252 | 287 |
| 240 | 182 | 3 | 234 | 224 | 252 | 281 |

### match_id 7016695 — Buckhurst Hill CC v Hutton CC (26/07/2025)
_Division 01 - 1st XI Premier Division_  ·  actual final innings 1 score: **230**

| balls in | runs | wkts | naive | p10 | p50 | p90 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 15 | 2 | 168 | 124 | 246 | 283 |
| 60 | 26 | 3 | 146 | 126 | 223 | 277 |
| 90 | 40 | 3 | 149 | 118 | 212 | 272 |
| 120 | 48 | 3 | 134 | 114 | 211 | 261 |
| 150 | 59 | 3 | 132 | 118 | 211 | 258 |
| 180 | 79 | 3 | 147 | 125 | 211 | 270 |
| 210 | 94 | 4 | 150 | 115 | 210 | 265 |
| 240 | 120 | 4 | 168 | 126 | 243 | 272 |

### match_id 7016670 — Buckhurst Hill CC v Brentwood CC (21/06/2025)
_Division 01 - 1st XI Premier Division_  ·  actual final innings 1 score: **255**

| balls in | runs | wkts | naive | p10 | p50 | p90 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 16 | 0 | 186 | 242 | 276 | 290 |
| 60 | 32 | 1 | 186 | 242 | 268 | 289 |
| 90 | 44 | 2 | 170 | 235 | 251 | 281 |
| 120 | 51 | 2 | 148 | 231 | 242 | 270 |
| 150 | 72 | 2 | 167 | 231 | 245 | 277 |
| 180 | 94 | 3 | 182 | 238 | 259 | 283 |
| 210 | 113 | 3 | 187 | 246 | 264 | 284 |
| 240 | 140 | 4 | 203 | 249 | 278 | 289 |

### match_id 6525314 — Buckhurst Hill CC v Loughton CC (29/06/2024)
_Division 01 - 1st XI Premier Division_  ·  actual final innings 1 score: **269**

| balls in | runs | wkts | naive | p10 | p50 | p90 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 19 | 0 | 142 | 150 | 202 | 266 |
| 60 | 34 | 1 | 128 | 146 | 202 | 263 |
| 90 | 61 | 1 | 152 | 159 | 213 | 266 |
| 120 | 79 | 2 | 148 | 160 | 207 | 265 |
| 150 | 86 | 3 | 129 | 151 | 192 | 247 |
| 180 | 107 | 3 | 134 | 166 | 195 | 249 |
| 210 | 123 | 3 | 132 | 170 | 202 | 251 |
| 240 | 135 | 3 | 135 | 176 | 207 | 249 |

## What this POC does NOT yet have

- **Player skill features.** No `bat_avg_skill / bat_sr_skill
  / bowl_econ_skill / bowl_avg_skill / bowl_sr_skill` joins
  yet — these are the next lift per PLAN.md.
- **Remaining-batters skill aggregates.** Only a
  `batters_remaining_count` exists; the toggle described in
  PLAN.md (`real / default / masked`) needs the player
  skill model first.
- **12-month time-decayed skill snapshots.** Not built yet.
- **Ground rolling stats.** Not joined.
- **Calibration.** 80% interval covers ~73% of held-out
  truths — slightly narrow. Add isotonic calibration on a
  held-out fold once we have more matches.
- **Universe scope.** Only matches with cached BBB on this
  laptop. Expanding to the full Essex 1st-XI universe
  (~3,800 matches × ~95% BBB) is a one-shot fetch_balls run.
