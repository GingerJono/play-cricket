# POC results — Essex 1st XI BBB

State-only feature set (no external player skill snapshots).
Innings 1 only. Train/val split is by match_id; the table below
reports MAE on held-out matches.

## Headline

- Train matches: **269** (35390 ball rows)
- Val matches:   **90** (12398 ball rows)
- LGBM p50 MAE:  **24.8 runs**
- Naive RR MAE:  **39.6 runs**
- 80% interval (p10-p90) empirical coverage: **0.73**

## Per-ball-position MAE (val)

| balls in | LGBM p50 | naive RR |
|---|---:|---:|
| 0-29 | 27.6 | 65.7 |
| 30-59 | 23.2 | 34.1 |
| 60-89 | 24.0 | 28.9 |
| 90-119 | 24.7 | 28.6 |
| 120-149 | 28.7 | 38.8 |
| 150-179 | 26.2 | 39.4 |
| 180-209 | 22.7 | 36.8 |
| 210-239 | 20.3 | 31.4 |
| 240-269 | 22.0 | 19.7 |
| 270+ | 18.4 | 14.8 |

## Example projection trajectories

Each row shows the feature state at that ball position and
the model's predicted final innings score (p10 / p50 / p90)
alongside the naive run-rate extrapolation.

### match_id 7017021 — Benfleet CC v Rainham CC, Essex (14/06/2025)
_Division 05 - 1st XI Division Four_  ·  actual final innings 1 score: **233**

| balls in | runs | wkts | naive | p10 | p50 | p90 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 23 | 0 | 207 | 190 | 250 | 256 |
| 60 | 42 | 0 | 189 | 190 | 250 | 255 |
| 90 | 65 | 0 | 195 | 187 | 252 | 255 |
| 120 | 78 | 0 | 176 | 185 | 246 | 256 |
| 150 | 103 | 0 | 185 | 193 | 252 | 257 |
| 180 | 122 | 2 | 183 | 195 | 252 | 256 |
| 210 | 141 | 2 | 181 | 191 | 249 | 254 |
| 240 | 167 | 3 | 188 | 196 | 248 | 253 |

### match_id 5293291 — Rainham CC, Essex v Aztecs CC, Ilford (25/06/2022)
_Division 04 - 1st XI Division Three_  ·  actual final innings 1 score: **188**

| balls in | runs | wkts | naive | p10 | p50 | p90 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 27 | 1 | 175 | 171 | 222 | 320 |
| 60 | 36 | 2 | 116 | 167 | 202 | 237 |
| 90 | 64 | 3 | 138 | 166 | 194 | 237 |
| 120 | 80 | 3 | 129 | 168 | 202 | 234 |
| 150 | 100 | 5 | 129 | 164 | 191 | 224 |
| 180 | 110 | 6 | 119 | 159 | 157 | 208 |
| 210 | 129 | 8 | 129 | 152 | 165 | 200 |
| 240 | 142 | 9 | 142 | 155 | 164 | 203 |

### match_id 6525712 — Rainham CC, Essex v Roding Valley CC (17/08/2024)
_Division 05 - 1st XI Division Four_  ·  actual final innings 1 score: **243**

| balls in | runs | wkts | naive | p10 | p50 | p90 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 24 | 1 | 197 | 198 | 191 | 245 |
| 60 | 64 | 1 | 262 | 186 | 190 | 248 |
| 90 | 90 | 1 | 246 | 185 | 192 | 248 |
| 120 | 115 | 1 | 236 | 192 | 198 | 255 |
| 150 | 138 | 2 | 226 | 196 | 208 | 265 |
| 180 | 158 | 3 | 216 | 198 | 206 | 245 |
| 210 | 177 | 3 | 207 | 205 | 210 | 252 |
| 240 | 214 | 4 | 219 | 224 | 232 | 280 |

### match_id 5293307 — Rainham CC, Essex v West Essex CC (02/07/2022)
_Division 04 - 1st XI Division Three_  ·  actual final innings 1 score: **233**

| balls in | runs | wkts | naive | p10 | p50 | p90 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | 23 | 0 | 207 | 191 | 250 | 256 |
| 60 | 45 | 1 | 202 | 189 | 251 | 255 |
| 90 | 63 | 1 | 189 | 186 | 252 | 255 |
| 120 | 83 | 2 | 187 | 185 | 252 | 256 |
| 150 | 98 | 3 | 176 | 180 | 242 | 250 |
| 180 | 111 | 3 | 166 | 174 | 212 | 249 |
| 210 | 147 | 6 | 189 | 190 | 225 | 237 |
| 240 | 188 | 8 | 212 | 198 | 225 | 235 |

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
