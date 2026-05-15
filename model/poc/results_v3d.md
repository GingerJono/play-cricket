# POC v3d — + ground stats + remaining-batters + bowl-team strength

- Train: **15,000** matches / 3,971,099 balls
- Val:   **5,000** matches / 1,322,192 balls
- Skill non-null after join: 83.9%
- Universe filter at query time: **on**

## Headline (held-out val)

| Model | MAE | Δ vs v1 | Δ vs v3c |
|---|---:|---:|---:|
| naive RR | 47.0 | — | — |
| v1 (state only) | 30.3 | (baseline) | — |
| v3c (+ skill + context) | 28.6 | -1.7 | (baseline) |
| v3d (+ ground + remaining-bat + bowl-team) | **26.8** | **-3.5** | **-1.8** |

v3d 80% interval coverage: **0.76**

## Per-ball-position MAE

| balls in | naive | v1 | v3c | v3d | Δ v3d–v3c |
|---|---:|---:|---:|---:|---:|
| 0-29 | 107.4 | 48.3 | 44.5 | 39.7 | -4.8 |
| 30-59 | 62.6 | 43.5 | 40.7 | 36.7 | -4.0 |
| 60-89 | 52.8 | 39.5 | 36.9 | 33.8 | -3.1 |
| 90-119 | 47.3 | 34.9 | 32.8 | 30.7 | -2.1 |
| 120-149 | 41.8 | 30.0 | 28.4 | 27.2 | -1.2 |
| 150-179 | 36.5 | 25.4 | 24.3 | 23.5 | -0.7 |
| 180-209 | 30.0 | 20.9 | 20.1 | 19.9 | -0.3 |
| 210-239 | 24.4 | 17.1 | 16.6 | 16.6 | +0.1 |
| 240-269 | 20.8 | 14.6 | 14.2 | 14.5 | +0.2 |
| 270+ | 18.8 | 12.1 | 11.9 | 12.3 | +0.4 |

## Top 25 features by gain

| feature | gain |
|---|---:|
| `run_rate` | 3,548,808 |
| `overs_per_innings` | 1,241,771 |
| `runs` | 1,164,703 |
| `bat_remaining_avg_mean` | 954,016 |
| `wickets` | 852,852 |
| `bowl_team_avg_mean` | 545,520 |
| `bowl_team_econ_mean` | 463,605 |
| `striker_bat_avg_skill` | 354,622 |
| `bat_remaining_sr_mean` | 325,613 |
| `ground_overs_avg` | 323,442 |
| `bowl_team_avg_max` | 306,784 |
| `ground_avg_2y` | 259,249 |
| `bowl_team_econ_max` | 232,136 |
| `bowl_team_sr_mean` | 224,151 |
| `bowl_team_sr_max` | 216,375 |
| `bat_remaining_count_real` | 207,279 |
| `ground_n_2y` | 189,614 |
| `bat_remaining_avg_min` | 181,695 |
| `balls_left` | 163,676 |
| `bat_remaining_sr_min` | 129,931 |
| `ns_bat_avg_skill` | 129,242 |
| `season` | 98,738 |
| `balls_gone` | 95,596 |
| `bowl_team_recognised_n` | 95,435 |
| `match_month` | 90,779 |
