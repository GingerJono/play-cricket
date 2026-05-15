#!/usr/bin/env python3
"""
Build a player-skill snapshot from the last 12 months of cached match data.

Five `/100` metrics per player, kept separate (per model/PLAN.md):
  - bat_avg_skill   runs / dismissals
  - bat_sr_skill    runs / balls * 100
  - bowl_econ_skill runs / overs (lower is better)
  - bowl_avg_skill  runs / wickets (lower is better)
  - bowl_sr_skill   balls / wickets (lower is better)

Each raw metric is computed over the player's match-level batting / bowling
rows whose `match_date` lies in the trailing 12 months from `--as-of`
(default = today). The raw metrics are then percentile-ranked within the
active cohort (≥ 100 balls batted / ≥ 30 overs bowled) and scaled to
[0, 100]. For bowling metrics, lower-is-better → percentile is inverted
so 100 = best.

Players below the sample-size threshold get the cohort median for that
metric (a simple Bayesian shrinkage).

Output:
  data/skill_snapshot.csv
  data/skill_snapshot.json   summary stats + cohort medians

Usage:
  python3 model/skill/build_snapshot.py
  python3 model/skill/build_snapshot.py --as-of 2026-05-08
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "rainham.db"
OUT_DIR = ROOT / "data"


def parse_overs(s: str | None) -> float:
    """Cricket overs string '10.4' -> 10 + 4/6 balls expressed as overs."""
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


def build_snapshot(as_of: date) -> dict:
    cutoff = as_of - timedelta(days=365)
    cutoff_int = cutoff.year * 10000 + cutoff.month * 100 + cutoff.day
    as_of_int = as_of.year * 10000 + as_of.month * 100 + as_of.day

    print(f"window: {cutoff} → {as_of}", flush=True)
    if not DB.exists():
        raise SystemExit(f"missing DB at {DB}")

    con = sqlite3.connect(DB)

    # match_date in matches is dd/mm/yyyy; convert in pandas after loading
    matches = pd.read_sql("SELECT match_id, match_date FROM matches", con)
    matches["yyyymmdd"] = matches["match_date"].map(yyyymmdd_from_ddmmyyyy)
    in_window = matches[(matches["yyyymmdd"] >= cutoff_int)
                        & (matches["yyyymmdd"] <= as_of_int)]
    in_window_ids = in_window["match_id"].tolist()
    print(f"matches in window: {len(in_window):,} of {len(matches):,}", flush=True)

    if not in_window_ids:
        raise SystemExit("no matches in window — check DB / cutoff")

    # batting rows in window
    placeholders = ",".join("?" * len(in_window_ids))
    bat = pd.read_sql(
        f"SELECT batsman_id, batsman_name, how_out, runs, balls "
        f"FROM batting WHERE match_id IN ({placeholders})",
        con, params=in_window_ids,
    )
    print(f"batting rows: {len(bat):,}", flush=True)
    # exclude did-not-bat / absent
    bat = bat[~bat["how_out"].isin(["did not bat", "absent"])]
    bat["dismissed"] = ~bat["how_out"].isin(["not out", "retired not out", ""])
    bat["dismissed"] = bat["dismissed"].astype(int)
    bat["runs"] = pd.to_numeric(bat["runs"], errors="coerce").fillna(0)
    bat["balls"] = pd.to_numeric(bat["balls"], errors="coerce").fillna(0)

    bat_agg = bat.groupby("batsman_id").agg(
        bat_runs=("runs", "sum"),
        bat_balls=("balls", "sum"),
        bat_dismissals=("dismissed", "sum"),
        bat_innings=("runs", "count"),
        last_name=("batsman_name", "last"),
    ).reset_index()
    bat_agg.rename(columns={"batsman_id": "player_id"}, inplace=True)
    bat_agg["bat_avg"] = bat_agg["bat_runs"] / bat_agg["bat_dismissals"].replace(0, np.nan)
    bat_agg["bat_sr"] = bat_agg["bat_runs"] * 100.0 / bat_agg["bat_balls"].replace(0, np.nan)

    # bowling rows in window
    bowl = pd.read_sql(
        f"SELECT bowler_id, bowler_name, overs, runs, wickets "
        f"FROM bowling WHERE match_id IN ({placeholders})",
        con, params=in_window_ids,
    )
    print(f"bowling rows: {len(bowl):,}", flush=True)
    bowl["overs_f"] = bowl["overs"].apply(parse_overs)
    bowl["balls_bowled"] = (bowl["overs_f"] * 6.0).round().astype(int)
    bowl["runs"] = pd.to_numeric(bowl["runs"], errors="coerce").fillna(0)
    bowl["wickets"] = pd.to_numeric(bowl["wickets"], errors="coerce").fillna(0)

    bowl_agg = bowl.groupby("bowler_id").agg(
        bowl_runs=("runs", "sum"),
        bowl_balls=("balls_bowled", "sum"),
        bowl_wickets=("wickets", "sum"),
        bowl_innings=("runs", "count"),
        last_name=("bowler_name", "last"),
    ).reset_index()
    bowl_agg.rename(columns={"bowler_id": "player_id"}, inplace=True)
    bowl_agg["bowl_overs"] = bowl_agg["bowl_balls"] / 6.0
    bowl_agg["bowl_econ"] = bowl_agg["bowl_runs"] / bowl_agg["bowl_overs"].replace(0, np.nan)
    bowl_agg["bowl_avg"] = bowl_agg["bowl_runs"] / bowl_agg["bowl_wickets"].replace(0, np.nan)
    bowl_agg["bowl_sr"] = bowl_agg["bowl_balls"] / bowl_agg["bowl_wickets"].replace(0, np.nan)

    con.close()

    # cohort percentiles. minimum sample sizes:
    bat_min_balls = 100
    bowl_min_overs = 30
    bat_cohort = bat_agg[bat_agg["bat_balls"] >= bat_min_balls].copy()
    bowl_cohort = bowl_agg[bowl_agg["bowl_overs"] >= bowl_min_overs].copy()
    print(f"\nbatting cohort (≥{bat_min_balls} balls): {len(bat_cohort):,} players")
    print(f"bowling cohort (≥{bowl_min_overs} overs): {len(bowl_cohort):,} players")

    def pct_rank(series, higher_better: bool = True) -> pd.Series:
        # rank gives 1..N; convert to 0..100 percentile
        r = series.rank(method="average", na_option="keep")
        n = r.notna().sum()
        pct = (r - 1) / max(1, n - 1) * 100.0
        if not higher_better:
            pct = 100.0 - pct
        return pct.round(1)

    bat_cohort["bat_avg_skill"] = pct_rank(bat_cohort["bat_avg"], higher_better=True)
    bat_cohort["bat_sr_skill"] = pct_rank(bat_cohort["bat_sr"], higher_better=True)
    bowl_cohort["bowl_econ_skill"] = pct_rank(bowl_cohort["bowl_econ"], higher_better=False)
    bowl_cohort["bowl_avg_skill"] = pct_rank(bowl_cohort["bowl_avg"], higher_better=False)
    bowl_cohort["bowl_sr_skill"] = pct_rank(bowl_cohort["bowl_sr"], higher_better=False)

    # cohort medians for the shrinkage fallback
    medians = {
        "bat_avg": float(bat_cohort["bat_avg"].median()),
        "bat_sr": float(bat_cohort["bat_sr"].median()),
        "bowl_econ": float(bowl_cohort["bowl_econ"].median()),
        "bowl_avg": float(bowl_cohort["bowl_avg"].median()),
        "bowl_sr": float(bowl_cohort["bowl_sr"].median()),
    }

    # join: every player who appeared in either rolls into the snapshot.
    all_pids = sorted(set(bat_agg["player_id"]) | set(bowl_agg["player_id"]))
    snap = pd.DataFrame({"player_id": all_pids})
    snap = snap.merge(
        bat_agg[["player_id", "last_name", "bat_runs", "bat_balls",
                 "bat_dismissals", "bat_innings", "bat_avg", "bat_sr"]],
        on="player_id", how="left",
    )
    snap = snap.merge(
        bat_cohort[["player_id", "bat_avg_skill", "bat_sr_skill"]],
        on="player_id", how="left",
    )
    bowl_join = bowl_agg.rename(columns={"last_name": "last_name_bowl"})
    snap = snap.merge(
        bowl_join[["player_id", "last_name_bowl", "bowl_overs", "bowl_runs",
                   "bowl_wickets", "bowl_innings", "bowl_econ", "bowl_avg",
                   "bowl_sr"]],
        on="player_id", how="left",
    )
    snap = snap.merge(
        bowl_cohort[["player_id", "bowl_econ_skill", "bowl_avg_skill", "bowl_sr_skill"]],
        on="player_id", how="left",
    )
    # prefer batting last_name when present
    snap["display_name"] = snap["last_name"].fillna(snap["last_name_bowl"])
    snap = snap.drop(columns=["last_name", "last_name_bowl"])
    snap["as_of_date"] = as_of.isoformat()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "skill_snapshot.csv"
    snap.to_csv(csv_path, index=False)
    print(f"\nwrote {csv_path}  ({len(snap):,} players)", flush=True)

    summary = {
        "as_of_date": as_of.isoformat(),
        "window_days": 365,
        "matches_in_window": int(len(in_window)),
        "n_players_total": int(len(snap)),
        "n_bat_cohort": int(len(bat_cohort)),
        "n_bowl_cohort": int(len(bowl_cohort)),
        "cohort_medians": medians,
        "thresholds": {"bat_min_balls": bat_min_balls,
                       "bowl_min_overs": bowl_min_overs},
    }
    json_path = OUT_DIR / "skill_snapshot.json"
    json_path.write_text(json.dumps(summary, indent=2))
    print(f"wrote {json_path}")
    print()
    print("== top 10 batters by bat_sr_skill (cohort) ==")
    top = bat_cohort.sort_values("bat_sr_skill", ascending=False).head(10)
    for _, r in top.iterrows():
        print(f"  pid={int(r['player_id']):<10}  {str(r['last_name'])[:25]:<25}  "
              f"avg={r['bat_avg']:5.1f}  sr={r['bat_sr']:5.1f}  "
              f"avg_skill={r['bat_avg_skill']:.0f}  sr_skill={r['bat_sr_skill']:.0f}  "
              f"({int(r['bat_balls'])} balls)")
    print()
    print("== top 10 bowlers by bowl_econ_skill (cohort) ==")
    top = bowl_cohort.sort_values("bowl_econ_skill", ascending=False).head(10)
    for _, r in top.iterrows():
        print(f"  pid={int(r['player_id']):<10}  {str(r['last_name'])[:25]:<25}  "
              f"econ={r['bowl_econ']:4.2f}  avg={r['bowl_avg']:5.1f}  "
              f"sr={r['bowl_sr']:5.1f}  "
              f"econ_skill={r['bowl_econ_skill']:.0f}  avg_skill={r['bowl_avg_skill']:.0f}  "
              f"({r['bowl_overs']:.0f} ov)")

    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None,
                    help="ISO date for snapshot end; default = today")
    args = ap.parse_args()
    as_of = (datetime.strptime(args.as_of, "%Y-%m-%d").date()
             if args.as_of else date.today())
    build_snapshot(as_of)
    return 0


if __name__ == "__main__":
    sys.exit(main())
