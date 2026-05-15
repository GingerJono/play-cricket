#!/usr/bin/env python3
"""
Per-league benchmark: drive the universe scrape end-to-end and time each
phase. The universe is taken from `model/bench/universe.json` (scraped from
each league's homepage by `scrape_universe.py`); this script is the API
client + benchmark harness.

Phases per league:
  P1  Season summaries          (10 seasons of matches.json)
  P2  Universe filter           (top-N senior divisions per universe.json)
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
UNIVERSE_PATH = OUT / "universe.json"


# ---------- name canonicalisation -------------------------------------------

EXCL = re.compile(
    r"\b("
    r"2nd XI|3rd XI|4th XI|5th XI|Reserve|"
    r"Sunday|Midweek|Evening|"
    r"T20|Twenty20|20-over|20/20|Smash|Blast|"
    r"U1[1-9]|Under\s*1[1-9]|Junior|"
    r"Women|Ladies|Girls|Female|Softball|"
    r"Indoor|Winter"
    r")\b",
    re.I,
)

# Tokens we strip when computing a tier_key. Order matters — apply prefixes
# first, then the inner cleaners, then trim.
NUMERIC_PREFIX = re.compile(r"^\s*Division\s*0?\d+\s*-\s*", re.I)
SPONSOR_PREFIX = re.compile(r"^\s*[\w&\.,'\(\) ]+?\s*-\s*(?=[A-Z])")
PARENS = re.compile(r"\([^)]*\)")
LEADING_KEYWORDS = re.compile(r"^\s*(GMCL|ECB|JW Lees|JWL|1st XI)\s+", re.I)
TRAILING_1ST_XI = re.compile(r"\s+1st XI\s*$", re.I)


def tier_key(name: str) -> str:
    """Reduce a competition_name to a stable tier identifier."""
    s = name
    # strip parenthesised qualifier ("(Time & Overs)", "(50/50)", ...)
    s = PARENS.sub(" ", s)
    # strip "Division 0N - " numeric prefix Essex-style
    m = NUMERIC_PREFIX.match(s)
    if m:
        s = s[m.end():]
    # strip sponsor-prefix style ("Beechwood Mazda - ", "Geoff Cox Cars - ")
    # heuristic: there's a " - " separator and the first segment isn't itself
    # a "Division N" string
    if " - " in s and not re.match(r"^\s*(Division|Premier)\b", s, re.I):
        s = s.split(" - ", 1)[1]
    # strip common league-name leading keywords (GMCL, ECB, JW Lees, etc.)
    for _ in range(3):
        prev = s
        s = LEADING_KEYWORDS.sub("", s).strip()
        if s == prev:
            break
    # strip trailing "1st XI"
    s = TRAILING_1ST_XI.sub("", s)
    # squeeze whitespace
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


# ---------- universe lookup ---------------------------------------------------

def load_universe(site_id: int) -> dict:
    if not UNIVERSE_PATH.exists():
        raise SystemExit(
            f"missing {UNIVERSE_PATH} — run scrape_universe.py first"
        )
    u = json.loads(UNIVERSE_PATH.read_text())
    for entry in u.values():
        if entry.get("site_id") == site_id:
            if entry.get("error"):
                raise SystemExit(f"site_id {site_id} universe scrape failed: {entry['error']}")
            return entry
    raise SystemExit(f"site_id {site_id} not found in universe.json")


def discover_universe_for_league(slug: str, site_id: int, seasons: list[int]) -> dict:
    """
    Universe per (slug, season) using:
      - 2026: exact cid match against universe.json top_n.
      - earlier seasons: canonical tier_key match.
    """
    entry = load_universe(site_id)
    top_n_2026 = entry["top_n"]
    if not top_n_2026:
        return {
            "cohort_keys": [], "top_n_2026": [], "top_n_per_season": {},
            "universe_match_ids": [], "bbb_match_ids": [],
        }
    cohort_keys = [tier_key(d["name"]) for d in top_n_2026]
    cids_2026 = {d["cid"] for d in top_n_2026}

    universe_ids: set[int] = set()
    bbb_ids: set[int] = set()
    per_season: dict[int, list[dict]] = {}

    for s in seasons:
        path = F.fetch_season(site_id, s, force=False)
        matches = json.loads(path.read_text()).get("matches", [])
        # competitions in this season
        comps: dict[int, dict] = {}
        for m in matches:
            if m.get("competition_type") != "League":
                continue
            cid = m.get("competition_id")
            cname = m.get("competition_name") or ""
            if not cid or EXCL.search(cname):
                continue
            if cid not in comps:
                comps[cid] = {"cid": cid, "name": cname, "n": 0,
                              "tier_key": tier_key(cname),
                              "match_type": m.get("match_type")}
            comps[cid]["n"] += 1

        # selection: prefer 2026 cid match (only valid for 2026 itself);
        # otherwise tier_key match against the cohort.
        sel: list[dict] = []
        if s == 2026:
            for cid, c in comps.items():
                if cid in cids_2026:
                    sel.append(c)
        else:
            for k in cohort_keys:
                # take all comps whose tier_key matches (sometimes a tier
                # has multiple geographical splits, e.g. Div 3 East/West)
                hits = [c for c in comps.values() if c["tier_key"] == k]
                sel.extend(hits)
        # filter to Limited Overs (we model overs cricket only)
        sel = [c for c in sel if c.get("match_type") == "Limited Overs"]
        per_season[s] = sel

        cids = {c["cid"] for c in sel}
        for m in matches:
            if (m.get("competition_id") in cids
                    and m.get("match_type") == "Limited Overs"):
                mid = int(m["id"])
                universe_ids.add(mid)
                if int(s) >= 2021:
                    bbb_ids.add(mid)

    return {
        "cohort_keys": cohort_keys,
        "top_n_2026": top_n_2026,
        "top_n_per_season": per_season,
        "universe_match_ids": sorted(universe_ids),
        "bbb_match_ids": sorted(bbb_ids),
    }


# ---------- fetch phases (unchanged) ----------------------------------------

def parallel_fetch_details(match_ids: list[int], workers: int = 8) -> dict:
    todo = [m for m in match_ids
            if not (F.MATCH_DETAIL_DIR / f"{m}.json").exists()]
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
    headers = FB.auth_headers()
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
            if status == "fetched": counts["rv_fetched"] += 1
            elif status == "cached": counts["rv_cached"] += 1
            elif status == "nv-fetched": counts["nv_fetched"] += 1
            elif status == "nv-cached": counts["nv_cached"] += 1
            elif status == "no-mapping": counts["no_mapping"] += 1
            elif status == "no-data": counts["no_data"] += 1
            else: counts["failed"] += 1
    return counts


def run_league(slug: str, site_id: int, workers_detail: int = 8,
               workers_bbb: int = 4) -> dict:
    seasons = list(range(2017, 2027))
    print(f"\n========= {slug}  (site_id={site_id}) =========", flush=True)

    out = {
        "slug": slug,
        "site_id": site_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phases": {},
    }

    print("[P1] season summaries (10 seasons) ...", flush=True)
    t0 = time.perf_counter()
    for s in seasons:
        F.fetch_season(site_id, s, force=False)
    out["phases"]["p1_summaries_secs"] = round(time.perf_counter() - t0, 2)
    print(f"     done in {out['phases']['p1_summaries_secs']}s", flush=True)

    print("[P2] universe filter (homepage-scraped top-N senior tiers) ...", flush=True)
    t0 = time.perf_counter()
    u = discover_universe_for_league(slug, site_id, seasons)
    out["phases"]["p2_universe_secs"] = round(time.perf_counter() - t0, 2)
    out["universe_n"] = len(u["universe_match_ids"])
    out["bbb_universe_n"] = len(u["bbb_match_ids"])
    out["cohort_keys"] = u["cohort_keys"]
    out["divisions_per_season"] = {
        str(s): [{"cid": c["cid"], "name": c["name"], "n": c.get("n", 0)}
                 for c in v]
        for s, v in u["top_n_per_season"].items()
    }
    print(f"     universe={out['universe_n']}  bbb-era={out['bbb_universe_n']}  "
          f"cohort_keys={u['cohort_keys']}  ({out['phases']['p2_universe_secs']}s)",
          flush=True)

    print(f"[P3] match_detail backfill ({workers_detail} workers) ...", flush=True)
    t0 = time.perf_counter()
    p3 = parallel_fetch_details(u["universe_match_ids"], workers=workers_detail)
    out["phases"]["p3_match_detail_secs"] = round(time.perf_counter() - t0, 2)
    out["phases"]["p3_counts"] = p3
    rate = (p3["fetched"] / out["phases"]["p3_match_detail_secs"]
            if p3["fetched"] and out["phases"]["p3_match_detail_secs"] else 0)
    print(f"     fetched={p3['fetched']}  cached={p3['cached']}  failed={p3['failed']}  "
          f"({out['phases']['p3_match_detail_secs']}s, {rate:.1f}/s)", flush=True)

    print(f"[P4] BBB fetch ({workers_bbb} workers, 2021+ only) ...", flush=True)
    t0 = time.perf_counter()
    p4 = parallel_fetch_bbb(u["bbb_match_ids"], workers=workers_bbb)
    out["phases"]["p4_bbb_secs"] = round(time.perf_counter() - t0, 2)
    out["phases"]["p4_counts"] = p4
    p4_fetched = p4["rv_fetched"] + p4["nv_fetched"]
    rate = (p4_fetched / out["phases"]["p4_bbb_secs"]
            if p4_fetched and out["phases"]["p4_bbb_secs"] else 0)
    print(f"     rv-fetched={p4['rv_fetched']}  rv-cached={p4['rv_cached']}  "
          f"nv-fetched={p4['nv_fetched']}  nv-cached={p4['nv_cached']}  "
          f"no-data={p4['no_data']}  failed={p4['failed']}", flush=True)
    print(f"     balls_total={p4['balls_total']}  ({out['phases']['p4_bbb_secs']}s, "
          f"{rate:.1f}/s for newly-fetched)", flush=True)

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
    args = ap.parse_args()
    run_league(args.slug, args.site_id, args.workers_detail, args.workers_bbb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
