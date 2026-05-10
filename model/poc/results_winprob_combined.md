# Combined win-prob — innings 1 + chase + isotonic

Two LightGBM binary classifiers trained on a 60/20/20 split:
60% train / 20% calibration (isotonic fit) / 20% val (held-out).

Saved artefacts under `model/poc/winprob/`:
  - `innings1_lgbm.txt`, `innings1_isotonic.json`
  - `chase_lgbm.txt`, `chase_isotonic.json`

## Headline (held-out val)

| Model | Brier (raw) | Brier (calibrated) | BSS (raw → cal) |
|---|---:|---:|---:|
| Innings 1 | 0.1993 | **0.2000** | 0.200 → **0.197** |
| Chase     | 0.1176 | **0.1174** | 0.527 → **0.528** |

## Innings 1 calibration (raw vs isotonic)

| bucket | n | mean pred (raw) | empirical | mean pred (cal) |
|---|---:|---:|---:|---:|
| [0.0, 0.1) | 7,335 | 0.084 | 0.120 | 0.075 |
| [0.1, 0.2) | 59,597 | 0.157 | 0.135 | 0.166 |
| [0.2, 0.3) | 93,930 | 0.252 | 0.249 | 0.251 |
| [0.3, 0.4) | 114,265 | 0.352 | 0.362 | 0.354 |
| [0.4, 0.5) | 128,252 | 0.450 | 0.436 | 0.454 |
| [0.5, 0.6) | 128,603 | 0.550 | 0.542 | 0.552 |
| [0.6, 0.7) | 125,289 | 0.649 | 0.640 | 0.646 |
| [0.7, 0.8) | 119,516 | 0.749 | 0.744 | 0.744 |
| [0.8, 0.9) | 94,729 | 0.846 | 0.850 | 0.839 |
| [0.9, 1.0) | 29,385 | 0.927 | 0.953 | 0.939 |

## Chase calibration (raw vs isotonic)

| bucket | n | mean pred (raw) | empirical | mean pred (cal) |
|---|---:|---:|---:|---:|
| [0.0, 0.1) | 170,707 | 0.036 | 0.035 | 0.022 |
| [0.1, 0.2) | 53,706 | 0.146 | 0.169 | 0.144 |
| [0.2, 0.3) | 37,345 | 0.248 | 0.293 | 0.268 |
| [0.3, 0.4) | 32,071 | 0.349 | 0.401 | 0.336 |
| [0.4, 0.5) | 29,261 | 0.450 | 0.471 | 0.436 |
| [0.5, 0.6) | 29,951 | 0.551 | 0.566 | 0.541 |
| [0.6, 0.7) | 34,336 | 0.651 | 0.623 | 0.654 |
| [0.7, 0.8) | 42,128 | 0.752 | 0.731 | 0.744 |
| [0.8, 0.9) | 57,327 | 0.854 | 0.840 | 0.850 |
| [0.9, 1.0) | 112,525 | 0.955 | 0.970 | 0.959 |

## Inference helpers

`model/poc/predict_winprob.py` (next) loads the LGBM `.txt` files
plus the isotonic JSONs and exposes:
```python
predict_winprob(state: dict, innings: int) -> float  # P(team batting first wins)
```
`build_matchweek.py` will call this per match-snapshot to bake the
win-prob trajectory into the matchweek JSON.

