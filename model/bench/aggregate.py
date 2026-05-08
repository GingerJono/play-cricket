#!/usr/bin/env python3
"""
Aggregate per-league benchmark JSONs into a single results.md report.

Usage:
  python3 model/bench/aggregate.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent


def fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    return f"{s // 60:.0f}m {s % 60:04.1f}s"


def render_league(d: dict) -> list[str]:
    md = []
    md.append(f"### {d['slug']}  (site_id={d['site_id']})")
    md.append("")
    p = d["phases"]
    p3 = p.get("p3_counts", {})
    p4 = p.get("p4_counts", {})
    md.append(f"- Universe (top-5 1st-XI Limited Overs, 2017-2026): **{d['universe_n']:,}** matches")
    md.append(f"- BBB-era subset (2021+):                                  **{d['bbb_universe_n']:,}** matches")
    md.append("")
    md.append("| Phase | Wall time | New API calls | From cache | Throughput |")
    md.append("|---|---:|---:|---:|---:|")
    md.append(f"| P1 — season summaries (×10) | {fmt_secs(p.get('p1_summaries_secs', 0))} | up to 10 | – | – |")
    md.append(f"| P2 — universe filter        | {fmt_secs(p.get('p2_universe_secs', 0))} | 0 | – | – |")
    p3_rate = p3['fetched'] / p['p3_match_detail_secs'] if p3.get('fetched') and p['p3_match_detail_secs'] else 0
    md.append(
        f"| P3 — match_detail backfill  | {fmt_secs(p.get('p3_match_detail_secs', 0))} | "
        f"{p3.get('fetched', 0):,} | {p3.get('cached', 0):,} | "
        f"{p3_rate:.1f} req/s |"
    )
    p4_fetched = p4.get("rv_fetched", 0) + p4.get("nv_fetched", 0)
    p4_cached = p4.get("rv_cached", 0) + p4.get("nv_cached", 0)
    p4_rate = p4_fetched / p['p4_bbb_secs'] if p4_fetched and p['p4_bbb_secs'] else 0
    md.append(
        f"| P4 — BBB fetch              | {fmt_secs(p.get('p4_bbb_secs', 0))} | "
        f"{p4_fetched:,} | {p4_cached:,} | {p4_rate:.1f} match/s |"
    )
    md.append(
        f"| **Total wall time**         | **{fmt_secs(p.get('wall_secs', 0))}** | – | – | – |"
    )
    md.append("")
    md.append("**BBB outcome breakdown** (across all 2021+ universe matches):")
    md.append("")
    md.append("| outcome | n |")
    md.append("|---|---:|")
    md.append(f"| RV fetched | {p4.get('rv_fetched', 0):,} |")
    md.append(f"| RV cached  | {p4.get('rv_cached', 0):,} |")
    md.append(f"| NV fetched | {p4.get('nv_fetched', 0):,} |")
    md.append(f"| NV cached  | {p4.get('nv_cached', 0):,} |")
    md.append(f"| no mapping | {p4.get('no_mapping', 0):,} |")
    md.append(f"| no data    | {p4.get('no_data', 0):,} |")
    md.append(f"| failed     | {p4.get('failed', 0):,} |")
    md.append(f"| **balls fetched (cumulative)** | **{p4.get('balls_total', 0):,}** |")
    md.append("")
    return md


def main() -> int:
    league_files = sorted(OUT.glob("*.json"))
    league_files = [f for f in league_files if f.name not in {"results.json"}]
    if not league_files:
        print("no per-league JSONs found in model/bench/")
        return 1
    leagues = [json.loads(f.read_text()) for f in league_files]

    md: list[str] = []
    md.append("# Bench — multi-league universe scrape")
    md.append("")
    md.append("Wall-clock timings for the four phases in `run_league.py`,")
    md.append("running on this laptop / runner against the live Play-Cricket")
    md.append("API. Cache hits are essentially free; the throughput numbers")
    md.append("only count freshly-fetched calls.")
    md.append("")
    md.append("All universe sizes use **top-5 1st-XI Limited Overs**")
    md.append("divisions over 2017-2026 (matches with `match_type =")
    md.append("'Limited Overs'`, ~~Declaration~~). The BBB phase narrows")
    md.append("further to the 2021+ era where ball-by-ball coverage is")
    md.append("meaningful.")
    md.append("")
    md.append("## Headline")
    md.append("")
    md.append("| League | Universe | BBB-era | P3 detail | P4 BBB | Wall total |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for d in leagues:
        p = d["phases"]
        md.append(
            f"| `{d['slug']}` ({d['site_id']}) | {d['universe_n']:,} | {d['bbb_universe_n']:,} | "
            f"{fmt_secs(p.get('p3_match_detail_secs', 0))} | "
            f"{fmt_secs(p.get('p4_bbb_secs', 0))} | "
            f"{fmt_secs(p.get('wall_secs', 0))} |"
        )
    md.append("")

    # totals
    tot_universe = sum(d["universe_n"] for d in leagues)
    tot_bbb_universe = sum(d["bbb_universe_n"] for d in leagues)
    tot_balls = sum(d["phases"]["p4_counts"].get("balls_total", 0) for d in leagues)
    tot_wall = sum(d["phases"].get("wall_secs", 0) for d in leagues)
    md.append(f"- Total universe matches:  **{tot_universe:,}**")
    md.append(f"- Total BBB-era matches:   **{tot_bbb_universe:,}**")
    md.append(f"- Total balls in cache:    **{tot_balls:,}**")
    md.append(f"- Total wall (sum):        **{fmt_secs(tot_wall)}**")
    md.append("")

    md.append("## Per-league detail")
    md.append("")
    for d in leagues:
        md.extend(render_league(d))

    # extrapolation
    avg_universe = tot_universe / len(leagues)
    avg_bbb = tot_bbb_universe / len(leagues)
    md.append("## Extrapolation to all 33 ECB Premier Leagues")
    md.append("")
    md.append(f"Average per league: ~{avg_universe:,.0f} universe matches, ~{avg_bbb:,.0f} BBB-era.")
    md.append("")
    md.append(f"- Estimated full-scrape universe: **~{int(avg_universe * 33):,}** matches")
    md.append(f"- Estimated full-scrape BBB-era:  **~{int(avg_bbb * 33):,}** matches")
    md.append("")
    md.append("If we extrapolate the per-league wall time linearly, the")
    md.append("national scrape (one-shot, idempotent) is on the order of")
    md.append(f"**{fmt_secs(tot_wall * 33 / len(leagues))}** of API time.")
    md.append("")
    md.append("These are wall-clock times against the shared")
    md.append("Play-Cricket API token, with realistic worker counts (8 for")
    md.append("match_detail, 4 for BBB). Throughput is bounded by the API,")
    md.append("not the laptop.")

    out_path = OUT / "results.md"
    out_path.write_text("\n".join(md) + "\n")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
