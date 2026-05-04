#!/usr/bin/env python3
"""
Fetch matches and match details from the Play-Cricket API.

Cache layout (committed):
  stats/data/raw/matches/<site_id>/<season>.json   per-season summary list
  stats/data/raw/match_detail/<match_id>.json      per-match scorecard (shared)

Idempotent: skips files that already exist on disk.

Default site_id is 5251 (Rainham CC, Essex). Pass --site-id to fetch any other
club's matches (e.g. for opposition scouting).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API_BASE = "https://play-cricket.com/api/v2"
DEFAULT_SITE_ID = 5251
API_TOKEN = os.environ.get("PC_API_TOKEN", "683aa06cb15ca7e565c28cca8aac77ab")

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
MATCHES_DIR = RAW_DIR / "matches"          # /<site_id>/<season>.json
MATCH_DETAIL_DIR = RAW_DIR / "match_detail"
LEAGUE_TABLE_DIR = RAW_DIR / "league_table"  # /<division_id>.json

DEFAULT_FIRST_SEASON = 1990
DEFAULT_LAST_SEASON = 2026

USER_AGENT = "rainham-cc-stats/1.0 (+https://github.com/gingerjono/rcc-2020-site)"


def http_get_json(url: str, attempts: int = 5) -> dict:
    backoff = 1.0
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        except (URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"Failed after {attempts} attempts: {url} (last: {last_err})")


def site_dir(site_id: int) -> Path:
    return MATCHES_DIR / str(site_id)


def fetch_season(site_id: int, season: int, force: bool = False) -> Path:
    out = site_dir(site_id) / f"{season}.json"
    if out.exists() and not force:
        return out
    url = f"{API_BASE}/matches.json?" + urlencode({
        "site_id": site_id,
        "api_token": API_TOKEN,
        "season": season,
    })
    data = http_get_json(url)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))
    return out


def fetch_match_detail(match_id: int, force: bool = False) -> Path:
    out = MATCH_DETAIL_DIR / f"{match_id}.json"
    if out.exists() and not force:
        return out
    url = f"{API_BASE}/match_detail.json?" + urlencode({
        "match_id": match_id,
        "api_token": API_TOKEN,
    })
    data = http_get_json(url)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))
    return out


def fetch_league_table(division_id: int, force: bool = False) -> Path:
    out = LEAGUE_TABLE_DIR / f"{division_id}.json"
    if out.exists() and not force:
        return out
    url = f"{API_BASE}/league_table.json?" + urlencode({
        "division_id": division_id,
        "api_token": API_TOKEN,
    })
    data = http_get_json(url)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))
    return out


def collect_match_ids(site_id: int, seasons: list[int]) -> list[int]:
    ids: list[int] = []
    for season in seasons:
        path = site_dir(site_id) / f"{season}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for m in data.get("matches", []):
            try:
                ids.append(int(m["id"]))
            except (KeyError, TypeError, ValueError):
                continue
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-id", type=int, default=DEFAULT_SITE_ID,
                        help="Play-Cricket site ID to fetch (default: 5251 Rainham CC)")
    parser.add_argument("--first-season", type=int, default=DEFAULT_FIRST_SEASON)
    parser.add_argument("--last-season", type=int, default=DEFAULT_LAST_SEASON)
    parser.add_argument("--force-seasons", action="store_true",
                        help="Re-fetch matches.json even if cached.")
    parser.add_argument("--force-details", action="store_true",
                        help="Re-fetch match_detail.json even if cached.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--only-recent", type=int, default=0,
                        help="If >0, force-refetch the last N seasons' summaries.")
    parser.add_argument("--division-ids", default="",
                        help="Comma-separated list of division_ids to fetch league_table.json for "
                             "(in addition to the matches fetch).")
    parser.add_argument("--force-league-tables", action="store_true")
    args = parser.parse_args()

    seasons = list(range(args.first_season, args.last_season + 1))
    print(f"site_id={args.site_id}  seasons {seasons[0]}..{seasons[-1]}", flush=True)

    recent_force = set()
    if args.only_recent > 0:
        recent_force = set(seasons[-args.only_recent:])
    for season in seasons:
        force = args.force_seasons or season in recent_force
        path = fetch_season(args.site_id, season, force=force)
        n = len(json.loads(path.read_text()).get("matches", []))
        print(f"  {season}: {n} matches", flush=True)

    match_ids = collect_match_ids(args.site_id, seasons)
    print(f"Total unique match IDs for site {args.site_id}: {len(match_ids)}",
          flush=True)

    todo: list[int] = []
    for mid in match_ids:
        if args.force_details or not (MATCH_DETAIL_DIR / f"{mid}.json").exists():
            todo.append(mid)
    print(f"To fetch: {len(todo)} (already cached: {len(match_ids) - len(todo)})",
          flush=True)

    failed: list[tuple[int, str]] = []
    if todo:
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(fetch_match_detail, mid, args.force_details): mid
                       for mid in todo}
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    fut.result()
                    done += 1
                    if done % 50 == 0:
                        print(f"  fetched {done}/{len(todo)}", flush=True)
                except Exception as e:
                    failed.append((mid, str(e)))
        print(f"Fetched: {done}/{len(todo)}; failed: {len(failed)}", flush=True)
        if failed:
            for mid, err in failed[:20]:
                print(f"  FAIL {mid}: {err}", flush=True)

    # League tables (independent of matches fetch)
    if args.division_ids:
        ids = [int(x.strip()) for x in args.division_ids.split(",") if x.strip()]
        for did in ids:
            path = fetch_league_table(did, force=args.force_league_tables)
            print(f"  league_table {did}: {path}", flush=True)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
