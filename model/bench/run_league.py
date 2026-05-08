#!/usr/bin/env python3
"""
Per-league benchmark: drive the universe scrape end-to-end and time each
phase. Reuses the existing fetch.* / fetch_balls.cache_match functions —
this script is a benchmark harness, not a new fetcher.

Phases per league:
  P1  Season summaries          (10 seasons of matches.json)
  P2  Universe filter           (top-5 1st-XI Limited Overs divisions)
  P3  match_detail backfill     (one API call per universe match)
  P4  BBB fetch                 (RV with NV fallback, 2021+ only)

Outputs:
  model/bench/<slug>.json       per-league timings + counts
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import fetch as F                                # noqa: E402
import fetch_balls as FB                         # noqa: E402

OUT = Path(__file__).resolve().parent

EXCL = re.compile(
    r"\b(2nd XI|3rd XI|4th XI|Reserve|Sunday|T20|Twenty20|20-over|20/20|"
    r"U1[1-9]|Junior|Women|Ladies|Girls|Indoor|Smash)\b",
    re.I,
)


def tier_score(name: str) -> int:
    n = name.lower()
    if "premier" in n:
        return 0
    m = re.search(r"div(?:ision)?\s*0*(\d+)", n)
    if m:
        return int(m.group(1))
    return 99


def discover_universe(site_id: int, seasons: list[int], strict_senior_filter: bool = False) -> dict:
    """
    Returns:
        {
          'top5_per_season': {season: [{tier, cid, name, n}]},
          'universe_match_ids': [int],
          'bbb_match_ids': [int]   # subset, 2021+ only, for BBB phase
        }

    `strict_senior_filter=True` keeps the original Essex inclusion filter
    (`1st XI|Senior|Premier`), which only matches leagues whose division
    names carry that prefix. The default (False) relies on EXCL alone —
    which is the correct generalisation for leagues like Surrey that
    name their divisions `Premier Division`, `Division 1`, `Division 2`
    etc. without the `1st XI` prefix.
    """
    by_season_top5 = {}
    universe_ids: set[int] = set()
    bbb_ids: set[int] = set()
    for s in seasons:
        # ensure cached
        path = F.fetch_season(site_id, s, force=False)
        matches = json.loads(path.read_text()).get("matches", [])
        # competition counts (League only, not the excluded patterns)
        comp_counts: dict[int, int] = {}
        comp_meta: dict[int, str] = {}
        for m in matches:
            if m.get("competition_type") != "League":
                continue
            if m.get("match_type") != "Limited Overs":
                # Declaration / timed are out of scope per the model plan
                continue
            cname = m.get("competition_name") or ""
            if EXCL.search(cname):
                continue
            cid = m.get("competition_id")
            if not cid:
                continue
            comp_counts[cid] = comp_counts.get(cid, 0) + 1
            comp_meta[cid] = cname
        # senior pattern (optional)
        senior = []
        for cid, n in comp_counts.items():
            cname = comp_meta[cid]
            if strict_senior_filter and not re.search(r"1st XI|Senior|Premier", cname, re.I):
                continue
            senior.append((tier_score(cname), cid, cname, n))
        senior.sort()
        top5 = senior[:5]
        by_season_top5[s] = [
            {"tier": t, "cid": cid, "name": name, "n": n}
            for (t, cid, name, n) in top5
        ]
        cids = {c[1] for c in top5}
        for m in matches:
            if m.get("competition_id") in cids and m.get("match_type") == "Limited Overs":
                mid = int(m["id"])
                universe_ids.add(mid)
                if int(s) >= 2021:
                    bbb_ids.add(mid)
    return {
        "top5_per_season": by_season_top5,
        "universe_match_ids": sorted(universe_ids),
        "bbb_match_ids": sorted(bbb_ids),
    }


def parallel_fetch_details(match_ids: list[int], workers: int = 8) -> dict:
    """Pull missing match_detail.json files. Returns {fetched, cached, failed}."""
    todo = [
        m for m in match_ids
        if not (F.MATCH_DETAIL_DIR / f"{m}.json").exists()
    ]
    cached_n = len(match_ids) - len(todo)
    failed = 0
    fetched = 0
    if not todo:
        return {"fetched": 0, "cached": cached_n, "failed": 0, "total": len(match_ids)}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(F.fetch_match_detail, mid, False): mid for mid in todo}
        for fut in as_completed(futs):
            try:
                fut.result()
                fetched += 1
            except Exception:
                failed += 1
    return {"fetched": fetched, "cached": cached_n, "failed": failed, "total": len(match_ids)}


def parallel_fetch_bbb(match_ids: list[int], workers: int = 4) -> dict:
    """Pull RV+NV BBB for each match. Returns {fetched, cached, no_data, failed}."""
    headers = FB.auth_headers()  # one auth handshake for the whole batch
    counts = {
        "rv_fetched": 0, "rv_cached": 0,
        "nv_fetched": 0, "nv_cached": 0,
        "no_mapping": 0, "no_data": 0,
        "failed": 0,
        "balls_total": 0,
        "total_matches": len(match_ids),
    }

    def _one(mid: int) -> tuple[int, str]:
        try:
            return FB.cache_match(mid, force=False, headers=headers)
        except Exception as e:
            return (0, f"err:{type(e).__name__}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, mid): mid for mid in match_ids}
        for fut in as_completed(futs):
            mid = futs[fut]
            n_balls, status = fut.result()
            counts["balls_total"] += n_balls
            if status == "fetched":
                counts["rv_fetched"] += 1
            elif status == "cached":
                counts["rv_cached"] += 1
            elif status == "nv-fetched":
                counts["nv_fetched"] += 1
            elif status == "nv-cached":
                counts["nv_cached"] += 1
            elif status == "no-mapping":
                counts["no_mapping"] += 1
            elif status == "no-data":
                counts["no_data"] += 1
            else:
                counts["failed"] += 1
    return counts


def run_league(slug: str, site_id: int, workers_detail: int = 8, workers_bbb: int = 4,
               strict_senior_filter: bool = False) -> dict:
    seasons = list(range(2017, 2027))
    print(f"\n========= {slug}  (site_id={site_id}, strict={strict_senior_filter}) =========", flush=True)

    out = {
        "slug": slug,
        "site_id": site_id,
        "strict_senior_filter": strict_senior_filter,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phases": {},
    }

    # P1: summaries
    print("[P1] season summaries (10 seasons) ...", flush=True)
    t0 = time.perf_counter()
    for s in seasons:
        F.fetch_season(site_id, s, force=False)
    out["phases"]["p1_summaries_secs"] = round(time.perf_counter() - t0, 2)
    print(f"     done in {out['phases']['p1_summaries_secs']}s", flush=True)

    # P2: universe filter
    print("[P2] universe filter (top-5 1st-XI Limited Overs) ...", flush=True)
    t0 = time.perf_counter()
    u = discover_universe(site_id, seasons, strict_senior_filter=strict_senior_filter)
    out["phases"]["p2_universe_secs"] = round(time.perf_counter() - t0, 2)
    out["universe_n"] = len(u["universe_match_ids"])
    out["bbb_universe_n"] = len(u["bbb_match_ids"])
    out["divisions_per_season"] = {
        str(s): len(v) for s, v in u["top5_per_season"].items()
    }
    print(f"     universe={out['universe_n']}  bbb-era={out['bbb_universe_n']}  ({out['phases']['p2_universe_secs']}s)", flush=True)

    # P3: match_detail backfill
    print(f"[P3] match_detail backfill ({workers_detail} workers) ...", flush=True)
    t0 = time.perf_counter()
    p3 = parallel_fetch_details(u["universe_match_ids"], workers=workers_detail)
    out["phases"]["p3_match_detail_secs"] = round(time.perf_counter() - t0, 2)
    out["phases"]["p3_counts"] = p3
    rate = p3["fetched"] / out["phases"]["p3_match_detail_secs"] if out["phases"]["p3_match_detail_secs"] > 0 and p3["fetched"] else 0
    print(f"     fetched={p3['fetched']}  cached={p3['cached']}  failed={p3['failed']}  ({out['phases']['p3_match_detail_secs']}s, {rate:.1f}/s)", flush=True)

    # P4: BBB fetch (BBB-era only)
    print(f"[P4] BBB fetch ({workers_bbb} workers, 2021+ only) ...", flush=True)
    t0 = time.perf_counter()
    p4 = parallel_fetch_bbb(u["bbb_match_ids"], workers=workers_bbb)
    out["phases"]["p4_bbb_secs"] = round(time.perf_counter() - t0, 2)
    out["phases"]["p4_counts"] = p4
    p4_fetched = p4["rv_fetched"] + p4["nv_fetched"]
    rate = p4_fetched / out["phases"]["p4_bbb_secs"] if out["phases"]["p4_bbb_secs"] > 0 and p4_fetched else 0
    print(f"     rv-fetched={p4['rv_fetched']}  rv-cached={p4['rv_cached']}  nv-fetched={p4['nv_fetched']}  nv-cached={p4['nv_cached']}  no-data={p4['no_data']}  failed={p4['failed']}", flush=True)
    print(f"     balls_total={p4['balls_total']}  ({out['phases']['p4_bbb_secs']}s, {rate:.1f}/s for newly-fetched)", flush=True)

    # totals
    out["phases"]["wall_secs"] = sum(
        v for k, v in out["phases"].items() if k.endswith("_secs")
    )
    out["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    out_path = OUT / f"{slug}.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"     wrote {out_path}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--site-id", type=int, required=True)
    ap.add_argument("--workers-detail", type=int, default=8)
    ap.add_argument("--workers-bbb", type=int, default=4)
    ap.add_argument("--strict-senior-filter", action="store_true",
                    help="require '1st XI|Senior|Premier' in division name "
                         "(default: rely on EXCL alone, the better generaliser)")
    args = ap.parse_args()
    run_league(args.slug, args.site_id, args.workers_detail, args.workers_bbb,
               args.strict_senior_filter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
