# Rainham CC — Top 50+ Scorers, Duck Streaks, 10+ Streaks

_All formats / all teams. Source: `data/rainham.db`._

Innings inclusion: rows where `how_out` is anything other than _did not bat_ / _absent_, with a recorded runs value. Streaks are taken in chronological order (`match_date`, then `match_id`, then `innings_seq`, then batting `position`).

## 1. Most 50+ scores (half-centuries + hundreds)

Ranked by total 50+ scores; ties broken by hundreds, then highest score.

| # | Player | 50+ | 50s | 100s | HS | Runs | Inns |
|--:|--------|--:|--:|--:|---:|-----:|-----:|
| 1 | Sid Patel | **64** | 52 | 12 | 191 | 9989 | 411 |
| 2 | Jon O'Neill | **61** | 53 | 8 | 141 | 8497 | 290 |
| 3 | Daniel Skipper | **54** | 50 | 4 | 125 | 8927 | 446 |
| 4 | Alex Sullivan | **37** | 31 | 6 | 123 | 7931 | 367 |
| 5 | Garnet Shallow | **36** | 34 | 2 | 122 | 4464 | 157 |
| 6 | Jas Hothi | **31** | 28 | 3 | 131 | 7741 | 382 |
| 7 | Paul Collis | **31** | 30 | 1 | 127 | 4396 | 180 |
| 8 | Ronnie Jackson | **30** | 24 | 6 | 177 | 5661 | 258 |
| 9 | Adrian Moon | **27** | 25 | 2 | 170 | 4476 | 190 |
| 10 | Peter Reynolds | **22** | 19 | 3 | 127 | 3630 | 174 |
| 11 | Mickey Callaghan | **20** | 18 | 2 | 103 | 4560 | 227 |
| 12 | Raj Hothi | **18** | 16 | 2 | 105 | 4255 | 241 |
| 13 | David Adkins | **16** | 15 | 1 | 151 | 2662 | 105 |
| 14 | Paul Margiotta | **16** | 15 | 1 | 127 | 2070 | 68 |
| 15 | Ashley Foster | **14** | 13 | 1 | 100 | 2630 | 141 |
| 16 | Nikhil Patel | **13** | 10 | 3 | 121 | 2771 | 203 |
| 17 | Ben Little | **11** | 9 | 2 | 134 | 2916 | 195 |
| 18 | Bobby Little | **11** | 9 | 2 | 115 | 2561 | 176 |
| 19 | Joe Sarro | **11** | 11 | 0 | 77 | 4733 | 294 |
| 20 | James Fuller | **10** | 9 | 1 | 106 | 3269 | 181 |
| 21 | Biren Patel | **10** | 9 | 1 | 102 | 4169 | 341 |
| 22 | Shailendra Rajput | **9** | 6 | 3 | 131 | 935 | 17 |
| 23 | Samir Patel | **8** | 6 | 2 | 123 | 1042 | 37 |
| 24 | Tyler Bunn | **8** | 7 | 1 | 102 | 2257 | 172 |
| 25 | Tom Herbert | **8** | 8 | 0 | 88 | 2314 | 179 |

## 2. Longest streaks of consecutive ducks (out for 0)

Top 15. _Score format_: `runs` (with `*` for not-out). Innings between the first and last in the streak are listed in chronological order.

| # | Player | Streak | Span | Scores |
|--:|--------|------:|------|--------|
| 1 | Luis Hardy | **8** | 19/06/2018 → 12/05/2019 | 0 0 0 0 0 0 0 0 |
| 2 | Elsie Orwell | **6** | 13/08/2017 → 17/06/2018 | 0 0 0 0 0 0 |
| 3 | Billy Purton | **5** | 11/05/2016 → 06/07/2016 | 0 0 0 0 0 |
| 4 | David McCarthy | **5** | 05/08/2018 → 16/06/2019 | 0 0 0 0 0 |
| 5 | Hudson Mccarthy | **5** | 17/05/2025 → 12/07/2025 | 0 0 0 0 0 |
| 6 | Lewis Sullivan | **4** | 31/05/2009 → 15/07/2009 | 0 0 0 0 |
| 7 | T Gwillem | **4** | 29/05/2011 → 21/08/2011 | 0 0 0 0 |
| 8 | Reiss Anatol-Liburd | **4** | 29/05/2011 → 19/06/2011 | 0 0 0 0 |
| 9 | L Maguire | **4** | 03/07/2011 → 31/07/2011 | 0 0 0 0 |
| 10 | Jake Downton | **4** | 12/07/2015 → 27/08/2015 | 0 0 0 0 |
| 11 | Craig Wightman | **4** | 18/06/2013 → 22/06/2014 | 0 0 0 0 |
| 12 | Daniel Purton | **4** | 04/08/2014 → 13/05/2015 | 0 0 0 0 |
| 13 | Flynn Treanor | **4** | 06/07/2016 → 01/05/2017 | 0 0 0 0 |
| 14 | Hayden Musham | **4** | 08/06/2016 → 09/05/2018 | 0 0 0 0 |
| 15 | Holly Vickers | **4** | 03/06/2018 → 13/06/2018 | 0 0 0 0 |

Definition of a duck: batsman dismissed (`how_out` ∈ {ct, b, lbw, run out, st, hit wicket, retired out, pairs inning}) for 0 runs. Not-out 0s and unrecorded dismissals do **not** count as ducks but they **do** break the streak (only _did not bat_ / _absent_ are skipped).

## 3. Longest streaks of consecutive 10+ scores

Top 15. _Score format_: `runs` (with `*` for not-out). Innings between the first and last in the streak are listed in chronological order.

| # | Player | Streak | Span | Scores |
|--:|--------|------:|------|--------|
| 1 | Jas Hothi | **19** | 11/07/2015 → 04/06/2016 | 10 34 48 54* 115 40* 46 23 17 16 33 81 27 26 28 13 19 24 62 |
| 2 | Jon O'Neill | **18** | 15/04/2017 → 24/06/2017 | 58* 17 40 34* 35 33* 21 15* 50 50 10 65 19 50 48 33 39 14 |
| 3 | S Owais | **16** | 30/07/2005 → 12/08/2007 | 20 16 33 47* 19 35 24 18* 11 29* 25 23 37 37 22 89 |
| 4 | Adrian Moon | **13** | 07/07/2007 → 31/05/2008 | 17 15 13* 69* 38 19 37* 26 14* 16 12 73* 13 |
| 5 | Kyan Lehal | **13** | 22/06/2018 → 02/09/2018 | 16 32* 13 30* 28* 25* 22* 19 17* 20 33* 24 22* |
| 6 | Alex Sullivan | **12** | 18/06/2006 → 20/08/2006 | 111* 46 13 73 12 12 40 48 11 14 99 27 |
| 7 | Paul Margiotta | **12** | 02/06/2007 → 28/06/2008 | 14 21 23 45 10 37 12 58 58* 26 84 22 |
| 8 | Shailendra Rajput | **12** | 07/05/2022 → 20/05/2023 | 56 81* 131 48 73 104 104* 18 92* 15 39 53 |
| 9 | David Adkins | **11** | 19/08/2000 → 31/08/2002 | 24 79 10 151* 65* 60 34 46 27 10 10 |
| 10 | Peter Reynolds | **11** | 21/05/2011 → 27/06/2012 | 27 11 80 12 16* 66 43 10 40 27 64 |
| 11 | Garnet Shallow | **11** | 22/04/2006 → 26/05/2012 | 42* 11 48* 48 70 28 35 18* 56* 15 89* |
| 12 | Hari Patel | **11** | 29/05/2021 → 21/08/2021 | 16 12 52* 21 24* 34 10* 17 34* 35 71 |
| 13 | Mayur G Patel | **11** | 15/05/2021 → 17/07/2021 | 24 34 59 32 16 23 57 18 27 47 57* |
| 14 | James Fuller | **10** | 04/07/2010 → 05/09/2010 | 21 19 26 10 10 40 13 15 106* 22 |
| 15 | Kenny Sims | **10** | 09/05/2009 → 25/07/2009 | 11 34 23 27 24 32 12 24* 34 17* |

Definition: an innings counts toward the streak when `runs >= 10`, regardless of how out. _Did not bat_ / _absent_ rows are skipped (they neither extend nor break the streak); any innings under 10 breaks it, including not-outs.
