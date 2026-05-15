# Essex Cricket League — survey for amateur-cricket-modelling

Test run for `PLAN_AMATEUR_MODELLING.md`. Probes scope, BBB coverage,
and video availability on the **top 5 men's 1st-XI divisions** for the
last 10 seasons (2017 – 2026), site_id `7300`.

## Scope

| Year | Divisions found | Matches in universe | Notes |
|---|---|---|---|
| 2017 | 4 | 360 | Time & Overs naming, no Div Four |
| 2018 | 4 | 360 | |
| 2019 | 4 | 360 | |
| 2020 | 1 | 45 | Covid season — single restructured "Premier Tier - Gooch Division" |
| 2021 | 4 | 430 | new naming convention; **Div Three counts 160** (extra fixtures, possibly playoffs) |
| 2022 | 4 | 460 | **Div Three counts 190** — same flag |
| 2023 | 5 | 468 | first year with five divisions; **Div Four counts 108** |
| 2024 | 5 | 450 | clean — exactly 90 per division |
| 2025 | 5 | 450 | clean |
| 2026 | 5 | 450 | clean |
| **Total** | — | **3,833** | vs the planning estimate of ~4,500 |

The plan estimated ~4,500 matches per league (5 divs × 10 seasons × 90).
Reality for Essex is **~3,833** — pulled down by:

- 4 divisions instead of 5 in 2017–2022
- a 45-match Covid 2020
- a few divisions exceeding 90 (160, 190, 108) which need investigating
  — likely playoff brackets or sub-tournaments accidentally tagged
  `competition_type = "League"`

Across 33 leagues, expect **~110-130k matches**, not 148k.

### Top-5 division ids per season

```
2017  prem=71768   d1=71770   d2=71771   d3=71772
2018  prem=79025   d1=79026   d2=79028   d3=79032
2019  prem=84072   d1=84073   d2=84074   d3=84076
2020  gooch=92771                               (single Covid division)
2021  prem=87298   d1=87299   d2=87300   d3=96962
2022  prem=102819  d1=102820  d2=102821  d3=102822
2023  prem=110435  d1=110436  d2=110437  d3=110439  d4=110438
2024  prem=117997  d1=117998  d2=117999  d3=118000  d4=118001
2025  prem=125062  d1=125063  d2=125064  d3=125065  d4=125066
2026  prem=135282  d1=135283  d2=135296  d3=135297  d4=135298
```

`competition_id`s are not stable across seasons — must be re-derived each year.

### Division-name patterns

Two distinct conventions need handling in the universe regex:

```
2017-2019:  "1st XI Premier Division (Time & Overs)"
            "1st XI Division One (Time & Overs)"  ...
2020 only:  "Premier Tier - Gooch Division"          (one-off)
2021-2026:  "Division 01 - 1st XI Premier Division"
            "Division 02 - 1st XI Division One"  ...
```

The `1st XI|Senior|Premier` regex from the plan catches all three, but
ordering by `Division 0X` numeric prefix only works post-2021.
Pre-2021 ordering falls back to text match (`Premier`, `One`, `Two`,
`Three`).

## BBB coverage

50-match stratified sample (10 per season × 2021–2025; 7 from
Premier+Div1, 3 from Div2/3/4). Each match probed with
`fetch_balls.py` against ResultsVault (RV) first, NV Play fallback.

| Year | RV (PCS) | NV (livestream) | None | n |
|---|---|---|---|---|
| 2021 | 1 | 8 | 1 | 10 |
| 2022 | 2 | 8 | 0 | 10 |
| 2023 | 5 | 4 | 1 | 10 |
| 2024 | 7 | 3 | 0 | 10 |
| 2025 | 5 | 5 | 0 | 10 |
| **Total** | **20 (40%)** | **28 (56%)** | **2 (4%)** | **50** |

**~96 % of recent (2021–2025) Essex top-5 1st-XI matches have ball-by-ball
data of some form**, with a near-even split between PCS scoring (RV)
and livestream scoring (NV Play). This is dramatically better than
the working assumption (the existing CLAUDE.md notes Rainham's recent
seasons hit ~17 % BBB coverage, but that's because Rainham's _own_
matches skew heavily toward fixtures the club paper-scored — the Premier
+ Div 1 ladder is much better covered).

Only 2 of 50 matches returned *neither* RV nor NV data. Worth
spot-checking those — they may be rain-offs or fixtures where the
match-detail flow itself was incomplete.

Pre-2021 wasn't sampled because the existing cache showed 0 BBB
matches for 2017–2020 in the Rainham subset, and the API often
returns no RV mapping for those seasons. A separate probe for
2017-2020 Essex Premier would be a useful follow-up; expectation is
single-digit %.

## Video coverage

The big surprise: the **NV Play match payload itself carries video**.
For NV-scored matches the `nv_match/<match_id>.json` file contains:

| Field | Description |
|---|---|
| `Match.LiveStreamEmbedUrl` | YouTube embed URL for the full stream (e.g. `https://www.youtube.com/embed/2qLe269gM3M`) |
| `Match.LiveStreamIsYouTube` / `LiveStreamIsEmbed` / `LiveStreamIsLink` | flags |
| `Match.HasVideo` / `IsVideoDataAvailable` / `IsTelevised` | flags |
| `Innings[].Overs[].Balls[].Video.VideoUrl` | per-ball MP4 clip on `vid2.ecb.nvplay.net/<uuid>/<ball-id>.mp4` |
| `Innings[].Overs[].Balls[].Video.ThumbUrl` | per-ball thumbnail JPG |
| `Innings[].Overs[].Balls[].Video.VideoDateTime` | timestamp |
| `BattingCard[].VideoHowOut` | dismissal-clip identifier |

i.e. for any match scored on NV Play with the live-stream feature
enabled, **the entire video catalogue (one MP4 per legal delivery
plus the YouTube full-stream URL) is already in the data we fetch**.

Of 29 NV-scored matches in the sample:

| Field | Count | % of NV |
|---|---|---|
| `HasVideo == true` | 4 | 14 % |
| `LiveStreamEmbedUrl` set | 1 | 3 % |
| ball-level `VideoUrl` present | 4 | 14 % |

The `HasVideo` flag tracks NV's per-ball MP4 archive on
`vid2.ecb.nvplay.net`. The `LiveStreamEmbedUrl` field tracks the
**full** YouTube/Frogbox stream — only set when the NV scorer
explicitly attached a stream link.

This means the existing manual "hunt for match videos" workflow in
CLAUDE.md becomes mostly redundant for NV-scored fixtures: just read
the field. For PCS-scored (RV) fixtures we still have to YouTube-search
or scrape Frogbox's channel — `api.resultsvault.co.uk` does not
expose a video URL.

Video coverage by year in the sample (n=29):

| Year | NV matches | HasVideo | EmbedUrl |
|---|---|---|---|
| 2021 | 8 | 1 | 0 |
| 2022 | 8 | 1 | 1 |
| 2023 | 5 | 0 | 0 |
| 2024 | 3 | 0 | 0 |
| 2025 | 5 | 2 | 0 |

Sample size is too small for headline numbers; rerun against the
full universe to get a real distribution. But the **shape** of the
data — full-match YouTube embed + per-ball MP4 — is consistent.

## Implications for the plan

1. **BBB at the top of the pyramid is well-served.** A 33-league bulk
   fetch will likely hit 80–90 %+ coverage on Premier + Div 1 from 2021
   onwards. The actual modelling-grade dataset is much richer than I
   was anticipating.

2. **Video is in the NV payload — no scraping required.** Update the
   plan's "Video coverage" section: priority (1) becomes "extract
   `LiveStreamEmbedUrl` and per-ball `VideoUrl` from cached NV
   payloads"; priority (2) `frogbox/youtube search` is the residual
   for **RV-scored** matches only.

3. **Per-ball MP4 clips** open a new modelling axis: dismissal clips,
   boundary clips, and shot-by-shot indexing keyed off `over.ball`.
   Out of scope for v1 of the dataset, but worth a flag.

4. **Universe regex needs two handlers** for Essex (Time & Overs vs
   `Division 0N - 1st XI ...`). Other leagues will have their own
   conventions. Build a small overrides file rather than one giant
   regex.

5. **Bulk-fetch budget.** With ~120k matches × 1 RV-mapping call +
   2 RV-or-NV calls ≈ 350-400k API calls. At 4 workers / 250 ms
   per call that's roughly 7-8 hours of wall time. Feasible
   overnight.

## Suggested next steps

1. Investigate the two flagged sites (`lincspremiercl=30316`,
   `npcl=7301`) to find their historical site_ids.
2. Investigate the inflated-division-count rows (Essex Div 3 in 2021
   = 160 matches, in 2022 = 190 matches) — figure out which fixtures
   are real Saturday league games vs playoff brackets.
3. Build a generic `leagues_universe.py` that does the
   competition-id discovery for any league site.
4. Run an extended Essex BBB probe — full 3,833-match universe,
   not just 50 — to get a ground-truth coverage % per division per
   season.
5. Then run leagues 2-33 in parallel batches.
