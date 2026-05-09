# POC v3c — disambiguated PC IDs from SQL + per-month skill

Reads the `balls` SQL table (which carries PC-namespace player_ids
after `_rv_balls.py` / `_disambig.py` have run), not raw JSON.
Drops rows where disambiguation left batter_id or bowler_id NULL.

- Train: **292** matches / 38,123 balls
- Val:   **98** matches / 12,830 balls
- Skill rows: per-month, joined by (player_id, year×100+month)
- Striker skill non-null after join: **58.8%**

## Headline (held-out val)

| Model | MAE | Δ vs v1 |
|---|---:|---:|
| naive RR | 122.6 | — |
| v1 (state only) | 29.0 | (baseline) |
| v3c (+ per-month skill) | **25.8** | **-3.2** |

v3c 80% interval coverage: **0.70**

## Per-ball-position MAE

| balls in | naive | v1 | v3c | v3c vs v1 |
|---|---:|---:|---:|---:|
| 0-29 | 233.2 | 36.3 | 32.2 | -4.1 |
| 30-59 | 174.9 | 33.9 | 27.7 | -6.2 |
| 60-89 | 130.3 | 33.2 | 26.0 | -7.2 |
| 90-119 | 104.4 | 29.4 | 25.8 | -3.6 |
| 120-149 | 46.4 | 25.4 | 27.2 | +1.8 |
| 150-179 | 35.0 | 22.1 | 23.6 | +1.5 |
| 180-209 | 21.2 | 15.6 | 17.0 | +1.5 |
| 210-239 | 11.7 | 13.9 | 14.5 | +0.6 |
| 240-269 | 10.3 | 14.2 | 15.0 | +0.8 |
| 270+ | 13.9 | 16.0 | 15.6 | -0.4 |

## Top 20 features by GAIN

| feature | gain |
|---|---:|
| `runs` | 47,777 |
| `match_month` | 22,958 |
| `run_rate` | 20,600 |
| `overs_per_innings` | 20,270 |
| `wickets` | 14,573 |
| `season` | 12,442 |
| `bowler_bowl_sr_skill` | 11,944 |
| `striker_bat_avg_skill` | 9,973 |
| `balls_left` | 7,337 |
| `bowler_bowl_econ_skill` | 6,585 |
| `bowler_bowl_avg_skill` | 4,415 |
| `striker_bat_sr_skill` | 4,332 |
| `ns_bat_avg_skill` | 3,723 |
| `bowler_bat_avg_skill` | 3,166 |
| `ns_bat_sr_skill` | 2,921 |
| `bowler_bat_sr_skill` | 2,792 |
| `striker_bowl_sr_skill` | 2,683 |
| `ns_bowl_econ_skill` | 2,397 |
| `striker_bowl_econ_skill` | 2,339 |
| `balls_gone` | 1,714 |
