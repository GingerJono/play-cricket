# Bench — multi-league universe scrape

Wall-clock timings for the 4 phases in `run_league.py`,
running on this laptop / runner against the live
Play-Cricket API. Cache hits are essentially free; the
throughput numbers count freshly-fetched calls only.

Universe = **top-5 1st-XI Limited Overs** divisions across
the 10 seasons 2017-2026. The BBB phase narrows further to
the 2021+ era where ball-by-ball coverage is meaningful.

## Headline (canonical / relaxed-filter runs)

| League | Universe | BBB-era | BBB-positive | Balls | P3 | P4 | Wall total |
|---|---:|---:|---:|---:|---:|---:|---:|
| `essex` (7300) | 3,181 | 2,708 | 1,902 (70%) | 919,125 | 4m 04.3s | 17m 23.1s | 21m 28.3s |
| `herts` (572) | 2,980 | 2,045 | 1,278 (62%) | 653,387 | 2m 21.8s | 10m 37.5s | 12m 59.7s |
| `surrey` (29012) | 900 | 900 | 400 (44%) | 208,763 | 48.5s | 3m 47.9s | 4m 36.6s |
| **Total** | **7,061** | **5,653** | **3,580** (63%) | **1,781,275** | – | – | **39m 04.5s** |

## Filter sensitivity: strict vs relaxed

The original `1st XI|Senior|Premier` inclusion regex undercounts
leagues that name divisions plainly (`Division 1`, `Division 2 A/B`,
etc.) without the `1st XI` prefix. Most ECB Premier League sites
use that plainer convention. The relaxed filter (EXCL alone) is the
correct generaliser.

| League | Strict universe | Relaxed universe | × |
|---|---:|---:|---:|
| `herts` (572) | 692 | 2,980 | 4.3× |
| `surrey` (29012) | 180 | 900 | 5.0× |

## Per-league detail (relaxed filter)

### essex  (site_id=7300)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,181** matches
- BBB-era subset (2021+):                                  **2,708** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.8s | 0 | – | – |
| P3 — match_detail backfill  | 4m 04.3s | 2,914 | 266 | 11.9 req/s |
| P4 — BBB fetch              | 17m 23.1s | 1,782 | 120 | 1.7 match/s |
| **Total wall time**         | **21m 28.3s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 1,007 | 37% |
| RV cached | 57 | 2% |
| NV fetched | 775 | 29% |
| NV cached | 63 | 2% |
| no mapping | 0 | 0% |
| no data | 805 | 30% |
| failed | 1 | 0% |
| **BBB-positive (any source)** | **1,902** | **70%** |
| **balls fetched (cumulative)** | **919,125** | – |

### herts  (site_id=572)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **2,980** matches
- BBB-era subset (2021+):                                  **2,045** matches
- _Strict-filter comparison_:                              692 universe / 540 BBB-era (23% of relaxed)

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.3s | 0 | – | – |
| P3 — match_detail backfill  | 2m 21.8s | 2,288 | 692 | 16.1 req/s |
| P4 — BBB fetch              | 10m 37.5s | 876 | 402 | 1.4 match/s |
| **Total wall time**         | **12m 59.7s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 359 | 18% |
| RV cached | 32 | 2% |
| NV fetched | 517 | 25% |
| NV cached | 370 | 18% |
| no mapping | 0 | 0% |
| no data | 767 | 38% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,278** | **62%** |
| **balls fetched (cumulative)** | **653,387** | – |

### surrey  (site_id=29012)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **900** matches
- BBB-era subset (2021+):                                  **900** matches
- _Strict-filter comparison_:                              180 universe / 180 BBB-era (20% of relaxed)

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 48.5s | 720 | 180 | 14.8 req/s |
| P4 — BBB fetch              | 3m 47.9s | 315 | 85 | 1.4 match/s |
| **Total wall time**         | **4m 36.6s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 242 | 27% |
| RV cached | 20 | 2% |
| NV fetched | 73 | 8% |
| NV cached | 65 | 7% |
| no mapping | 0 | 0% |
| no data | 500 | 56% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **400** | **44%** |
| **balls fetched (cumulative)** | **208,763** | – |

## Extrapolation to all 33 ECB Premier Leagues

Across the three benchmarked leagues (relaxed filter):
- Mean universe: **~2,354** matches
- Mean BBB-era:  **~1,884** matches
- Mean wall (P1-P4 first run): **13m 01.5s**

Surrey site_id 29012 only carries 2025-2026 data on the
Play-Cricket league site (older seasons live elsewhere), so its
universe is much smaller than its actual 10-year footprint —
the means above are pulled down by that. Excluding Surrey:
- Mean universe: **~3,080** matches
- Mean BBB-era:  **~2,376** matches
- Mean wall:     **17m 14.0s**

**Naive extrapolation (Essex + Herts only) to 33 leagues:**

- Total universe: ~101,656 matches
- Total BBB-era:  ~78,424 matches
- Total wall:     ~568m 41.2s of API time

Wall is roughly linear in BBB-positive count (~1.5 match/s
at 4 BBB workers). At higher worker count it bottlenecks on
the API, not the laptop, so the practical floor for a
from-scratch national scrape sits around 6-10 hours.

## Caveats

- All three runs were **first-time fetches** (no warm cache).
  Subsequent re-runs hit the idempotency check and are
  effectively free — we already see this on the strict→relaxed
  re-runs where match_details overlapped.
- BBB coverage on the wider universe (60-70%) is meaningfully
  lower than the earlier 50-match Essex sample (96%) because
  the wider universe includes 2026 future fixtures (no BBB
  yet) and lower-tier divisions that PCS-score less reliably.
- Surrey site_id 29012 is a recent rebrand — historical
  seasons live elsewhere. Same flag applies to lincspremiercl
  / npcl per `PLAN_AMATEUR_MODELLING.md`.
