#!/usr/bin/env python3
"""
Find the most surprising 1st-innings finals vs the v3d model's prediction
at 10 overs in (60 legal balls).

Train v3d on a subsample, then predict on every match's 10-over state and
rank by actual_final − predicted.

Notes:
  - y is derived from balls.SUM(runs_bat+runs_extra) directly (sidesteps
    innings_seq mismatch bug between innings/balls tables).
  - Universe-filtered (top-N senior tiers per league).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "rainham.db"
SKILL_PARQUET = ROOT / "data" / "skill_monthly.parquet"
GROUND_PARQUET = ROOT / "data" / "ground_stats.parquet"
BENCH = ROOT / "model" / "bench"

# Reuse v3d's pipeline functions
sys.path.insert(0, str(ROOT))
from model.poc.run_poc_v3d import (
    load_universe_match_ids_from_db, load_balls, add_state, join_skill,
    join_ground, join_remaining_batters, join_bowl_team_strength,
    V1_FEATURES, SKILL_FEATURE_COLS, CONTEXT, GROUND_FEATS,
    BAT_REM_FEATS, BOWL_TEAM_FEATS, ALL_FEATURES, TARGET, fit,
)

OUT = ROOT / "model" / "poc"


def url_for(mid: int, m2s: dict, slug_by_sid: dict) -> str:
    sid = m2s.get(mid)
    slug = slug_by_sid.get(sid, "www")
    return f"https://{slug}.play-cricket.com/website/results/{mid}"


def main() -> int:
    print("== loading universe + balls (via v3d pipeline) ==", flush=True)
    con0 = sqlite3.connect(DB)
    universe_ids = load_universe_match_ids_from_db(con0)
    print(f"  universe match ids: {len(universe_ids):,}")
    con0.close()

    print("\n== loading balls + state + features ==", flush=True)
    balls, con = load_balls(universe_ids)
    df = add_state(balls)
    df, nn = join_skill(df)
    df = join_ground(df)
    df = join_remaining_batters(df, con)
    df = join_bowl_team_strength(df, con)
    con.close()
    print(f"\nfeature df: {len(df):,} balls / {df['match_id'].nunique():,} matches")

    # train on a subsample; predict on EVERY match's 10-over snapshot
    MAX_TRAIN_MATCHES = 18000
    rng = np.random.default_rng(seed=42)
    all_mids = df["match_id"].unique()
    train_pool = rng.choice(all_mids, min(MAX_TRAIN_MATCHES, len(all_mids)),
                            replace=False)
    train_df = df[df["match_id"].isin(train_pool)].reset_index(drop=True)
    # quick val for early stopping
    val_pool = rng.choice([m for m in all_mids if m not in set(train_pool.tolist())],
                          min(2000, len(all_mids) - len(train_pool)),
                          replace=False)
    val_df = df[df["match_id"].isin(val_pool)].reset_index(drop=True)
    print(f"\ntrain: {len(train_pool)} matches / {len(train_df):,} balls")
    print(f"val:   {len(val_pool)} matches / {len(val_df):,} balls")

    for d in (train_df, val_df, df):
        f64 = d.select_dtypes(include=["float64"]).columns
        d[f64] = d[f64].astype("float32")

    print("\nfitting v3d (p50) ...", flush=True)
    m = fit(train_df, val_df, 0.5, ALL_FEATURES)

    # find the row at 60 legal balls per match
    print("\nfinding 10-over snapshot per match ...", flush=True)
    snap = (df[df["balls_gone"] >= 60]
            .sort_values(["match_id", "balls_gone"])
            .drop_duplicates("match_id", keep="first")
            .reset_index(drop=True))
    print(f"  matches with >= 10 ov bowled: {len(snap):,}")

    snap["v3d_pred"] = m.predict(snap[ALL_FEATURES])
    snap["surprise"] = snap[TARGET] - snap["v3d_pred"]
    snap["abs_surprise"] = snap["surprise"].abs()

    # naive RR proj from 10 ov
    snap["naive_proj"] = snap["runs"] + snap["run_rate"] * 40

    # build URL helpers
    m2s = {}
    for sd in (ROOT / "data" / "raw" / "matches").iterdir():
        if not sd.is_dir(): continue
        try: sid = int(sd.name)
        except: continue
        for sf in sd.glob("*.json"):
            try:
                for mm in json.loads(sf.read_text()).get("matches", []):
                    m2s.setdefault(int(mm["id"]), sid)
            except: pass
    slug_by_sid = {e["site_id"]: s for s, e
                   in json.load(open(BENCH / "universe.json")).items()}

    # also need teams + date — pull from matches once
    print("\nfetching match metadata for top results ...", flush=True)
    con = sqlite3.connect(DB)
    matches_meta = pd.read_sql(
        "SELECT match_id, match_date, home_club_name, away_club_name, "
        "       competition_name FROM matches",
        con,
    )
    con.close()
    snap = snap.merge(matches_meta, on="match_id", how="left")

    def show(label, sub, ascending):
        print()
        print("=" * 95)
        print(label)
        print("=" * 95)
        s = sub.sort_values("surprise", ascending=ascending).head(10)
        for _, r in s.iterrows():
            print(f"  {r['match_date']}  10ov: {int(r['runs'])}/{int(r['wickets'])}"
                  f"  →  FINAL: {int(r[TARGET])}  "
                  f"(v3d predicted {int(r['v3d_pred'])}, "
                  f"surprise {int(r['surprise']):+d})")
            ht, at = r['home_club_name'], r['away_club_name']
            print(f"      {ht} v {at}  ·  {r['competition_name']}")
            print(f"      {url_for(r['match_id'], m2s, slug_by_sid)}")

    show("TOP 10 ACCELERATIONS — actual >> v3d expectation",
         snap, ascending=False)
    show("TOP 10 COLLAPSES — actual << v3d expectation",
         snap, ascending=True)

    # quick distribution context
    print()
    print("=" * 95)
    print("Distribution of v3d surprise at 10 overs (actual − predicted)")
    print("=" * 95)
    print(f"  n matches:       {len(snap):,}")
    print(f"  mean surprise:   {snap['surprise'].mean():+.1f}  (should be ~0)")
    print(f"  median:          {snap['surprise'].median():+.1f}")
    print(f"  std:             {snap['surprise'].std():.1f}  (≈ MAE × 1.25)")
    print(f"  p10 / p90:       {snap['surprise'].quantile(0.1):+.1f}  /  "
          f"{snap['surprise'].quantile(0.9):+.1f}")
    print(f"  abs surprise:    mean={snap['abs_surprise'].mean():.1f}  "
          f"median={snap['abs_surprise'].median():.1f}")

    # save tabular output
    snap_out = snap[["match_id", "match_date", "home_club_name",
                     "away_club_name", "competition_name", "runs", "wickets",
                     TARGET, "v3d_pred", "surprise", "naive_proj"]]
    snap_out.to_csv(OUT / "surprise_at_10ov.csv", index=False)
    print(f"\nwrote {OUT / 'surprise_at_10ov.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
