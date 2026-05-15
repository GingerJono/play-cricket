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
| `bdpcl` (252) | 1,573 | 1,309 | 1,180 (90%) | 601,066 | 1.9s | 21.7s | 23.7s |
| `bradfordcl` (259) | 2,980 | 2,596 | 2,430 (94%) | 1,167,163 | 0.0s | 33.6s | 33.8s |
| `ccl` (285) | 4,646 | 2,884 | 1,507 (52%) | 701,582 | 0.0s | 2m 01.6s | 2m 01.8s |
| `cheshirecountycl` (7246) | 4,062 | 2,732 | 2,186 (80%) | 1,054,663 | 0.0s | 46.1s | 46.4s |
| `derbyscountylge` (296) | 4,310 | 3,278 | 2,830 (86%) | 1,353,034 | 0.0s | 36.4s | 36.8s |
| `devoncl` (298) | 3,582 | 2,250 | 1,743 (77%) | 829,974 | 0.0s | 45.4s | 45.6s |
| `dorsetcl` (302) | 2,225 | 1,467 | 706 (48%) | 319,763 | 0.0s | 1m 33.5s | 1m 33.6s |
| `eapcl` (305) | 973 | 638 | 575 (90%) | 313,952 | 0.0s | 4.5s | 4.5s |
| `essexcl` (7300) | 2,638 | 2,258 | 1,903 (84%) | 919,813 | 0.0s | 32.2s | 32.4s |
| `gtrmcrcricket` (11685) | 1,370 | 1,370 | 1,113 (81%) | 500,433 | 0.0s | 24.7s | 25.0s |
| `hcpcl` (342) | 1,088 | 900 | 817 (91%) | 423,967 | 0.0s | 3.1s | 3.1s |
| `hertspremiercl` (572) | 1,485 | 1,260 | 1,083 (86%) | 574,903 | 0.0s | 15.8s | 16.1s |
| `huddersfieldcl` (346) | 3,737 | 2,845 | 2,518 (89%) | 1,095,529 | 0.0s | 33.0s | 33.1s |
| `kcl` (362) | 1,800 | 1,530 | 1,178 (77%) | 580,703 | 2.5s | 35.0s | 37.6s |
| `lancashireleague` (10954) | 924 | 924 | 832 (90%) | 380,223 | 0.0s | 6.9s | 7.0s |
| `ldcc` (378) | 2,471 | 1,936 | 1,396 (72%) | 687,368 | 0.0s | 4m 08.8s | 4m 09.0s |
| `leicestershirescl` (370) | 1,056 | 660 | 596 (90%) | 299,212 | 0.0s | 4.3s | 4.6s |
| `lincspremiercl` (30316) | 0 | 0 | 0 (0%) | 0 | 0.0s | 0.1s | 9.4s |
| `middlesexccl` (393) | 2,664 | 2,214 | 1,894 (86%) | 901,031 | 3m 08.7s | 14m 52.2s | 18m 22.5s |
| `ncl` (426) | 4,818 | 2,660 | 1,595 (60%) | 753,655 | 0.0s | 1m 18.5s | 1m 19.2s |
| `nepremierleague` (409) | 3,068 | 2,369 | 1,906 (80%) | 891,592 | 3m 42.1s | 15m 27.9s | 19m 30.9s |
| `nottinghamshirecbpl` (443) | 1,316 | 1,146 | 1,063 (93%) | 534,526 | 0.0s | 7.1s | 7.1s |
| `nssc` (447) | 3,080 | 2,508 | 2,370 (94%) | 1,049,999 | 4m 03.2s | 18m 13.5s | 22m 37.9s |
| `nwcl` (4655) | 3,571 | 2,933 | 1,044 (36%) | 456,792 | 4m 17.2s | 16m 51.1s | 21m 27.6s |
| `nysdl` (441) | 5,494 | 3,345 | 2,827 (85%) | 1,262,254 | 0.1s | 45.5s | 46.2s |
| `spcl` (496) | 3,250 | 1,814 | 1,461 (81%) | 737,360 | 0.0s | 22.5s | 22.6s |
| `surreycricketchampionship` (29012) | 450 | 450 | 400 (89%) | 208,763 | 0.0s | 4.7s | 4.9s |
| `sussexcricketleague` (16378) | 2,610 | 2,250 | 1,693 (75%) | 810,842 | 0.0s | 38.4s | 39.1s |
| `swpcl` (10196) | 1,440 | 900 | 761 (85%) | 371,585 | 0.0s | 6.4s | 6.5s |
| `westofengland` (545) | 3,582 | 2,250 | 1,993 (89%) | 956,367 | 0.0s | 21m 18.3s | 21m 18.6s |
| `ycspl` (22888) | 3,238 | 3,238 | 2,772 (86%) | 1,351,159 | 5m 39.4s | 32m 17.8s | 38m 15.1s |
| `ypln` (8240) | 1,408 | 1,408 | 1,312 (93%) | 660,120 | 1m 60.0s | 9m 33.1s | 12m 00.9s |
| **Total** | **80,909** | **60,322** | **47,684** (79%) | **22,749,393** | – | – | **170m 42.4s** |

## Per-league detail (relaxed filter)

### bdpcl  (site_id=252)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,573** matches
- BBB-era subset (2021+):                                  **1,309** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 1.9s | 2 | 1,571 | 1.1 req/s |
| P4 — BBB fetch              | 21.7s | 1 | 1,179 | 0.0 match/s |
| **Total wall time**         | **23.7s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 44 | 3% |
| NV fetched | 1 | 0% |
| NV cached | 1,135 | 87% |
| no mapping | 0 | 0% |
| no data | 129 | 10% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,180** | **90%** |
| **balls fetched (cumulative)** | **601,066** | – |

### bradfordcl  (site_id=259)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **2,980** matches
- BBB-era subset (2021+):                                  **2,596** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 2,980 | 0.0 req/s |
| P4 — BBB fetch              | 33.6s | 0 | 2,430 | 0.0 match/s |
| **Total wall time**         | **33.8s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 544 | 21% |
| NV fetched | 0 | 0% |
| NV cached | 1,886 | 73% |
| no mapping | 0 | 0% |
| no data | 165 | 6% |
| failed | 1 | 0% |
| **BBB-positive (any source)** | **2,430** | **94%** |
| **balls fetched (cumulative)** | **1,167,163** | – |

### ccl  (site_id=285)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **4,646** matches
- BBB-era subset (2021+):                                  **2,884** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.2s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 4,646 | 0.0 req/s |
| P4 — BBB fetch              | 2m 01.6s | 1 | 1,506 | 0.0 match/s |
| **Total wall time**         | **2m 01.8s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 1 | 0% |
| RV cached | 1,226 | 43% |
| NV fetched | 0 | 0% |
| NV cached | 280 | 10% |
| no mapping | 0 | 0% |
| no data | 1,376 | 48% |
| failed | 1 | 0% |
| **BBB-positive (any source)** | **1,507** | **52%** |
| **balls fetched (cumulative)** | **701,582** | – |

### cheshirecountycl  (site_id=7246)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **4,062** matches
- BBB-era subset (2021+):                                  **2,732** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.2s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 4,062 | 0.0 req/s |
| P4 — BBB fetch              | 46.1s | 0 | 2,186 | 0.0 match/s |
| **Total wall time**         | **46.4s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 1,499 | 55% |
| NV fetched | 0 | 0% |
| NV cached | 687 | 25% |
| no mapping | 0 | 0% |
| no data | 546 | 20% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **2,186** | **80%** |
| **balls fetched (cumulative)** | **1,054,663** | – |

### derbyscountylge  (site_id=296)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **4,310** matches
- BBB-era subset (2021+):                                  **3,278** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.3s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 4,310 | 0.0 req/s |
| P4 — BBB fetch              | 36.4s | 1 | 2,829 | 0.0 match/s |
| **Total wall time**         | **36.8s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 1 | 0% |
| RV cached | 1,255 | 38% |
| NV fetched | 0 | 0% |
| NV cached | 1,574 | 48% |
| no mapping | 0 | 0% |
| no data | 448 | 14% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **2,830** | **86%** |
| **balls fetched (cumulative)** | **1,353,034** | – |

### devoncl  (site_id=298)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,582** matches
- BBB-era subset (2021+):                                  **2,250** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 3,582 | 0.0 req/s |
| P4 — BBB fetch              | 45.4s | 1 | 1,742 | 0.0 match/s |
| **Total wall time**         | **45.6s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 754 | 34% |
| NV fetched | 1 | 0% |
| NV cached | 988 | 44% |
| no mapping | 0 | 0% |
| no data | 507 | 23% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,743** | **77%** |
| **balls fetched (cumulative)** | **829,974** | – |

### dorsetcl  (site_id=302)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **2,225** matches
- BBB-era subset (2021+):                                  **1,467** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 2,225 | 0.0 req/s |
| P4 — BBB fetch              | 1m 33.5s | 0 | 706 | 0.0 match/s |
| **Total wall time**         | **1m 33.6s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 647 | 44% |
| NV fetched | 0 | 0% |
| NV cached | 59 | 4% |
| no mapping | 0 | 0% |
| no data | 761 | 52% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **706** | **48%** |
| **balls fetched (cumulative)** | **319,763** | – |

### eapcl  (site_id=305)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **973** matches
- BBB-era subset (2021+):                                  **638** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.0s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 973 | 0.0 req/s |
| P4 — BBB fetch              | 4.5s | 0 | 575 | 0.0 match/s |
| **Total wall time**         | **4.5s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 82 | 13% |
| NV fetched | 0 | 0% |
| NV cached | 493 | 77% |
| no mapping | 0 | 0% |
| no data | 63 | 10% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **575** | **90%** |
| **balls fetched (cumulative)** | **313,952** | – |

### essexcl  (site_id=7300)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **2,638** matches
- BBB-era subset (2021+):                                  **2,258** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.2s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 2,638 | 0.0 req/s |
| P4 — BBB fetch              | 32.2s | 0 | 1,903 | 0.0 match/s |
| **Total wall time**         | **32.4s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 1,064 | 47% |
| NV fetched | 0 | 0% |
| NV cached | 839 | 37% |
| no mapping | 0 | 0% |
| no data | 355 | 16% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,903** | **84%** |
| **balls fetched (cumulative)** | **919,813** | – |

### gtrmcrcricket  (site_id=11685)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,370** matches
- BBB-era subset (2021+):                                  **1,370** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.2s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 1,370 | 0.0 req/s |
| P4 — BBB fetch              | 24.7s | 2 | 1,111 | 0.1 match/s |
| **Total wall time**         | **25.0s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 2 | 0% |
| RV cached | 1,074 | 78% |
| NV fetched | 0 | 0% |
| NV cached | 37 | 3% |
| no mapping | 0 | 0% |
| no data | 257 | 19% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,113** | **81%** |
| **balls fetched (cumulative)** | **500,433** | – |

### hcpcl  (site_id=342)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,088** matches
- BBB-era subset (2021+):                                  **900** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.0s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 1,088 | 0.0 req/s |
| P4 — BBB fetch              | 3.1s | 0 | 817 | 0.0 match/s |
| **Total wall time**         | **3.1s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 124 | 14% |
| NV fetched | 0 | 0% |
| NV cached | 693 | 77% |
| no mapping | 0 | 0% |
| no data | 83 | 9% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **817** | **91%** |
| **balls fetched (cumulative)** | **423,967** | – |

### hertspremiercl  (site_id=572)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,485** matches
- BBB-era subset (2021+):                                  **1,260** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.3s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 1,485 | 0.0 req/s |
| P4 — BBB fetch              | 15.8s | 1 | 1,082 | 0.1 match/s |
| **Total wall time**         | **16.1s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 178 | 14% |
| NV fetched | 1 | 0% |
| NV cached | 904 | 72% |
| no mapping | 0 | 0% |
| no data | 177 | 14% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,083** | **86%** |
| **balls fetched (cumulative)** | **574,903** | – |

### huddersfieldcl  (site_id=346)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,737** matches
- BBB-era subset (2021+):                                  **2,845** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 3,737 | 0.0 req/s |
| P4 — BBB fetch              | 33.0s | 0 | 2,518 | 0.0 match/s |
| **Total wall time**         | **33.1s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 2,413 | 85% |
| NV fetched | 0 | 0% |
| NV cached | 105 | 4% |
| no mapping | 0 | 0% |
| no data | 327 | 11% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **2,518** | **89%** |
| **balls fetched (cumulative)** | **1,095,529** | – |

### kcl  (site_id=362)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,800** matches
- BBB-era subset (2021+):                                  **1,530** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.2s | 0 | – | – |
| P3 — match_detail backfill  | 2.5s | 1 | 1,799 | 0.4 req/s |
| P4 — BBB fetch              | 35.0s | 1 | 1,177 | 0.0 match/s |
| **Total wall time**         | **37.6s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 824 | 54% |
| NV fetched | 1 | 0% |
| NV cached | 353 | 23% |
| no mapping | 0 | 0% |
| no data | 352 | 23% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,178** | **77%** |
| **balls fetched (cumulative)** | **580,703** | – |

### lancashireleague  (site_id=10954)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **924** matches
- BBB-era subset (2021+):                                  **924** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 924 | 0.0 req/s |
| P4 — BBB fetch              | 6.9s | 0 | 832 | 0.0 match/s |
| **Total wall time**         | **7.0s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 572 | 62% |
| NV fetched | 0 | 0% |
| NV cached | 260 | 28% |
| no mapping | 0 | 0% |
| no data | 92 | 10% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **832** | **90%** |
| **balls fetched (cumulative)** | **380,223** | – |

### ldcc  (site_id=378)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **2,471** matches
- BBB-era subset (2021+):                                  **1,936** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 2,471 | 0.0 req/s |
| P4 — BBB fetch              | 4m 08.8s | 462 | 934 | 1.9 match/s |
| **Total wall time**         | **4m 09.0s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 344 | 18% |
| RV cached | 604 | 31% |
| NV fetched | 118 | 6% |
| NV cached | 330 | 17% |
| no mapping | 0 | 0% |
| no data | 540 | 28% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,396** | **72%** |
| **balls fetched (cumulative)** | **687,368** | – |

### leicestershirescl  (site_id=370)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,056** matches
- BBB-era subset (2021+):                                  **660** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.2s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 1,056 | 0.0 req/s |
| P4 — BBB fetch              | 4.3s | 0 | 596 | 0.0 match/s |
| **Total wall time**         | **4.6s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 331 | 50% |
| NV fetched | 0 | 0% |
| NV cached | 265 | 40% |
| no mapping | 0 | 0% |
| no data | 64 | 10% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **596** | **90%** |
| **balls fetched (cumulative)** | **299,212** | – |

### lincspremiercl  (site_id=30316)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **0** matches
- BBB-era subset (2021+):                                  **0** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 9.3s | up to 10 | – | – |
| P2 — universe filter        | 0.0s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 0 | 0.0 req/s |
| P4 — BBB fetch              | 0.1s | 0 | 0 | 0.0 match/s |
| **Total wall time**         | **9.4s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 0 | 0% |
| NV fetched | 0 | 0% |
| NV cached | 0 | 0% |
| no mapping | 0 | 0% |
| no data | 0 | 0% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **0** | **0%** |
| **balls fetched (cumulative)** | **0** | – |

### middlesexccl  (site_id=393)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **2,664** matches
- BBB-era subset (2021+):                                  **2,214** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 21.4s | up to 10 | – | – |
| P2 — universe filter        | 0.2s | 0 | – | – |
| P3 — match_detail backfill  | 3m 08.7s | 2,664 | 0 | 14.1 req/s |
| P4 — BBB fetch              | 14m 52.2s | 1,894 | 0 | 2.1 match/s |
| **Total wall time**         | **18m 22.5s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 1,281 | 58% |
| RV cached | 0 | 0% |
| NV fetched | 613 | 28% |
| NV cached | 0 | 0% |
| no mapping | 0 | 0% |
| no data | 319 | 14% |
| failed | 1 | 0% |
| **BBB-positive (any source)** | **1,894** | **86%** |
| **balls fetched (cumulative)** | **901,031** | – |

### ncl  (site_id=426)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **4,818** matches
- BBB-era subset (2021+):                                  **2,660** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.7s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 4,818 | 0.0 req/s |
| P4 — BBB fetch              | 1m 18.5s | 0 | 1,595 | 0.0 match/s |
| **Total wall time**         | **1m 19.2s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 962 | 36% |
| NV fetched | 0 | 0% |
| NV cached | 633 | 24% |
| no mapping | 0 | 0% |
| no data | 1,065 | 40% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,595** | **60%** |
| **balls fetched (cumulative)** | **753,655** | – |

### nepremierleague  (site_id=409)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,068** matches
- BBB-era subset (2021+):                                  **2,369** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 20.8s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 3m 42.1s | 3,068 | 0 | 13.8 req/s |
| P4 — BBB fetch              | 15m 27.9s | 1,906 | 0 | 2.1 match/s |
| **Total wall time**         | **19m 30.9s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 1,197 | 51% |
| RV cached | 0 | 0% |
| NV fetched | 709 | 30% |
| NV cached | 0 | 0% |
| no mapping | 0 | 0% |
| no data | 463 | 20% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,906** | **80%** |
| **balls fetched (cumulative)** | **891,592** | – |

### nottinghamshirecbpl  (site_id=443)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,316** matches
- BBB-era subset (2021+):                                  **1,146** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.0s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 1,316 | 0.0 req/s |
| P4 — BBB fetch              | 7.1s | 0 | 1,063 | 0.0 match/s |
| **Total wall time**         | **7.1s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 290 | 25% |
| NV fetched | 0 | 0% |
| NV cached | 773 | 67% |
| no mapping | 0 | 0% |
| no data | 83 | 7% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,063** | **93%** |
| **balls fetched (cumulative)** | **534,526** | – |

### nssc  (site_id=447)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,080** matches
- BBB-era subset (2021+):                                  **2,508** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 21.1s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 4m 03.2s | 3,080 | 0 | 12.7 req/s |
| P4 — BBB fetch              | 18m 13.5s | 2,370 | 0 | 2.2 match/s |
| **Total wall time**         | **22m 37.9s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 1,229 | 49% |
| RV cached | 0 | 0% |
| NV fetched | 1,141 | 45% |
| NV cached | 0 | 0% |
| no mapping | 0 | 0% |
| no data | 138 | 6% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **2,370** | **94%** |
| **balls fetched (cumulative)** | **1,049,999** | – |

### nwcl  (site_id=4655)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,571** matches
- BBB-era subset (2021+):                                  **2,933** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 19.2s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 4m 17.2s | 3,571 | 0 | 13.9 req/s |
| P4 — BBB fetch              | 16m 51.1s | 1,044 | 0 | 1.0 match/s |
| **Total wall time**         | **21m 27.6s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 720 | 25% |
| RV cached | 0 | 0% |
| NV fetched | 324 | 11% |
| NV cached | 0 | 0% |
| no mapping | 0 | 0% |
| no data | 1,889 | 64% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,044** | **36%** |
| **balls fetched (cumulative)** | **456,792** | – |

### nysdl  (site_id=441)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **5,494** matches
- BBB-era subset (2021+):                                  **3,345** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.6s | 0 | – | – |
| P3 — match_detail backfill  | 0.1s | 0 | 5,494 | 0.0 req/s |
| P4 — BBB fetch              | 45.5s | 0 | 2,827 | 0.0 match/s |
| **Total wall time**         | **46.2s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 2,553 | 76% |
| NV fetched | 0 | 0% |
| NV cached | 274 | 8% |
| no mapping | 0 | 0% |
| no data | 518 | 15% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **2,827** | **85%** |
| **balls fetched (cumulative)** | **1,262,254** | – |

### spcl  (site_id=496)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,250** matches
- BBB-era subset (2021+):                                  **1,814** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 3,250 | 0.0 req/s |
| P4 — BBB fetch              | 22.5s | 0 | 1,461 | 0.0 match/s |
| **Total wall time**         | **22.6s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 733 | 40% |
| NV fetched | 0 | 0% |
| NV cached | 728 | 40% |
| no mapping | 0 | 0% |
| no data | 353 | 19% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,461** | **81%** |
| **balls fetched (cumulative)** | **737,360** | – |

### surreycricketchampionship  (site_id=29012)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **450** matches
- BBB-era subset (2021+):                                  **450** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 450 | 0.0 req/s |
| P4 — BBB fetch              | 4.7s | 0 | 400 | 0.0 match/s |
| **Total wall time**         | **4.9s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 262 | 58% |
| NV fetched | 0 | 0% |
| NV cached | 138 | 31% |
| no mapping | 0 | 0% |
| no data | 50 | 11% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **400** | **89%** |
| **balls fetched (cumulative)** | **208,763** | – |

### sussexcricketleague  (site_id=16378)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **2,610** matches
- BBB-era subset (2021+):                                  **2,250** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.7s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 2,610 | 0.0 req/s |
| P4 — BBB fetch              | 38.4s | 0 | 1,693 | 0.0 match/s |
| **Total wall time**         | **39.1s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 887 | 39% |
| NV fetched | 0 | 0% |
| NV cached | 806 | 36% |
| no mapping | 0 | 0% |
| no data | 557 | 25% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,693** | **75%** |
| **balls fetched (cumulative)** | **810,842** | – |

### swpcl  (site_id=10196)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,440** matches
- BBB-era subset (2021+):                                  **900** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.0s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 1,440 | 0.0 req/s |
| P4 — BBB fetch              | 6.4s | 0 | 761 | 0.0 match/s |
| **Total wall time**         | **6.5s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 0 | 0% |
| RV cached | 131 | 15% |
| NV fetched | 0 | 0% |
| NV cached | 630 | 70% |
| no mapping | 0 | 0% |
| no data | 139 | 15% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **761** | **85%** |
| **balls fetched (cumulative)** | **371,585** | – |

### westofengland  (site_id=545)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,582** matches
- BBB-era subset (2021+):                                  **2,250** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 0.0s | up to 10 | – | – |
| P2 — universe filter        | 0.3s | 0 | – | – |
| P3 — match_detail backfill  | 0.0s | 0 | 3,582 | 0.0 req/s |
| P4 — BBB fetch              | 21m 18.3s | 1,902 | 91 | 1.5 match/s |
| **Total wall time**         | **21m 18.6s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 485 | 22% |
| RV cached | 15 | 1% |
| NV fetched | 1,417 | 63% |
| NV cached | 76 | 3% |
| no mapping | 0 | 0% |
| no data | 257 | 11% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,993** | **89%** |
| **balls fetched (cumulative)** | **956,367** | – |

### ycspl  (site_id=22888)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **3,238** matches
- BBB-era subset (2021+):                                  **3,238** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 17.8s | up to 10 | – | – |
| P2 — universe filter        | 0.1s | 0 | – | – |
| P3 — match_detail backfill  | 5m 39.4s | 3,238 | 0 | 9.5 req/s |
| P4 — BBB fetch              | 32m 17.8s | 2,772 | 0 | 1.4 match/s |
| **Total wall time**         | **38m 15.1s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 757 | 23% |
| RV cached | 0 | 0% |
| NV fetched | 2,015 | 62% |
| NV cached | 0 | 0% |
| no mapping | 0 | 0% |
| no data | 466 | 14% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **2,772** | **86%** |
| **balls fetched (cumulative)** | **1,351,159** | – |

### ypln  (site_id=8240)

- Universe (top-5 1st-XI Limited Overs, 2017-2026): **1,408** matches
- BBB-era subset (2021+):                                  **1,408** matches

| Phase | Wall time | New API calls | From cache | Throughput |
|---|---:|---:|---:|---:|
| P1 — season summaries (×10) | 27.7s | up to 10 | – | – |
| P2 — universe filter        | 0.2s | 0 | – | – |
| P3 — match_detail backfill  | 1m 60.0s | 1,408 | 0 | 11.7 req/s |
| P4 — BBB fetch              | 9m 33.1s | 1,312 | 0 | 2.3 match/s |
| **Total wall time**         | **12m 00.9s** | – | – | – |

**BBB outcome breakdown** (across all 2021+ universe matches):

| outcome | n | % |
|---|---:|---:|
| RV fetched | 886 | 63% |
| RV cached | 0 | 0% |
| NV fetched | 426 | 30% |
| NV cached | 0 | 0% |
| no mapping | 0 | 0% |
| no data | 96 | 7% |
| failed | 0 | 0% |
| **BBB-positive (any source)** | **1,312** | **93%** |
| **balls fetched (cumulative)** | **660,120** | – |

## Extrapolation to all 33 ECB Premier Leagues

Across the three benchmarked leagues (relaxed filter):
- Mean universe: **~2,528** matches
- Mean BBB-era:  **~1,885** matches
- Mean wall (P1-P4 first run): **5m 20.1s**

Surrey site_id 29012 only carries 2025-2026 data on the
Play-Cricket league site (older seasons live elsewhere), so its
universe is much smaller than its actual 10-year footprint —
the means above are pulled down by that. Excluding Surrey:
- Mean universe: **~2,528** matches
- Mean BBB-era:  **~1,885** matches
- Mean wall:     **5m 20.1s**

**Naive extrapolation (Essex + Herts only) to 33 leagues:**

- Total universe: ~83,437 matches
- Total BBB-era:  ~62,207 matches
- Total wall:     ~176m 02.5s of API time

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
