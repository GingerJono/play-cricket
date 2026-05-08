#!/usr/bin/env python3
"""
Build *monthly* trailing-12-month skill snapshots — one per (year, month).

Replaces the single-as-of snapshot in build_snapshot.py. The motivation
is the temporal-leak problem in POC v2: a 2022 match looking up a
May-2026 snapshot gets stale or NaN player skills, so the model learns
to ignore them and fall back to date features instead.

For each calendar month from 2021-01 onwards, compute the player's
career stats across all batting / bowling rows whose `match_date`
falls in the trailing 365-day window ending on the **last day of the
prior month**. Percentile-rank the active cohort to /100 (separate
ranks for each of the five metrics — never blended).

At ball-feature time, a match in (year, month) looks up the snapshot
keyed by (year, month) — which uses data ending the day before the
month started, so there's no look-ahead leakage.

Output:
  data/skill_monthly.parquet   one row per (snapshot_yyyymm, player_id)
                               (parquet to keep size sensible)
  data/skill_monthly_summary.json   cohort sizes per snapshot
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "rainham.db"
OUT_DIR = ROOT / "data"


def parse_overs(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        if "." in s:
            whole, balls = s.split(".", 1)
            return float(whole) + (int(balls) / 6.0)
        return float(s)
    except (ValueError, AttributeError):
        return 0.0


def yyyymmdd_from_ddmmyyyy(s: str | None) -> int:
    if not s:
        return 0
    try:
        d, m, y = s.split("/")
        return int(y) * 10000 + int(m) * 100 + int(d)
    except Exception:
        return 0


def month_end(year: int, month: int) -> date:
    """Last day of (year, month)."""
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def pct_rank(series: pd.Series, higher_better: bool = True) -> pd.Series:
    r = series.rank(method="average", na_option="keep")
    n = r.notna().sum()
    if n <= 1:
        return pd.Series(50.0, index=series.index, dtype=float)
    pct = (r - 1) / (n - 1) * 100.0
    if not higher_better:
        pct = 100.0 - pct
    return pct.round(1)


# minimum sample sizes to enter the cohort for percentile-ranking
BAT_MIN_BALLS = 100
BOWL_MIN_OVERS = 30


def compute_for_window(bat: pd.DataFrame, bowl: pd.DataFrame,
                       lo_int: int, hi_int: int) -> pd.DataFrame:
    """Compute (player_id, five skill columns) for batting + bowling rows
    whose match_date YYYYMMDD lies in (lo_int, hi_int]."""
    bat_w = bat[(bat["yyyymmdd"] > lo_int) & (bat["yyyymmdd"] <= hi_int)]
    bowl_w = bowl[(bowl["yyyymmdd"] > lo_int) & (bowl["yyyymmdd"] <= hi_int)]

    bat_agg = bat_w.groupby("batsman_id").agg(
        bat_runs=("runs", "sum"),
        bat_balls=("balls", "sum"),
        bat_dismissals=("dismissed", "sum"),
        bat_innings=("runs", "count"),
    ).reset_index().rename(columns={"batsman_id": "player_id"})
    bat_agg["bat_avg"] = bat_agg["bat_runs"] / bat_agg["bat_dismissals"].replace(0, np.nan)
    bat_agg["bat_sr"] = bat_agg["bat_runs"] * 100.0 / bat_agg["bat_balls"].replace(0, np.nan)

    bowl_agg = bowl_w.groupby("bowler_id").agg(
        bowl_runs=("runs", "sum"),
        bowl_balls=("balls_bowled", "sum"),
        bowl_wickets=("wickets", "sum"),
        bowl_innings=("runs", "count"),
    ).reset_index().rename(columns={"bowler_id": "player_id"})
    bowl_agg["bowl_overs"] = bowl_agg["bowl_balls"] / 6.0
    bowl_agg["bowl_econ"] = bowl_agg["bowl_runs"] / bowl_agg["bowl_overs"].replace(0, np.nan)
    bowl_agg["bowl_avg"] = bowl_agg["bowl_runs"] / bowl_agg["bowl_wickets"].replace(0, np.nan)
    bowl_agg["bowl_sr"] = bowl_agg["bowl_balls"] / bowl_agg["bowl_wickets"].replace(0, np.nan)

    bat_cohort = bat_agg[bat_agg["bat_balls"] >= BAT_MIN_BALLS].copy()
    bowl_cohort = bowl_agg[bowl_agg["bowl_overs"] >= BOWL_MIN_OVERS].copy()

    bat_cohort["bat_avg_skill"] = pct_rank(bat_cohort["bat_avg"], higher_better=True)
    bat_cohort["bat_sr_skill"] = pct_rank(bat_cohort["bat_sr"], higher_better=True)
    bowl_cohort["bowl_econ_skill"] = pct_rank(bowl_cohort["bowl_econ"], higher_better=False)
    bowl_cohort["bowl_avg_skill"] = pct_rank(bowl_cohort["bowl_avg"], higher_better=False)
    bowl_cohort["bowl_sr_skill"] = pct_rank(bowl_cohort["bowl_sr"], higher_better=False)

    all_pids = sorted(set(bat_agg["player_id"]) | set(bowl_agg["player_id"]))
    out = pd.DataFrame({"player_id": all_pids})
    out = out.merge(bat_agg[["player_id", "bat_innings", "bat_runs",
                             "bat_balls", "bat_dismissals", "bat_avg",
                             "bat_sr"]], on="player_id", how="left")
    out = out.merge(bat_cohort[["player_id", "bat_avg_skill", "bat_sr_skill"]],
                    on="player_id", how="left")
    out = out.merge(bowl_agg[["player_id", "bowl_innings", "bowl_runs",
                              "bowl_balls", "bowl_wickets", "bowl_overs",
                              "bowl_econ", "bowl_avg", "bowl_sr"]],
                    on="player_id", how="left")
    out = out.merge(bowl_cohort[["player_id", "bowl_econ_skill",
                                 "bowl_avg_skill", "bowl_sr_skill"]],
                    on="player_id", how="left")
    return out, len(bat_cohort), len(bowl_cohort)


def main() -> int:
    if not DB.exists():
        raise SystemExit(f"missing DB at {DB}")
    print(f"loading from {DB}", flush=True)
    con = sqlite3.connect(DB)
    matches = pd.read_sql("SELECT match_id, match_date FROM matches", con)
    matches["yyyymmdd"] = matches["match_date"].map(yyyymmdd_from_ddmmyyyy)

    bat = pd.read_sql(
        "SELECT b.match_id, b.batsman_id, b.how_out, b.runs, b.balls "
        "FROM batting b", con
    )
    bowl = pd.read_sql(
        "SELECT b.match_id, b.bowler_id, b.overs, b.runs, b.wickets "
        "FROM bowling b", con
    )
    con.close()

    bat = bat.merge(matches[["match_id", "yyyymmdd"]], on="match_id", how="left")
    bat = bat[~bat["how_out"].isin(["did not bat", "absent"])]
    bat["dismissed"] = (~bat["how_out"].isin(["not out", "retired not out", ""])).astype(int)
    bat["runs"] = pd.to_numeric(bat["runs"], errors="coerce").fillna(0)
    bat["balls"] = pd.to_numeric(bat["balls"], errors="coerce").fillna(0)

    bowl = bowl.merge(matches[["match_id", "yyyymmdd"]], on="match_id", how="left")
    bowl["overs_f"] = bowl["overs"].apply(parse_overs)
    bowl["balls_bowled"] = (bowl["overs_f"] * 6.0).round().astype(int)
    bowl["runs"] = pd.to_numeric(bowl["runs"], errors="coerce").fillna(0)
    bowl["wickets"] = pd.to_numeric(bowl["wickets"], errors="coerce").fillna(0)

    print(f"  batting rows: {len(bat):,}")
    print(f"  bowling rows: {len(bowl):,}")
    print(f"  matches: {len(matches):,}")

    # months to compute snapshots for: 2021-01 through current month + 1
    today = date.today()
    snapshots: list[pd.DataFrame] = []
    summary_rows: list[dict] = []
    yyyymms: list[int] = []
    y, m = 2021, 1
    while (y, m) <= (today.year, today.month):
        yyyymms.append(y * 100 + m)
        m += 1
        if m > 12:
            y, m = y + 1, 1

    print(f"\ncomputing {len(yyyymms)} monthly snapshots ...")
    for ym in yyyymms:
        y, m = divmod(ym, 100)
        # snapshot's "as_of" = last day of (y, m); window = (as_of - 365, as_of]
        as_of = month_end(y, m)
        lo = as_of - timedelta(days=365)
        as_of_int = as_of.year * 10000 + as_of.month * 100 + as_of.day
        lo_int = lo.year * 10000 + lo.month * 100 + lo.day

        snap, n_bat, n_bowl = compute_for_window(bat, bowl, lo_int, as_of_int)
        snap["snapshot_yyyymm"] = ym
        snapshots.append(snap)
        summary_rows.append({
            "yyyymm": ym, "as_of": as_of.isoformat(),
            "n_players": int(len(snap)),
            "n_bat_cohort": n_bat, "n_bowl_cohort": n_bowl,
        })
        print(f"  {ym}  as_of={as_of}  bat_cohort={n_bat:>5}  "
              f"bowl_cohort={n_bowl:>5}  total_players={len(snap):>5}",
              flush=True)

    full = pd.concat(snapshots, ignore_index=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "skill_monthly.parquet"
    try:
        full.to_parquet(out_path, index=False)
        print(f"\nwrote {out_path}  ({len(full):,} rows)")
    except ImportError:
        # fall back to csv if pyarrow / fastparquet not available
        out_path = OUT_DIR / "skill_monthly.csv"
        full.to_csv(out_path, index=False)
        print(f"\nwrote {out_path}  ({len(full):,} rows; parquet libs missing)")

    summary = {"snapshots": summary_rows,
               "thresholds": {"bat_min_balls": BAT_MIN_BALLS,
                              "bowl_min_overs": BOWL_MIN_OVERS}}
    (OUT_DIR / "skill_monthly_summary.json").write_text(
        json.dumps(summary, indent=2)
    )
    print(f"wrote {OUT_DIR / 'skill_monthly_summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
