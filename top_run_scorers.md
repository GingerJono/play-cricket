# Rainham CC — Top 25 Run Scorers (All Formats / All Teams)

_Source: `data/rainham.db` (built from `data/raw/match_detail/*.json`).  Rainham matches in cache: **2502** across seasons **1990–2026**.  Rainham batting rows: **21,400**._

Innings counted when `how_out` is anything other than _did not bat_ / _absent_. Not-outs are `not out` and `retired not out`. Averages use `runs / (innings − not_outs)`. `HS*` denotes the highest score was a not-out.

| # | Player | M | I | NO | Runs | HS | Avg | SR | 4s | 6s | 50s | 100s |
|--:|--------|--:|--:|--:|-----:|---:|----:|---:|---:|---:|----:|-----:|
| 1 | Sid Patel | 411 | 411 | 44 | **9989** | 191 | 27.22 | 97.1 | 869 | 173 | 52 | 12 |
| 2 | Daniel Skipper | 446 | 446 | 54 | **8927** | 125 | 22.77 | 62.9 | 500 | 31 | 50 | 4 |
| 3 | Jon O'Neill | 290 | 290 | 39 | **8497** | 141 | 33.85 | 114.3 | 746 | 226 | 53 | 8 |
| 4 | Alex Sullivan | 367 | 367 | 65 | **7931** | 123* | 26.26 | 61.2 | 469 | 34 | 31 | 6 |
| 5 | Jas Hothi | 382 | 382 | 43 | **7741** | 131 | 22.83 | 72.6 | 558 | 36 | 28 | 3 |
| 6 | Ronnie Jackson | 258 | 258 | 26 | **5661** | 177 | 24.40 | 76.9 | 421 | 62 | 24 | 6 |
| 7 | Joe Sarro | 294 | 294 | 40 | **4733** | 77* | 18.63 | 74.4 | 223 | 10 | 11 | 0 |
| 8 | Mickey Callaghan | 227 | 227 | 15 | **4560** | 103* | 21.51 | 47.8 | 142 | 1 | 18 | 2 |
| 9 | Adrian Moon | 190 | 190 | 18 | **4476** | 170* | 26.02 | 76.9 | 120 | 20 | 25 | 2 |
| 10 | Garnet Shallow | 157 | 157 | 32 | **4464** | 122 | 35.71 | 59.8 | 347 | 26 | 34 | 2 |
| 11 | Paul Collis | 180 | 180 | 40 | **4396** | 127* | 31.40 | 86.7 | 148 | 0 | 30 | 1 |
| 12 | Raj Hothi | 241 | 241 | 25 | **4255** | 105 | 19.70 | 86.4 | 359 | 35 | 16 | 2 |
| 13 | Biren Patel | 341 | 341 | 69 | **4169** | 102 | 15.33 | 67.4 | 237 | 26 | 9 | 1 |
| 14 | Peter Reynolds | 174 | 174 | 31 | **3630** | 127* | 25.38 | 72.6 | 110 | 3 | 19 | 3 |
| 15 | James Fuller | 181 | 181 | 26 | **3269** | 106* | 21.09 | 112.1 | 132 | 42 | 9 | 1 |
| 16 | Ben Little | 195 | 195 | 33 | **2916** | 134* | 18.00 | 90.8 | 158 | 29 | 9 | 2 |
| 17 | Nikhil Patel | 203 | 203 | 33 | **2771** | 121* | 16.30 | 50.2 | 147 | 11 | 10 | 3 |
| 18 | David Adkins | 105 | 105 | 12 | **2662** | 151* | 28.62 | 145.2 | 66 | 3 | 15 | 1 |
| 19 | Ashley Foster | 141 | 141 | 25 | **2630** | 100 | 22.67 | 60.4 | 123 | 6 | 13 | 1 |
| 20 | Bobby Little | 176 | 176 | 20 | **2561** | 115* | 16.42 | 76.4 | 156 | 29 | 9 | 2 |
| 21 | Keith Blake | 150 | 150 | 18 | **2455** | 102* | 18.60 | 45.2 | 106 | 6 | 6 | 1 |
| 22 | Tom Herbert | 179 | 179 | 31 | **2314** | 88* | 15.64 | 72.1 | 144 | 22 | 8 | 0 |
| 23 | Tyler Bunn | 172 | 172 | 44 | **2257** | 102 | 17.63 | 60.0 | 155 | 5 | 7 | 1 |
| 24 | Paul Margiotta | 68 | 68 | 7 | **2070** | 127* | 33.93 | 59.0 | 112 | 11 | 15 | 1 |
| 25 | Harry Light | 173 | 173 | 40 | **2045** | 69* | 15.38 | 63.0 | 95 | 4 | 8 | 0 |

> Top 10 are rows 1–10. The next 15 are included for context.

## Definitions

- **Rainham match** = a match where `home_club_id = 5251` or `away_club_id = 5251` (Rainham CC, Essex).
- **Rainham batting row** = a `batting` row whose `team_batting_id` is one of the Rainham team IDs in that match.
- **Player aggregation key** = `batsman_id` from the Play-Cricket API. Stable across teams and seasons. Display name is the most-recent non-empty `batsman_name` for that ID.

## Caveats

- Pre-2005 coverage is sparse on Play-Cricket; results before then rely on whatever has been retrospectively entered.
- A small number of pairs/junior matches record `pairs inning` as the dismissal mode; these are counted as innings batted with the scored runs, with no not-out flag.
- If a player ever appeared as a Rainham team-mate but never had a batting row (e.g. only fielded/bowled), they will not appear here. See `match_players` table for the fuller appearance list.
