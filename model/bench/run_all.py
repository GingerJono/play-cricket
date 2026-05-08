#!/usr/bin/env python3
"""
Run run_league.py over every league listed in universe.json, in series.

Idempotent — relies on the existing file-based cache so re-running just
catches up new matches. Skips leagues with empty top_n (dead sites).

Per-league progress lands in model/bench/<slug>.json, and a run-level
log goes to model/bench/run_all.log.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from model.bench.run_league import run_league  # noqa: E402

OUT = Path(__file__).resolve().parent
UNIVERSE = OUT / "universe.json"
LOG = OUT / "run_all.log"


def main() -> int:
    if not UNIVERSE.exists():
        print(f"missing {UNIVERSE} — run scrape_universe.py first")
        return 1
    u = json.loads(UNIVERSE.read_text())

    queue: list[tuple[str, int]] = []
    skipped: list[tuple[str, str]] = []
    for slug, entry in u.items():
        sid = entry.get("site_id")
        if entry.get("error"):
            skipped.append((slug, f"scrape error: {entry['error']}"))
            continue
        if not entry.get("top_n"):
            skipped.append((slug, "no senior tiers found"))
            continue
        queue.append((slug, sid))

    print(f"queue: {len(queue)} leagues")
    print(f"skipped: {len(skipped)}")
    for s, why in skipped:
        print(f"  - {s}: {why}")
    print()

    results = []
    t_run0 = time.perf_counter()
    log = LOG.open("w")
    for i, (slug, sid) in enumerate(queue, 1):
        t0 = time.perf_counter()
        line = f"[{i}/{len(queue)}] {slug} (site_id={sid}) ..."
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()
        try:
            r = run_league(slug, sid, workers_detail=8, workers_bbb=4)
            wall = r["phases"]["wall_secs"]
            line = (f"    OK   universe={r['universe_n']}  "
                    f"bbb-era={r['bbb_universe_n']}  "
                    f"balls={r['phases']['p4_counts'].get('balls_total', 0)}  "
                    f"wall={wall:.0f}s")
        except Exception as e:
            line = f"    FAIL {type(e).__name__}: {e}"
            r = {"slug": slug, "site_id": sid, "error": str(e)}
        elapsed = time.perf_counter() - t0
        line += f"   ({elapsed:.0f}s)"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()
        results.append(r)
    total = time.perf_counter() - t_run0
    print(f"\n=== ALL LEAGUES DONE in {total/60:.1f} min ===", flush=True)
    log.write(f"\nTOTAL: {total/60:.1f} min over {len(queue)} leagues\n")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
