#!/usr/bin/env python3
"""
POC v3d — v3c plus four new feature groups:

1. UNIVERSE FILTER. Drop balls whose match_id isn't in the universe
   defined by model/bench/<slug>.json (top-N senior tiers per league).
   Removes indoor / U13 / 2nd XI leakage.

2. GROUND ROLLING STATS. Per-match avg 1st-innings runs at the same
   ground over the trailing 730 days (built by build_ground_stats.py).
   Two features: ground_avg_2y, ground_overs_avg.

3. REMAINING BATTERS skill aggregate. For each ball, the players who
   haven't yet come to the crease (positions > wickets + 2) — aggregate
   their bat_avg_skill / bat_sr_skill (mean + min). Uses match_players
   ordered by batting.position.

4. REMAINING BOWLERS skill aggregate (simplified). Per match, the
   bowling team's recognised-bowler set: players in match_players for
   the bowling side who have non-NaN bowl_econ_skill in the snapshot.
   Aggregate mean/min of bowl_econ_skill, bowl_avg_skill, bowl_sr_skill.
   Static per match (doesn't depend on ball position).

Innings 1 only, Limited Overs only, BBB era (2021+).
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
OUT = Path(__file__).resolve().parent

SKILL_COLS = ["bat_avg_skill", "bat_sr_skill",
              "bowl_econ_skill", "bowl_avg_skill", "bowl_sr_skill"]
TARGET = "y_final_innings_runs"


# ---------- universe filter --------------------------------------------------

def load_universe_match_ids() -> set[int]:
    """match_ids in the top-N senior universe across all benchmarked leagues."""
    out: set[int] = set()
    for f in BENCH.glob("*.json"):
        if f.stem in {"universe", "results"} or f.stem.endswith("_strict"):
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        # use the cohort_keys / divisions per season — gather all comp ids
        cids = set()
        for season, divs in d.get("divisions_per_season", {}).items():
            for div in divs or []:
                if isinstance(div, dict):
                    try:
                        cids.add(int(div.get("cid")))
                    except (TypeError, ValueError):
                        pass
        if not cids:
            continue
        # universe match ids are stored as a top-level field by run_league.py
        # (we don't always have them — fall back to filtering via DB if needed)
    return out


def load_universe_match_ids_from_db(con) -> set[int]:
    """All match_ids whose competition_id is in the cohort cids of any league."""
    cids: set[int] = set()
    for f in BENCH.glob("*.json"):
        if f.stem in {"universe", "results"} or f.stem.endswith("_strict"):
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for season, divs in d.get("divisions_per_season", {}).items():
            for div in divs or []:
                if isinstance(div, dict):
                    try:
                        cids.add(int(div.get("cid")))
                    except (TypeError, ValueError):
                        pass
    if not cids:
        return set()
    placeholders = ",".join("?" * len(cids))
    rows = con.execute(
        f"SELECT match_id FROM matches WHERE competition_id IN ({placeholders})",
        list(cids),
    ).fetchall()
    return {r[0] for r in rows}


# ---------- 1. load balls from SQL ------------------------------------------

def load_balls(universe_ids: set[int]) -> pd.DataFrame:
    if not DB.exists():
        raise SystemExit(f"missing {DB}; run build_db.py first")
    con = sqlite3.connect(DB)
    print("  reading matches metadata ...", flush=True)
    matches = pd.read_sql(
        "SELECT match_id, match_date, match_type, "
        "       home_team_id, away_team_id, "
        "       home_club_id, away_club_id, ground_id "
        "FROM matches WHERE match_type = 'Limited Overs'",
        con,
    )
    print(f"    {len(matches):,} Limited Overs matches in DB", flush=True)
    matches["match_date_yyyymmdd"] = matches["match_date"].apply(
        lambda s: int(s.split("/")[2]) * 10000 + int(s.split("/")[1]) * 100
                  + int(s.split("/")[0]) if s and "/" in s else 0
    )
    matches["season"] = matches["match_date_yyyymmdd"] // 10000
    matches["match_month"] = (matches["match_date_yyyymmdd"] // 100) % 100
    matches["snap_ym"] = matches["season"] * 100 + matches["match_month"]
    matches = matches[(matches["season"] >= 2021) & (matches["season"] <= 2026)]
    if universe_ids:
        before = len(matches)
        matches = matches[matches["match_id"].isin(universe_ids)]
        print(f"    universe filter: {len(matches):,} of {before:,} matches "
              f"({100*len(matches)/max(1,before):.0f}%)", flush=True)

    # No innings_seq mismatch filter: y comes from ball-stream sum
    # (which is by definition consistent), and batting team comes from
    # balls.team_batting_club_id directly. The innings table is unused.

    # y target: sum of all balls in innings_seq=1
    print("  computing y from ball-stream sum ...", flush=True)
    y_from_balls = pd.read_sql(
        "SELECT match_id, "
        "       SUM(COALESCE(runs_bat,0) + COALESCE(runs_extra,0)) AS y_final_innings_runs "
        "FROM balls WHERE innings_seq = 1 GROUP BY match_id",
        con,
    )
    print(f"    y rows: {len(y_from_balls):,}")

    # overs_per_innings — heuristic clamp by ball count
    legal_per_match = pd.read_sql(
        "SELECT match_id, COUNT(*) AS legal_balls "
        "FROM balls WHERE innings_seq = 1 AND is_legal_ball = 1 "
        "GROUP BY match_id",
        con,
    )
    legal_per_match["overs_per_innings"] = legal_per_match["legal_balls"].apply(
        lambda b: 50 if b > 270 else 45 if b > 240 else 40 if b > 210 else 50
    )

    print("  reading balls (innings 1 only) ...", flush=True)
    balls = pd.read_sql(
        "SELECT match_id, ball_no, ball_no_disp, over_no, "
        "       batter_id, non_striker_id, bowler_id, "
        "       runs_bat, runs_extra, extras_type, is_legal_ball, "
        "       dismissed_batter_id, team_batting_club_id "
        "FROM balls WHERE innings_seq = 1",
        con,
    )
    print(f"    {len(balls):,} ball rows from innings 1", flush=True)

    balls = balls.merge(matches[["match_id", "season", "match_month",
                                  "snap_ym", "ground_id",
                                  "home_team_id", "away_team_id",
                                  "home_club_id", "away_club_id"]],
                        on="match_id", how="inner")
    balls = balls.merge(y_from_balls[["match_id", "y_final_innings_runs"]],
                        on="match_id", how="inner")
    balls = balls.merge(legal_per_match[["match_id", "overs_per_innings"]],
                        on="match_id", how="inner")
    # batting club from balls; bowling = the OTHER club
    balls["bowl_team_side"] = np.where(
        balls["team_batting_club_id"].astype(str)
        == balls["home_club_id"].astype(str),
        "away", "home",
    )
    print(f"    after join: {len(balls):,} rows / {balls['match_id'].nunique():,} matches", flush=True)

    return balls, con


# ---------- 2. compute per-ball cumulative state (same as v3c) -------------

def add_state(balls: pd.DataFrame) -> pd.DataFrame:
    print("  computing per-ball state ...", flush=True)
    t0 = time.perf_counter()
    df = balls.sort_values(["match_id", "over_no", "ball_no"]).reset_index(drop=True)
    df["runs_bat"] = pd.to_numeric(df["runs_bat"], errors="coerce").fillna(0)
    df["runs_extra"] = pd.to_numeric(df["runs_extra"], errors="coerce").fillna(0)
    df["is_legal_ball"] = pd.to_numeric(df["is_legal_ball"], errors="coerce").fillna(1).astype(int)
    df["delivery_runs"] = df["runs_bat"] + df["runs_extra"]
    df["is_wicket"] = df["dismissed_batter_id"].notna().astype(int)

    g = df.groupby("match_id", sort=False)
    df["runs"] = (g["delivery_runs"].cumsum() - df["delivery_runs"]).astype(int)
    df["balls_gone"] = (g["is_legal_ball"].cumsum() - df["is_legal_ball"]).astype(int)
    df["wickets"] = (g["is_wicket"].cumsum() - df["is_wicket"]).astype(int)

    df = df[df["is_legal_ball"] == 1].reset_index(drop=True)

    df["bat_runs_event"] = df["runs_bat"]
    df["bat_balls_event"] = (df["extras_type"] != 2).astype(int)
    g_b = df.groupby(["match_id", "batter_id"], sort=False, dropna=False)
    df["striker_runs_so_far"] = (g_b["bat_runs_event"].cumsum() - df["bat_runs_event"]).fillna(0).astype(int)
    df["striker_balls_so_far"] = (g_b["bat_balls_event"].cumsum() - df["bat_balls_event"]).fillna(0).astype(int)
    df["striker_intra_sr"] = np.where(
        df["striker_balls_so_far"] > 0,
        df["striker_runs_so_far"] * 100.0 / df["striker_balls_so_far"], np.nan,
    )

    df["ns_runs_so_far"] = np.nan
    df["ns_balls_so_far"] = np.nan
    df["ns_intra_sr"] = np.nan

    g_w = df.groupby(["match_id", "bowler_id"], sort=False, dropna=False)
    df["bowler_balls_in_innings"] = (g_w["is_legal_ball"].cumsum() - df["is_legal_ball"]).fillna(0).astype(int)
    df["bowler_runs_in_innings"] = (g_w["delivery_runs"].cumsum() - df["delivery_runs"]).fillna(0).astype(int)
    df["bowler_wkts_in_innings"] = (g_w["is_wicket"].cumsum() - df["is_wicket"]).fillna(0).astype(int)
    df["bowler_econ_so_far"] = np.where(
        df["bowler_balls_in_innings"] > 0,
        df["bowler_runs_in_innings"] * 6.0 / df["bowler_balls_in_innings"], np.nan,
    )

    df["legal_total"] = (df["overs_per_innings"] * 6).astype(int)
    df["balls_left"] = (df["legal_total"] - df["balls_gone"]).clip(lower=0)
    df["frac_innings"] = df["balls_gone"] / df["legal_total"]
    df["run_rate"] = np.where(df["balls_gone"] > 0,
                               df["runs"] * 6.0 / df["balls_gone"], 0.0)
    df["wickets_in_hand"] = (10 - df["wickets"]).clip(lower=0)
    df["batters_remaining_count"] = (11 - df["wickets"] - 2).clip(lower=0)

    df = df.rename(columns={"batter_id": "striker_id", "non_striker_id": "ns_id"})
    df = df.sort_values(["match_id", "over_no", "ball_no"]).reset_index(drop=True)
    print(f"    state computed in {time.perf_counter() - t0:.0f}s", flush=True)
    return df


# ---------- 3. skill join (same as v3c) -----------------------------------

def join_skill(df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    skill = pd.read_parquet(SKILL_PARQUET)
    skill["player_id"] = skill["player_id"].astype("Int64")
    skill["snapshot_yyyymm"] = skill["snapshot_yyyymm"].astype("Int64")
    skill_slim = skill[["player_id", "snapshot_yyyymm"] + SKILL_COLS]

    df["snap_ym"] = df["snap_ym"].astype("Int64")
    for role in ("striker", "ns", "bowler"):
        df[f"{role}_id"] = df[f"{role}_id"].astype("Int64")
        rename = {c: f"{role}_{c}" for c in SKILL_COLS}
        right = skill_slim.rename(columns={"player_id": f"{role}_id",
                                            "snapshot_yyyymm": "snap_ym",
                                            **rename})
        df = df.merge(right, on=[f"{role}_id", "snap_ym"], how="left")
    nn = df[f"striker_{SKILL_COLS[0]}"].notna().mean()
    print(f"  striker skill non-null fraction: {nn:.1%}", flush=True)
    return df, float(nn)


# ---------- 4. ground stats join -------------------------------------------

def join_ground(df: pd.DataFrame) -> pd.DataFrame:
    if not GROUND_PARQUET.exists():
        print("  no ground_stats.parquet — skipping ground features")
        df["ground_avg_2y"] = np.nan
        df["ground_overs_avg"] = np.nan
        df["ground_n_2y"] = 0
        return df
    g = pd.read_parquet(GROUND_PARQUET)
    df = df.merge(g[["match_id", "ground_avg_2y", "ground_overs_avg", "ground_n_2y"]],
                  on="match_id", how="left")
    nn = df["ground_avg_2y"].notna().mean()
    print(f"  ground stats non-null fraction: {nn:.1%}", flush=True)
    return df


# ---------- 5. remaining batters aggregate (per (match, wickets-down)) ----

def join_remaining_batters(df: pd.DataFrame, con) -> pd.DataFrame:
    """
    For each ball, aggregate skill of batters who haven't yet come to crease.
    Position threshold = wickets + 3 (the "next in" position).
    Pre-computes per (match_id, K) → aggregates of positions [K+1, ..., 11].
    """
    print("  joining remaining-batters aggregates ...", flush=True)
    t0 = time.perf_counter()
    bat = pd.read_sql(
        "SELECT match_id, innings_seq, position, batsman_id "
        "FROM batting WHERE innings_seq = 1",
        con,
    )
    bat = bat[bat["position"].notna()].copy()
    bat["position"] = pd.to_numeric(bat["position"], errors="coerce").astype("Int64")
    bat["batsman_id"] = bat["batsman_id"].astype("Int64")

    skill = pd.read_parquet(SKILL_PARQUET)
    skill["player_id"] = skill["player_id"].astype("Int64")
    skill["snapshot_yyyymm"] = skill["snapshot_yyyymm"].astype("Int64")

    # one snap_ym per match
    match_snap = df[["match_id", "snap_ym"]].drop_duplicates()
    bat = bat.merge(match_snap, on="match_id", how="inner")
    bat = bat.merge(
        skill[["player_id", "snapshot_yyyymm",
               "bat_avg_skill", "bat_sr_skill"]].rename(
            columns={"player_id": "batsman_id", "snapshot_yyyymm": "snap_ym"}),
        on=["batsman_id", "snap_ym"], how="left",
    )

    # for each (match_id, K), aggregate skill of positions > K
    # K can be 0..10 (corresponds to 0..10 wickets fallen)
    out = []
    for K in range(0, 11):
        sub = bat[bat["position"] > K + 2]  # positions strictly > K+2 i.e. yet-to-come
        agg = sub.groupby("match_id").agg(
            bat_remaining_avg_mean=("bat_avg_skill", "mean"),
            bat_remaining_avg_min=("bat_avg_skill", "min"),
            bat_remaining_sr_mean=("bat_sr_skill", "mean"),
            bat_remaining_sr_min=("bat_sr_skill", "min"),
            bat_remaining_count_real=("bat_avg_skill", "count"),
        ).reset_index()
        agg["wickets_key"] = K
        out.append(agg)
    rem_bat = pd.concat(out, ignore_index=True)
    df["wickets_key"] = df["wickets"].clip(0, 10).astype(int)
    df = df.merge(rem_bat, on=["match_id", "wickets_key"], how="left")
    df = df.drop(columns=["wickets_key"])
    nn = df["bat_remaining_avg_mean"].notna().mean()
    print(f"    bat_remaining non-null fraction: {nn:.1%}  ({time.perf_counter() - t0:.0f}s)",
          flush=True)
    return df


# ---------- 6. bowling team strength (static per-match) -------------------

def join_bowl_team_strength(df: pd.DataFrame, con) -> pd.DataFrame:
    """
    For each match, aggregate skill of recognised bowlers in the bowling
    team's match_players. Static feature — same for every ball in a match.
    """
    print("  joining bowling-team strength ...", flush=True)
    t0 = time.perf_counter()

    # bowl_team_side already on df from load_balls()

    mp = pd.read_sql(
        "SELECT match_id, team_side, player_id FROM match_players",
        con,
    )
    mp = mp.rename(columns={"team_side": "bowl_team_side"})
    mp["player_id"] = pd.to_numeric(mp["player_id"], errors="coerce").astype("Int64")

    skill = pd.read_parquet(SKILL_PARQUET)
    skill["player_id"] = skill["player_id"].astype("Int64")
    skill["snapshot_yyyymm"] = skill["snapshot_yyyymm"].astype("Int64")

    match_snap = df[["match_id", "snap_ym", "bowl_team_side"]].drop_duplicates()

    bowl_lineup = mp.merge(match_snap, on=["match_id", "bowl_team_side"], how="inner")
    bowl_lineup = bowl_lineup.merge(
        skill[["player_id", "snapshot_yyyymm",
               "bowl_econ_skill", "bowl_avg_skill", "bowl_sr_skill"]].rename(
            columns={"snapshot_yyyymm": "snap_ym"}),
        on=["player_id", "snap_ym"], how="left",
    )
    # restrict to recognised bowlers (non-NaN bowl_econ_skill)
    bowl_lineup = bowl_lineup[bowl_lineup["bowl_econ_skill"].notna()]
    agg = bowl_lineup.groupby("match_id").agg(
        bowl_team_econ_mean=("bowl_econ_skill", "mean"),
        bowl_team_econ_max=("bowl_econ_skill", "max"),
        bowl_team_avg_mean=("bowl_avg_skill", "mean"),
        bowl_team_avg_max=("bowl_avg_skill", "max"),
        bowl_team_sr_mean=("bowl_sr_skill", "mean"),
        bowl_team_sr_max=("bowl_sr_skill", "max"),
        bowl_team_recognised_n=("bowl_econ_skill", "count"),
    ).reset_index()
    df = df.merge(agg, on="match_id", how="left")
    nn = df["bowl_team_econ_mean"].notna().mean()
    print(f"    bowl_team_strength non-null: {nn:.1%}  ({time.perf_counter() - t0:.0f}s)",
          flush=True)
    return df


# ---------- 7. train --------------------------------------------------------

V1_FEATURES = [
    "overs_per_innings", "balls_gone", "balls_left", "frac_innings",
    "runs", "wickets", "run_rate", "wickets_in_hand",
    "striker_runs_so_far", "striker_balls_so_far", "striker_intra_sr",
    "ns_runs_so_far", "ns_balls_so_far", "ns_intra_sr",
    "bowler_balls_in_innings", "bowler_runs_in_innings",
    "bowler_wkts_in_innings", "bowler_econ_so_far",
    "batters_remaining_count",
]
SKILL_FEATURE_COLS = (
    [f"striker_{k}" for k in SKILL_COLS]
    + [f"ns_{k}" for k in SKILL_COLS]
    + [f"bowler_{k}" for k in SKILL_COLS]
)
CONTEXT = ["season", "match_month"]
GROUND_FEATS = ["ground_avg_2y", "ground_overs_avg", "ground_n_2y"]
BAT_REM_FEATS = ["bat_remaining_avg_mean", "bat_remaining_avg_min",
                 "bat_remaining_sr_mean", "bat_remaining_sr_min",
                 "bat_remaining_count_real"]
BOWL_TEAM_FEATS = ["bowl_team_econ_mean", "bowl_team_econ_max",
                   "bowl_team_avg_mean", "bowl_team_avg_max",
                   "bowl_team_sr_mean", "bowl_team_sr_max",
                   "bowl_team_recognised_n"]
ALL_FEATURES = (V1_FEATURES + SKILL_FEATURE_COLS + CONTEXT
                + GROUND_FEATS + BAT_REM_FEATS + BOWL_TEAM_FEATS)


def fit(train_df, val_df, q, feats):
    m = lgb.LGBMRegressor(
        objective="quantile", alpha=q,
        n_estimators=600, learning_rate=0.05, num_leaves=63,
        min_data_in_leaf=20, verbose=-1, importance_type="gain",
    )
    m.fit(
        train_df[feats], train_df[TARGET],
        eval_set=[(val_df[feats], val_df[TARGET])],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    return m


def naive(df):
    return df["runs"].astype(float) + df["run_rate"].astype(float) * (df["balls_left"] / 6.0)


def per_position(df, preds):
    df = df.copy()
    df["err"] = (preds - df[TARGET]).abs()
    bins = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 1000]
    labels = ["0-29","30-59","60-89","90-119","120-149","150-179","180-209","210-239","240-269","270+"]
    df["bin"] = pd.cut(df["balls_gone"], bins=bins, right=False, labels=labels)
    return df.groupby("bin", observed=True)["err"].mean().reset_index()


def main() -> int:
    print("== loading universe + balls ==", flush=True)
    con = sqlite3.connect(DB)
    universe_ids = load_universe_match_ids_from_db(con)
    print(f"  universe match ids: {len(universe_ids):,}")
    con.close()

    print("\n== loading balls from SQL ==", flush=True)
    balls, con = load_balls(universe_ids)

    print("\n== computing state ==", flush=True)
    df = add_state(balls)

    print("\n== joining skill ==", flush=True)
    df, nn_skill = join_skill(df)

    print("\n== joining ground stats ==", flush=True)
    df = join_ground(df)

    print("\n== joining remaining-batters aggregate ==", flush=True)
    df = join_remaining_batters(df, con)

    print("\n== joining bowling-team strength ==", flush=True)
    df = join_bowl_team_strength(df, con)
    con.close()

    # subsample matches to fit in RAM + downcast floats to f32
    MAX_MATCHES = 20000
    rng = np.random.default_rng(seed=42)
    mids = df["match_id"].unique()
    if len(mids) > MAX_MATCHES:
        mids = rng.choice(mids, MAX_MATCHES, replace=False)
        df = df[df["match_id"].isin(mids)].reset_index(drop=True)
        print(f"\n== subsampled to {MAX_MATCHES} matches / {len(df):,} balls ==")
    rng.shuffle(mids)
    n_val = max(1, int(round(0.25 * len(mids))))
    val_ids = set(mids[:n_val].tolist())
    train_ids = set(mids[n_val:].tolist())
    train_df = df[df["match_id"].isin(train_ids)].reset_index(drop=True)
    val_df = df[df["match_id"].isin(val_ids)].reset_index(drop=True)

    # downcast float64 → float32 to halve RAM for LGBM training
    for d in (train_df, val_df):
        f64 = d.select_dtypes(include=["float64"]).columns
        d[f64] = d[f64].astype("float32")
    print(f"\n== train/val ==")
    print(f"  train: {len(train_ids):,} matches / {len(train_df):,} balls")
    print(f"  val:   {len(val_ids):,} matches / {len(val_df):,} balls")

    print("\n== fitting v1 baseline (state only) ==", flush=True)
    m1 = fit(train_df, val_df, 0.5, V1_FEATURES)

    print("== fitting v3c (+ skill + context) ==", flush=True)
    v3c_feats = V1_FEATURES + SKILL_FEATURE_COLS + CONTEXT
    m3c = fit(train_df, val_df, 0.5, v3c_feats)

    print("== fitting v3d (+ ground + remaining-bat + bowl-team) ==", flush=True)
    m3d_10 = fit(train_df, val_df, 0.1, ALL_FEATURES)
    m3d_50 = fit(train_df, val_df, 0.5, ALL_FEATURES)
    m3d_90 = fit(train_df, val_df, 0.9, ALL_FEATURES)

    val = val_df.copy()
    val["v1_p50"] = m1.predict(val[V1_FEATURES])
    val["v3c_p50"] = m3c.predict(val[v3c_feats])
    val["v3d_p10"] = m3d_10.predict(val[ALL_FEATURES])
    val["v3d_p50"] = m3d_50.predict(val[ALL_FEATURES])
    val["v3d_p90"] = m3d_90.predict(val[ALL_FEATURES])
    val["naive"] = naive(val)

    mae_v1 = (val["v1_p50"] - val[TARGET]).abs().mean()
    mae_v3c = (val["v3c_p50"] - val[TARGET]).abs().mean()
    mae_v3d = (val["v3d_p50"] - val[TARGET]).abs().mean()
    mae_naive = (val["naive"] - val[TARGET]).abs().mean()
    cov = ((val[TARGET] >= val["v3d_p10"]) & (val[TARGET] <= val["v3d_p90"])).mean()

    print(f"\n== overall (val) ==")
    print(f"  MAE naive RR: {mae_naive:.2f}")
    print(f"  MAE v1:       {mae_v1:.2f}")
    print(f"  MAE v3c:      {mae_v3c:.2f}  (Δ {mae_v3c - mae_v1:+.2f} vs v1)")
    print(f"  MAE v3d:      {mae_v3d:.2f}  (Δ {mae_v3d - mae_v3c:+.2f} vs v3c, "
          f"{mae_v3d - mae_v1:+.2f} vs v1)")
    print(f"  v3d 80% interval coverage: {cov:.3f}")

    bin_v1 = per_position(val, val["v1_p50"].values)
    bin_v3c = per_position(val, val["v3c_p50"].values)
    bin_v3d = per_position(val, val["v3d_p50"].values)
    bin_naive = per_position(val, val["naive"].values)
    bins = (bin_naive.merge(bin_v1, on="bin", suffixes=("_naive", "_v1"))
            .merge(bin_v3c.rename(columns={"err": "err_v3c"}), on="bin")
            .merge(bin_v3d.rename(columns={"err": "err_v3d"}), on="bin"))
    print("\n== per-position MAE ==")
    print(bins.to_string(index=False))

    imp = pd.DataFrame({
        "feature": ALL_FEATURES,
        "gain": m3d_50.feature_importances_,
    }).sort_values("gain", ascending=False)
    print("\n== top 25 by GAIN ==")
    print(imp.head(25).to_string(index=False))

    md = []
    md.append("# POC v3d — + ground stats + remaining-batters + bowl-team strength")
    md.append("")
    md.append(f"- Train: **{len(train_ids):,}** matches / {len(train_df):,} balls")
    md.append(f"- Val:   **{len(val_ids):,}** matches / {len(val_df):,} balls")
    md.append(f"- Skill non-null after join: {nn_skill:.1%}")
    md.append(f"- Universe filter at query time: **on**")
    md.append("")
    md.append("## Headline (held-out val)")
    md.append("")
    md.append("| Model | MAE | Δ vs v1 | Δ vs v3c |")
    md.append("|---|---:|---:|---:|")
    md.append(f"| naive RR | {mae_naive:.1f} | — | — |")
    md.append(f"| v1 (state only) | {mae_v1:.1f} | (baseline) | — |")
    md.append(f"| v3c (+ skill + context) | {mae_v3c:.1f} | {mae_v3c - mae_v1:+.1f} | (baseline) |")
    md.append(f"| v3d (+ ground + remaining-bat + bowl-team) | **{mae_v3d:.1f}** | **{mae_v3d - mae_v1:+.1f}** | **{mae_v3d - mae_v3c:+.1f}** |")
    md.append("")
    md.append(f"v3d 80% interval coverage: **{cov:.2f}**")
    md.append("")
    md.append("## Per-ball-position MAE")
    md.append("")
    md.append("| balls in | naive | v1 | v3c | v3d | Δ v3d–v3c |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for _, r in bins.iterrows():
        md.append(f"| {r['bin']} | {r['err_naive']:.1f} | {r['err_v1']:.1f} | "
                  f"{r['err_v3c']:.1f} | {r['err_v3d']:.1f} | "
                  f"{r['err_v3d'] - r['err_v3c']:+.1f} |")
    md.append("")
    md.append("## Top 25 features by gain")
    md.append("")
    md.append("| feature | gain |")
    md.append("|---|---:|")
    for _, r in imp.head(25).iterrows():
        md.append(f"| `{r['feature']}` | {int(r['gain']):,} |")

    (OUT / "results_v3d.md").write_text("\n".join(md) + "\n")
    (OUT / "results_v3d.json").write_text(json.dumps({
        "mae_naive": float(mae_naive),
        "mae_v1": float(mae_v1),
        "mae_v3c": float(mae_v3c),
        "mae_v3d": float(mae_v3d),
        "v3d_interval_coverage_80": float(cov),
        "n_train_matches": len(train_ids),
        "n_val_matches": len(val_ids),
        "skill_nn_frac": float(nn_skill),
        "bins": bins.to_dict(orient="records"),
        "top_features": imp.head(25).to_dict(orient="records"),
    }, indent=2, default=str))
    print("\nwrote model/poc/results_v3d.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
