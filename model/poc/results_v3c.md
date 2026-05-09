# POC v3c — disambiguated PC IDs from SQL + per-month skill

Reads the `balls` SQL table (which carries PC-namespace player_ids
after `_rv_balls.py` / `_disambig.py` have run), not raw JSON.
Drops rows where disambiguation left batter_id or bowler_id NULL.

- Train: **18,676** matches / 4,443,171 balls
- Val:   **6,225** matches / 1,480,776 balls
- Skill rows: per-month, joined by (player_id, year×100+month)
- Striker skill non-null after join: **86.7%**

## Headline (held-out val)

| Model | MAE | Δ vs v1 |
|---|---:|---:|
| naive RR | 39.3 | — |
| v1 (state only) | 27.4 | (baseline) |
| v3c (+ per-month skill) | **26.0** | **-1.4** |

v3c 80% interval coverage: **0.79**

## Per-ball-position MAE

| balls in | naive | v1 | v3c | v3c vs v1 |
|---|---:|---:|---:|---:|
| 0-29 | 107.8 | 48.3 | 43.8 | -4.5 |
| 30-59 | 56.1 | 39.9 | 37.4 | -2.5 |
| 60-89 | 44.6 | 35.3 | 33.3 | -2.0 |
| 90-119 | 37.6 | 30.4 | 29.3 | -1.1 |
| 120-149 | 30.7 | 25.6 | 24.8 | -0.8 |
| 150-179 | 25.4 | 21.7 | 21.1 | -0.5 |
| 180-209 | 19.5 | 17.6 | 17.2 | -0.3 |
| 210-239 | 15.3 | 14.3 | 14.1 | -0.2 |
| 240-269 | 13.2 | 12.4 | 12.2 | -0.1 |
| 270+ | 15.1 | 11.6 | 11.3 | -0.2 |

## Top 20 features by GAIN

| feature | gain |
|---|---:|
| `run_rate` | 6,495,488 |
| `runs` | 1,526,776 |
| `overs_per_innings` | 1,351,918 |
| `wickets` | 999,732 |
| `striker_bat_avg_skill` | 325,272 |
| `balls_left` | 312,812 |
| `season` | 264,782 |
| `ns_bat_avg_skill` | 216,157 |
| `balls_gone` | 192,660 |
| `bowler_bowl_avg_skill` | 146,195 |
| `bowler_bowl_econ_skill` | 126,145 |
| `match_month` | 122,771 |
| `ns_bat_sr_skill` | 106,542 |
| `striker_bat_sr_skill` | 99,473 |
| `bowler_econ_so_far` | 88,954 |
| `bowler_balls_in_innings` | 79,416 |
| `bowler_runs_in_innings` | 51,483 |
| `bowler_bat_avg_skill` | 48,735 |
| `frac_innings` | 48,184 |
| `bowler_bowl_sr_skill` | 47,674 |
