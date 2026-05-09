#!/usr/bin/env python3
"""
Build per-match ground rolling stats: for each match, the average
first-innings runs at the same ground over the trailing 2 years
(NOT including this match — strict as-of cutoff).

Output:
  data/ground_stats.parquet
    columns:
      match_id           the match these stats apply to
      ground_id          the ground
      ground_avg_2y      avg 1st-innings runs over trailing 730d
      ground_n_2y        number of matches contributing
      ground_overs_avg   avg overs faced in 1st innings (for context)

Usage:
  python3 model/skill/build_ground_stats.py
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "rainham.db"
OUT = ROOT / "data" / "ground_stats.parquet"


def parse_overs(s) -> float:
    if not s:
        return 0.0
    try:
        s = str(s)
        if "." in s:
            w, b = s.split(".", 1)
            return float(w) + (int(b) / 6.0)
        return float(s)
    except (ValueError, AttributeError):
        return 0.0


def yyyymmdd(s):
    try:
        d, m, y = s.split("/")
        return int(y) * 10000 + int(m) * 100 + int(d)
    except Exception:
        return 0


def main() -> int:
    if not DB.exists():
        raise SystemExit(f"missing DB at {DB}")
    con = sqlite3.connect(DB)

    print("loading matches + 1st-innings totals ...", flush=True)
    t0 = time.perf_counter()
    df = pd.read_sql("""
      SELECT m.match_id, m.match_date, m.ground_id, m.match_type,
             i.runs AS i1_runs, i.overs AS i1_overs
      FROM matches m
      LEFT JOIN innings i
        ON i.match_id = m.match_id AND i.innings_seq = 1
      WHERE m.match_type = 'Limited Overs'
    """, con)
    con.close()
    df["yyyymmdd"] = df["match_date"].map(yyyymmdd)
    df = df[(df["yyyymmdd"] > 0) & df["ground_id"].notna()
            & df["i1_runs"].notna()].copy()
    df["i1_runs"] = pd.to_numeric(df["i1_runs"], errors="coerce")
    df["i1_overs_f"] = df["i1_overs"].apply(parse_overs)
    df = df[df["i1_runs"] > 30]  # exclude abandoned / partial
    df["date"] = pd.to_datetime(df["yyyymmdd"], format="%Y%m%d")
    df = df.sort_values(["ground_id", "date"]).reset_index(drop=True)
    print(f"  {len(df):,} matches with valid 1st-innings totals "
          f"({time.perf_counter() - t0:.1f}s)")

    # for each (ground, date) compute trailing 730-day avg of OTHER matches at this ground
    print("computing rolling 2y per-ground stats ...", flush=True)
    t0 = time.perf_counter()
    out_rows = []
    for ground_id, g in df.groupby("ground_id", sort=False):
        g = g.sort_values("date")
        runs = g["i1_runs"].to_numpy()
        overs = g["i1_overs_f"].to_numpy()
        dates = g["date"].to_numpy()
        match_ids = g["match_id"].to_numpy()
        n = len(g)
        i = 0
        for j in range(n):
            cutoff = dates[j] - np.timedelta64(730, "D")
            while i < j and dates[i] < cutoff:
                i += 1
            # window = [i, j) — strictly before this match
            if j > i:
                avg = float(runs[i:j].mean())
                ov = float(overs[i:j].mean())
                k = j - i
            else:
                avg = np.nan
                ov = np.nan
                k = 0
            out_rows.append({
                "match_id": int(match_ids[j]),
                "ground_id": ground_id,
                "ground_avg_2y": avg,
                "ground_overs_avg": ov,
                "ground_n_2y": k,
            })
    out_df = pd.DataFrame(out_rows)
    print(f"  {len(out_df):,} match rows  ({time.perf_counter() - t0:.1f}s)")
    print(f"  rows with valid 2y avg: {out_df['ground_avg_2y'].notna().sum():,}")
    print(f"  median ground_n_2y: {out_df['ground_n_2y'].median():.0f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(OUT, index=False)
    print(f"\nwrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
