# POC v3d — + ground stats + remaining-batters + bowl-team strength

- Train: **15,000** matches / 3,859,687 balls
- Val:   **5,000** matches / 1,283,243 balls
- Skill non-null after join: 83.9%
- Universe filter at query time: **on**

## Headline (held-out val)

| Model | MAE | Δ vs v1 | Δ vs v3c |
|---|---:|---:|---:|
| naive RR | 47.8 | — | — |
| v1 (state only) | 31.6 | (baseline) | — |
| v3c (+ skill + context) | 29.9 | -1.7 | (baseline) |
| v3d (+ ground + remaining-bat + bowl-team) | **28.1** | **-3.5** | **-1.8** |

v3d 80% interval coverage: **0.76**

## Per-ball-position MAE

| balls in | naive | v1 | v3c | v3d | Δ v3d–v3c |
|---|---:|---:|---:|---:|---:|
| 0-29 | 110.8 | 51.1 | 47.0 | 42.3 | -4.7 |
| 30-59 | 65.0 | 45.8 | 42.7 | 39.1 | -3.5 |
| 60-89 | 54.5 | 41.2 | 38.5 | 36.0 | -2.5 |
| 90-119 | 47.3 | 35.8 | 33.8 | 32.1 | -1.7 |
| 120-149 | 41.2 | 30.8 | 29.3 | 28.0 | -1.3 |
| 150-179 | 35.5 | 26.1 | 25.2 | 24.2 | -1.0 |
| 180-209 | 28.9 | 21.1 | 20.5 | 20.1 | -0.5 |
| 210-239 | 23.2 | 16.9 | 16.6 | 16.2 | -0.3 |
| 240-269 | 19.8 | 14.5 | 14.2 | 14.1 | -0.1 |
| 270+ | 17.8 | 12.0 | 11.8 | 12.0 | +0.2 |

## Top 25 features by gain

| feature | gain |
|---|---:|
| `run_rate` | 3,214,730 |
| `runs` | 1,284,192 |
| `overs_per_innings` | 1,229,337 |
| `wickets` | 799,203 |
| `bat_remaining_avg_mean` | 782,337 |
| `bowl_team_avg_mean` | 449,192 |
| `bowl_team_econ_mean` | 379,086 |
| `striker_bat_avg_skill` | 336,179 |
| `bat_remaining_sr_mean` | 285,309 |
| `bowl_team_avg_max` | 257,439 |
| `ground_overs_avg` | 237,189 |
| `bat_remaining_count_real` | 203,373 |
| `ground_avg_2y` | 198,412 |
| `ground_n_2y` | 183,460 |
| `bowl_team_sr_mean` | 176,368 |
| `bowl_team_econ_max` | 168,022 |
| `bowl_team_sr_max` | 158,005 |
| `balls_left` | 157,465 |
| `bat_remaining_avg_min` | 145,929 |
| `ns_bat_avg_skill` | 122,876 |
| `bat_remaining_sr_min` | 111,501 |
| `season` | 98,693 |
| `match_month` | 85,329 |
| `balls_gone` | 78,860 |
| `bowl_team_recognised_n` | 59,775 |
