# Win probability v0 — innings 1 (team batting first)

- Train: **12,860** matches / 3,389,037 balls
- Val:   **4,287** matches / 1,122,341 balls
- Base win-rate (batting first): **0.529**

## Headline (held-out val)

| Metric | Value |
|---|---:|
| Base-rate Brier (no model) | 0.2492 |
| v0 Brier | **0.1975** |
| v0 log-loss | 0.5778 |
| Brier skill score | **0.208** |

## Calibration (10 bins of predicted P(win))

| bucket | n | mean predicted | empirical W |
|---|---:|---:|---:|
| [0.0, 0.1) | 6,411 | 0.086 | 0.049 |
| [0.1, 0.2) | 79,257 | 0.158 | 0.110 |
| [0.2, 0.3) | 130,216 | 0.252 | 0.233 |
| [0.3, 0.4) | 147,088 | 0.351 | 0.367 |
| [0.4, 0.5) | 156,660 | 0.450 | 0.474 |
| [0.5, 0.6) | 160,382 | 0.550 | 0.573 |
| [0.6, 0.7) | 157,295 | 0.650 | 0.662 |
| [0.7, 0.8) | 147,758 | 0.749 | 0.741 |
| [0.8, 0.9) | 112,241 | 0.845 | 0.857 |
| [0.9, 1.0) | 25,033 | 0.923 | 0.959 |

## Per-ball-position metrics

| balls in | n | Brier | log-loss |
|---|---:|---:|---:|
| 0-29 | 128,315 | 0.2121 | 0.6122 |
| 30-59 | 128,135 | 0.2072 | 0.6007 |
| 60-89 | 127,959 | 0.2017 | 0.5880 |
| 90-119 | 127,286 | 0.1963 | 0.5751 |
| 120-149 | 124,642 | 0.1927 | 0.5666 |
| 150-179 | 120,443 | 0.1926 | 0.5665 |
| 180-209 | 113,458 | 0.1915 | 0.5641 |
| 210-239 | 103,000 | 0.1895 | 0.5589 |
| 240-269 | 84,765 | 0.1918 | 0.5644 |
| 270+ | 64,338 | 0.1916 | 0.5630 |

## Example match trajectories

### match 6987323 — team batting first **won**

| balls in | runs | wkts | P(team A wins) |
|---:|---:|---:|---:|
| 30 | 36 | 1 | 0.73 |
| 60 | 71 | 4 | 0.62 |
| 90 | 106 | 5 | 0.71 |

### match 4600045 — team batting first **won**

| balls in | runs | wkts | P(team A wins) |
|---:|---:|---:|---:|
| 30 | 36 | 0 | 0.75 |
| 60 | 59 | 1 | 0.66 |
| 90 | 81 | 1 | 0.70 |
| 120 | 103 | 1 | 0.62 |
| 150 | 129 | 2 | 0.74 |
| 180 | 160 | 2 | 0.78 |
| 210 | 198 | 2 | 0.86 |
| 240 | 245 | 2 | 0.90 |

### match 4136190 — team batting first **won**

| balls in | runs | wkts | P(team A wins) |
|---:|---:|---:|---:|
| 30 | 12 | 2 | 0.16 |
| 60 | 27 | 3 | 0.15 |
| 90 | 41 | 3 | 0.17 |
| 120 | 59 | 3 | 0.19 |
| 150 | 77 | 3 | 0.23 |
| 180 | 102 | 3 | 0.24 |
| 210 | 139 | 5 | 0.50 |
| 240 | 170 | 6 | 0.48 |

### match 6287909 — team batting first **lost**

| balls in | runs | wkts | P(team A wins) |
|---:|---:|---:|---:|
| 30 | 13 | 1 | 0.41 |
| 60 | 38 | 2 | 0.55 |
| 90 | 58 | 3 | 0.36 |
| 120 | 83 | 5 | 0.45 |
| 150 | 104 | 5 | 0.42 |
| 180 | 119 | 6 | 0.47 |
| 210 | 129 | 6 | 0.44 |
| 240 | 135 | 8 | 0.48 |

## Caveats / next

- Innings 1 only. The chase model (innings 2 → P(chase succeeds))
  is the natural follow-up.
- Class label is `team batting first wins`. Draws / ties / NR
  matches are dropped from training and val.
- Features are exactly the v3d set — no opposition-batting-strength
  feature, just `bowl_team_strength` (= the team that will bat
  in innings 2's *bowling* skill). A separate `bat_team_strength`
  for the chasing side would help.
