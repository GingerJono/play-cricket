#!/usr/bin/env python3
"""
Aggregate per-league benchmark JSONs into a single results.md report.

Files in model/bench/:
  <slug>.json           the canonical (relaxed-filter) result
  <slug>_strict.json    optional: strict-filter comparison
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent


def fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    return f"{s // 60:.0f}m {s % 60:04.1f}s"


def render_league(d: dict, strict: dict | None) -> list[str]:
    md = []
    md.append(f"### {d['slug']}  (site_id={d['site_id']})")
    md.append("")
    p = d["phases"]
    p3 = p.get("p3_counts", {})
    p4 = p.get("p4_counts", {})
    md.append(f"- Universe (top-5 1st-XI Limited Overs, 2017-2026): **{d['universe_n']:,}** matches")
    md.append(f"- BBB-era subset (2021+):                                  **{d['bbb_universe_n']:,}** matches")
    if strict:
        md.append(f"- _Strict-filter comparison_:                              "
                  f"{strict['universe_n']:,} universe / "
                  f"{strict['bbb_universe_n']:,} BBB-era "
                  f"({strict['universe_n']/d['universe_n']*100:.0f}% of relaxed)")
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
    bbb_pos = p4.get("rv_fetched", 0) + p4.get("rv_cached", 0) + p4.get("nv_fetched", 0) + p4.get("nv_cached", 0)
    bbb_pct = (100 * bbb_pos / p4["total_matches"]) if p4.get("total_matches") else 0
    md.append("**BBB outcome breakdown** (across all 2021+ universe matches):")
    md.append("")
    md.append("| outcome | n | % |")
    md.append("|---|---:|---:|")
    n_tot = p4.get("total_matches", 0) or 1
    for k, label in [
        ("rv_fetched", "RV fetched"),
        ("rv_cached", "RV cached"),
        ("nv_fetched", "NV fetched"),
        ("nv_cached", "NV cached"),
        ("no_mapping", "no mapping"),
        ("no_data", "no data"),
        ("failed", "failed"),
    ]:
        n = p4.get(k, 0)
        md.append(f"| {label} | {n:,} | {100*n/n_tot:.0f}% |")
    md.append(f"| **BBB-positive (any source)** | **{bbb_pos:,}** | **{bbb_pct:.0f}%** |")
    md.append(f"| **balls fetched (cumulative)** | **{p4.get('balls_total', 0):,}** | – |")
    md.append("")
    return md


def main() -> int:
    # canonical = files NOT ending in _strict.json
    all_files = sorted(OUT.glob("*.json"))
    canonical = [f for f in all_files if not f.stem.endswith("_strict")
                 and f.stem not in ("results", "universe")]
    strict_files = {f.stem.replace("_strict", ""): f for f in all_files if f.stem.endswith("_strict")}
    if not canonical:
        print("no canonical per-league JSONs found in model/bench/")
        return 1

    leagues = []
    for f in canonical:
        d = json.loads(f.read_text())
        s = strict_files.get(f.stem)
        s_d = json.loads(s.read_text()) if s else None
        leagues.append((d, s_d))

    md: list[str] = []
    md.append("# Bench — multi-league universe scrape")
    md.append("")
    md.append("Wall-clock timings for the 4 phases in `run_league.py`,")
    md.append("running on this laptop / runner against the live")
    md.append("Play-Cricket API. Cache hits are essentially free; the")
    md.append("throughput numbers count freshly-fetched calls only.")
    md.append("")
    md.append("Universe = **top-5 1st-XI Limited Overs** divisions across")
    md.append("the 10 seasons 2017-2026. The BBB phase narrows further to")
    md.append("the 2021+ era where ball-by-ball coverage is meaningful.")
    md.append("")
    md.append("## Headline (canonical / relaxed-filter runs)")
    md.append("")
    md.append("| League | Universe | BBB-era | BBB-positive | Balls | P3 | P4 | Wall total |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    tot_universe = 0
    tot_bbb_universe = 0
    tot_balls = 0
    tot_wall = 0.0
    tot_bbb_pos = 0
    for d, _ in leagues:
        p = d["phases"]
        p4 = p["p4_counts"]
        bbb_pos = p4.get("rv_fetched", 0) + p4.get("rv_cached", 0) + p4.get("nv_fetched", 0) + p4.get("nv_cached", 0)
        balls = p4.get("balls_total", 0)
        md.append(
            f"| `{d['slug']}` ({d['site_id']}) | {d['universe_n']:,} | "
            f"{d['bbb_universe_n']:,} | {bbb_pos:,} ({100*bbb_pos/max(1,d['bbb_universe_n']):.0f}%) | "
            f"{balls:,} | {fmt_secs(p['p3_match_detail_secs'])} | "
            f"{fmt_secs(p['p4_bbb_secs'])} | {fmt_secs(p['wall_secs'])} |"
        )
        tot_universe += d["universe_n"]
        tot_bbb_universe += d["bbb_universe_n"]
        tot_balls += balls
        tot_wall += p["wall_secs"]
        tot_bbb_pos += bbb_pos
    md.append(
        f"| **Total** | **{tot_universe:,}** | **{tot_bbb_universe:,}** | "
        f"**{tot_bbb_pos:,}** ({100*tot_bbb_pos/max(1,tot_bbb_universe):.0f}%) | "
        f"**{tot_balls:,}** | – | – | **{fmt_secs(tot_wall)}** |"
    )
    md.append("")

    # Strict comparison
    has_strict = [(d, s) for d, s in leagues if s is not None]
    if has_strict:
        md.append("## Filter sensitivity: strict vs relaxed")
        md.append("")
        md.append("The original `1st XI|Senior|Premier` inclusion regex undercounts")
        md.append("leagues that name divisions plainly (`Division 1`, `Division 2 A/B`,")
        md.append("etc.) without the `1st XI` prefix. Most ECB Premier League sites")
        md.append("use that plainer convention. The relaxed filter (EXCL alone) is the")
        md.append("correct generaliser.")
        md.append("")
        md.append("| League | Strict universe | Relaxed universe | × |")
        md.append("|---|---:|---:|---:|")
        for d, s in has_strict:
            ratio = d["universe_n"] / max(1, s["universe_n"])
            md.append(
                f"| `{d['slug']}` ({d['site_id']}) | {s['universe_n']:,} | "
                f"{d['universe_n']:,} | {ratio:.1f}× |"
            )
        md.append("")

    # Per-league detail
    md.append("## Per-league detail (relaxed filter)")
    md.append("")
    for d, s in leagues:
        md.extend(render_league(d, s))

    # Extrapolation
    avg_universe = tot_universe / len(leagues)
    avg_bbb = tot_bbb_universe / len(leagues)
    avg_wall_secs_per_league_first_run = tot_wall / len(leagues)
    md.append("## Extrapolation to all 33 ECB Premier Leagues")
    md.append("")
    md.append("Across the three benchmarked leagues (relaxed filter):")
    md.append("- Mean universe: **~{:,.0f}** matches".format(avg_universe))
    md.append("- Mean BBB-era:  **~{:,.0f}** matches".format(avg_bbb))
    md.append(f"- Mean wall (P1-P4 first run): **{fmt_secs(avg_wall_secs_per_league_first_run)}**")
    md.append("")
    md.append("Surrey site_id 29012 only carries 2025-2026 data on the")
    md.append("Play-Cricket league site (older seasons live elsewhere), so its")
    md.append("universe is much smaller than its actual 10-year footprint —")
    md.append("the means above are pulled down by that. Excluding Surrey:")
    others = [d for d, _ in leagues if d["slug"] != "surrey"]
    if others:
        avg_u_others = sum(d["universe_n"] for d in others) / len(others)
        avg_bbb_others = sum(d["bbb_universe_n"] for d in others) / len(others)
        avg_wall_others = sum(d["phases"]["wall_secs"] for d in others) / len(others)
        md.append(f"- Mean universe: **~{avg_u_others:,.0f}** matches")
        md.append(f"- Mean BBB-era:  **~{avg_bbb_others:,.0f}** matches")
        md.append(f"- Mean wall:     **{fmt_secs(avg_wall_others)}**")
    md.append("")
    md.append("**Naive extrapolation (Essex + Herts only) to 33 leagues:**")
    md.append("")
    md.append(f"- Total universe: ~{int(avg_u_others * 33):,} matches")
    md.append(f"- Total BBB-era:  ~{int(avg_bbb_others * 33):,} matches")
    md.append(f"- Total wall:     ~{fmt_secs(avg_wall_others * 33)} of API time")
    md.append("")
    md.append("Wall is roughly linear in BBB-positive count (~1.5 match/s")
    md.append("at 4 BBB workers). At higher worker count it bottlenecks on")
    md.append("the API, not the laptop, so the practical floor for a")
    md.append("from-scratch national scrape sits around 6-10 hours.")
    md.append("")
    md.append("## Caveats")
    md.append("")
    md.append("- All three runs were **first-time fetches** (no warm cache).")
    md.append("  Subsequent re-runs hit the idempotency check and are")
    md.append("  effectively free — we already see this on the strict→relaxed")
    md.append("  re-runs where match_details overlapped.")
    md.append("- BBB coverage on the wider universe (60-70%) is meaningfully")
    md.append("  lower than the earlier 50-match Essex sample (96%) because")
    md.append("  the wider universe includes 2026 future fixtures (no BBB")
    md.append("  yet) and lower-tier divisions that PCS-score less reliably.")
    md.append("- Surrey site_id 29012 is a recent rebrand — historical")
    md.append("  seasons live elsewhere. Same flag applies to lincspremiercl")
    md.append("  / npcl per `PLAN_AMATEUR_MODELLING.md`.")

    out_path = OUT / "results.md"
    out_path.write_text("\n".join(md) + "\n")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
