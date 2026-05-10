#!/usr/bin/env python3
"""
Win-probability chase model — innings 2.

For each ball in innings 2 of a Limited-Overs universe match, predict
P(chasing side wins). Adds chase-specific features (target, target_remaining,
req_run_rate, runs_diff_vs_par) on top of the v3d feature set.

Combines with the innings-1 model (run_winprob_v0.py) downstream.

Output:
  model/poc/results_winprob_chase.md
  model/poc/results_winprob_chase.json
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
from sklearn.metrics import brier_score_loss, log_loss

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "rainham.db"
SKILL_PARQUET = ROOT / "data" / "skill_monthly.parquet"
GROUND_PARQUET = ROOT / "data" / "ground_stats.parquet"
BENCH = ROOT / "model" / "bench"
OUT = Path(__file__).resolve().parent

SKILL_COLS = ["bat_avg_skill", "bat_sr_skill",
              "bowl_econ_skill", "bowl_avg_skill", "bowl_sr_skill"]


# ----- universe + abandoned filter (mirrors v3d) ---------------------------

def load_universe_match_ids(con):
    cids = set()
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
                        cids.add(int(div["cid"]))
                    except (KeyError, TypeError, ValueError):
                        pass
    if not cids:
        return set()
    placeholders = ",".join("?" * len(cids))
    rows = con.execute(
        f"SELECT match_id FROM matches WHERE competition_id IN ({placeholders})",
        list(cids),
    ).fetchall()
    return {r[0] for r in rows}


def load_innings2(universe_ids: set[int]) -> tuple[pd.DataFrame, sqlite3.Connection]:
    con = sqlite3.connect(DB)
    print("  reading matches metadata ...", flush=True)
    matches = pd.read_sql(
        "SELECT match_id, match_date, match_type, "
        "       home_team_id, away_team_id, "
        "       home_club_id, away_club_id, ground_id, "
        "       result, result_description, result_applied_to "
        "FROM matches WHERE match_type = 'Limited Overs'",
        con,
    )
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
        print(f"    universe filter: {len(matches):,} of {before:,}", flush=True)

    valid = matches["result"].isin(["W", "L"])
    not_aban = ~matches["result_description"].fillna("").str.contains(
        "Abandon|No Result|No decision|Cancelled", case=False, regex=True
    )
    matches = matches[valid & not_aban]
    print(f"    after dropping abandoned/cancelled/draws/ties: {len(matches):,}", flush=True)

    # innings 1 final and 2 final from `innings` (post-realign)
    inns = pd.read_sql(
        "SELECT match_id, innings_seq, team_batting_club_id, runs FROM innings",
        con,
    )
    inns["runs"] = pd.to_numeric(inns["runs"], errors="coerce")

    # innings 2's chasing team's club, plus the target = innings 1 runs
    target = inns[inns["innings_seq"] == 1].rename(
        columns={"team_batting_club_id": "i1_club", "runs": "target"}
    )[["match_id", "i1_club", "target"]]
    chase = inns[inns["innings_seq"] == 2].rename(
        columns={"team_batting_club_id": "i2_club", "runs": "i2_final"}
    )[["match_id", "i2_club", "i2_final"]]
    inns_join = matches[["match_id", "snap_ym", "season", "match_month",
                          "ground_id", "home_team_id", "away_team_id",
                          "home_club_id", "away_club_id",
                          "result", "result_applied_to"]].merge(
        target, on="match_id", how="inner"
    ).merge(chase, on="match_id", how="inner")

    # bowling team in innings 2 = team that batted in innings 1
    inns_join["batting_club_inn2"] = inns_join["i2_club"].astype(str)
    inns_join["bowling_club_inn2"] = inns_join["i1_club"].astype(str)
    # bowl_team_side: which side (home/away) is bowling in innings 2
    inns_join["home_club_id_s"] = inns_join["home_club_id"].astype(str)
    inns_join["bowl_team_side"] = np.where(
        inns_join["bowling_club_inn2"] == inns_join["home_club_id_s"], "home", "away"
    )

    # chase succeeded if i2 batting team won
    # winning_team_id derived from result + result_applied_to
    inns_join["result_applied_to"] = inns_join["result_applied_to"].fillna("").astype(str)
    inns_join["i2_team_id"] = np.where(
        inns_join["bowl_team_side"] == "home",
        inns_join["away_team_id"], inns_join["home_team_id"],
    ).astype(str)
    inns_join["winning_team"] = np.where(
        inns_join["result"] == "W", inns_join["result_applied_to"],
        np.where(inns_join["result_applied_to"] == inns_join["home_team_id"].astype(str),
                  inns_join["away_team_id"].astype(str),
                  inns_join["home_team_id"].astype(str))
    )
    inns_join["y_chase_won"] = (inns_join["winning_team"] == inns_join["i2_team_id"]).astype(int)
    print(f"    chase rows: {len(inns_join):,}  (won {inns_join['y_chase_won'].sum():,} = "
          f"{inns_join['y_chase_won'].mean():.3f})", flush=True)

    print("  reading balls (innings 2) ...", flush=True)
    balls = pd.read_sql(
        "SELECT match_id, ball_no, ball_no_disp, over_no, "
        "       batter_id, non_striker_id, bowler_id, "
        "       team_batting_club_id, "
        "       runs_bat, runs_extra, extras_type, is_legal_ball, "
        "       dismissed_batter_id "
        "FROM balls WHERE innings_seq = 2",
        con,
    )
    print(f"    {len(balls):,} ball rows from innings 2", flush=True)

    balls = balls.merge(inns_join[[
        "match_id", "season", "match_month", "snap_ym", "ground_id",
        "target", "y_chase_won", "bowl_team_side"
    ]], on="match_id", how="inner")
    print(f"    after join: {len(balls):,} rows / {balls['match_id'].nunique():,} matches", flush=True)
    return balls, con


# ----- per-ball chase state ------------------------------------------------

def add_chase_state(balls: pd.DataFrame) -> pd.DataFrame:
    print("  computing per-ball chase state ...", flush=True)
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

    # overs_per_innings — same heuristic as v3d
    legal_per_match = df.groupby("match_id")["is_legal_ball"].sum().rename("legal_total_innings_2")
    df = df.merge(legal_per_match, left_on="match_id", right_index=True)
    # use innings 1's ovs typically — but we don't have it here directly.
    # Hueristic clamp by team's max over_no in innings 2: chases run through
    # the same ball cap as innings 1.
    max_over_per_match = df.groupby("match_id")["over_no"].max().rename("max_over_seen")
    df = df.merge(max_over_per_match, left_on="match_id", right_index=True)
    df["overs_per_innings"] = df["max_over_seen"].apply(
        lambda o: 50 if o >= 40 else 45 if o >= 35 else 40 if o >= 30 else 50
    )
    df["legal_total"] = (df["overs_per_innings"] * 6).astype(int)
    df["balls_left"] = (df["legal_total"] - df["balls_gone"]).clip(lower=0)
    df["frac_innings"] = df["balls_gone"] / df["legal_total"]
    df["run_rate"] = np.where(df["balls_gone"] > 0,
                               df["runs"] * 6.0 / df["balls_gone"], 0.0)
    df["wickets_in_hand"] = (10 - df["wickets"]).clip(lower=0)
    df["batters_remaining_count"] = (11 - df["wickets"] - 2).clip(lower=0)

    # CHASE-SPECIFIC features
    df["target"] = pd.to_numeric(df["target"], errors="coerce")
    df["target_remaining"] = (df["target"] - df["runs"]).clip(lower=0)
    df["balls_left_safe"] = df["balls_left"].clip(lower=1)
    df["req_rr"] = df["target_remaining"] * 6.0 / df["balls_left_safe"]
    df["rr_diff"] = df["run_rate"] - df["req_rr"]   # +ve = ahead, -ve = behind
    df["target_progress"] = df["runs"] / df["target"].clip(lower=1)
    df = df.rename(columns={"batter_id": "striker_id", "non_striker_id": "ns_id"})

    # batter intra-state (skipping non-striker for parity with v3d)
    df["bat_runs_event"] = df["runs_bat"]
    df["bat_balls_event"] = (df["extras_type"] != 2).astype(int)
    df = df.sort_values(["match_id", "striker_id", "over_no", "ball_no"]).reset_index(drop=True)
    g_b = df.groupby(["match_id", "striker_id"], sort=False, dropna=False)
    df["striker_runs_so_far"] = (g_b["bat_runs_event"].cumsum() - df["bat_runs_event"]).fillna(0).astype(int)
    df["striker_balls_so_far"] = (g_b["bat_balls_event"].cumsum() - df["bat_balls_event"]).fillna(0).astype(int)
    df["striker_intra_sr"] = np.where(
        df["striker_balls_so_far"] > 0,
        df["striker_runs_so_far"] * 100.0 / df["striker_balls_so_far"], np.nan,
    )

    # bowler intra-state
    df = df.sort_values(["match_id", "bowler_id", "over_no", "ball_no"]).reset_index(drop=True)
    g_w = df.groupby(["match_id", "bowler_id"], sort=False, dropna=False)
    df["bowler_balls_in_innings"] = (g_w["is_legal_ball"].cumsum() - df["is_legal_ball"]).fillna(0).astype(int)
    df["bowler_runs_in_innings"] = (g_w["delivery_runs"].cumsum() - df["delivery_runs"]).fillna(0).astype(int)
    df["bowler_wkts_in_innings"] = (g_w["is_wicket"].cumsum() - df["is_wicket"]).fillna(0).astype(int)
    df["bowler_econ_so_far"] = np.where(
        df["bowler_balls_in_innings"] > 0,
        df["bowler_runs_in_innings"] * 6.0 / df["bowler_balls_in_innings"], np.nan,
    )

    df = df.sort_values(["match_id", "over_no", "ball_no"]).reset_index(drop=True)
    return df


# ----- joins (skill, ground, remaining-bat for innings 2, bowl-team) -------

def join_features(df: pd.DataFrame, con) -> pd.DataFrame:
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
    print(f"  striker skill non-null: {df['striker_bat_avg_skill'].notna().mean():.1%}")

    if GROUND_PARQUET.exists():
        g = pd.read_parquet(GROUND_PARQUET)
        df = df.merge(g[["match_id", "ground_avg_2y", "ground_overs_avg", "ground_n_2y"]],
                      on="match_id", how="left")
        print(f"  ground non-null: {df['ground_avg_2y'].notna().mean():.1%}")
    else:
        df["ground_avg_2y"] = np.nan
        df["ground_overs_avg"] = np.nan
        df["ground_n_2y"] = 0

    # remaining-batters from innings 2 batting card
    bat = pd.read_sql(
        "SELECT match_id, position, batsman_id "
        "FROM batting WHERE innings_seq = 2 AND position IS NOT NULL",
        con,
    )
    bat["position"] = pd.to_numeric(bat["position"], errors="coerce").astype("Int64")
    bat["batsman_id"] = bat["batsman_id"].astype("Int64")
    match_snap = df[["match_id", "snap_ym"]].drop_duplicates()
    bat = bat.merge(match_snap, on="match_id", how="inner")
    bat = bat.merge(
        skill[["player_id", "snapshot_yyyymm",
               "bat_avg_skill", "bat_sr_skill"]].rename(
            columns={"player_id": "batsman_id", "snapshot_yyyymm": "snap_ym"}),
        on=["batsman_id", "snap_ym"], how="left",
    )
    out = []
    for K in range(0, 11):
        sub = bat[bat["position"] > K + 2]
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
    print(f"  bat_remaining non-null: {df['bat_remaining_avg_mean'].notna().mean():.1%}")

    # bowl team strength (defending team = the one that batted innings 1)
    mp = pd.read_sql(
        "SELECT match_id, team_side, player_id FROM match_players", con)
    mp["player_id"] = pd.to_numeric(mp["player_id"], errors="coerce").astype("Int64")
    mp["team_side"] = mp["team_side"].astype(str).str.lower()
    # bowl team in innings 2 = the side that bowls; from df's bowl_team_side
    side_id = df[["match_id", "bowl_team_side"]].drop_duplicates()
    side_id["bowl_team_side"] = side_id["bowl_team_side"].astype(str).str.lower()
    bowl_lineup = mp.merge(side_id, left_on=["match_id", "team_side"],
                           right_on=["match_id", "bowl_team_side"], how="inner")
    bowl_lineup = bowl_lineup.merge(match_snap, on="match_id", how="inner")
    bowl_lineup = bowl_lineup.merge(
        skill[["player_id", "snapshot_yyyymm",
               "bowl_econ_skill", "bowl_avg_skill", "bowl_sr_skill"]].rename(
            columns={"snapshot_yyyymm": "snap_ym"}),
        on=["player_id", "snap_ym"], how="left",
    )
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
    print(f"  bowl_team_strength non-null: {df['bowl_team_econ_mean'].notna().mean():.1%}")
    return df


# ----- features ------------------------------------------------------------

V1_FEATURES = [
    "overs_per_innings", "balls_gone", "balls_left", "frac_innings",
    "runs", "wickets", "run_rate", "wickets_in_hand",
    "striker_runs_so_far", "striker_balls_so_far", "striker_intra_sr",
    "bowler_balls_in_innings", "bowler_runs_in_innings",
    "bowler_wkts_in_innings", "bowler_econ_so_far",
    "batters_remaining_count",
]
SKILL_F = ([f"striker_{k}" for k in SKILL_COLS]
           + [f"ns_{k}" for k in SKILL_COLS]
           + [f"bowler_{k}" for k in SKILL_COLS])
CONTEXT = ["season", "match_month"]
GROUND_F = ["ground_avg_2y", "ground_overs_avg", "ground_n_2y"]
BAT_REM_F = ["bat_remaining_avg_mean", "bat_remaining_avg_min",
              "bat_remaining_sr_mean", "bat_remaining_sr_min",
              "bat_remaining_count_real"]
BOWL_TEAM_F = ["bowl_team_econ_mean", "bowl_team_econ_max",
               "bowl_team_avg_mean", "bowl_team_avg_max",
               "bowl_team_sr_mean", "bowl_team_sr_max",
               "bowl_team_recognised_n"]
CHASE_F = ["target", "target_remaining", "req_rr", "rr_diff", "target_progress"]
ALL_F = (V1_FEATURES + SKILL_F + CONTEXT + GROUND_F + BAT_REM_F
         + BOWL_TEAM_F + CHASE_F)


def main() -> int:
    print("== loading universe + chase balls ==", flush=True)
    con = sqlite3.connect(DB)
    universe_ids = load_universe_match_ids(con)
    con.close()
    print(f"  universe match ids: {len(universe_ids):,}")

    balls, con = load_innings2(universe_ids)
    # subsample early — well before the heavy state computation + joins
    MAX_MATCHES = 14000
    rng = np.random.default_rng(seed=42)
    pre_mids = balls["match_id"].unique()
    if len(pre_mids) > MAX_MATCHES:
        pre_mids = rng.choice(pre_mids, MAX_MATCHES, replace=False)
        balls = balls[balls["match_id"].isin(pre_mids)].reset_index(drop=True)
        print(f"  subsampled to {MAX_MATCHES} matches / {len(balls):,} rows")
    # downcast int64 ids early to halve RAM
    for c in ("batter_id", "non_striker_id", "bowler_id", "match_id"):
        if c in balls.columns:
            balls[c] = pd.to_numeric(balls[c], errors="coerce").astype("Int32" if c == "match_id" else "Int64")

    df = add_chase_state(balls)
    df["ns_runs_so_far"] = np.nan
    df["ns_balls_so_far"] = np.nan
    df["ns_intra_sr"] = np.nan
    df = join_features(df, con)
    con.close()
    print(f"\nfeature df: {len(df):,} balls / {df['match_id'].nunique():,} matches")

    rng.shuffle(pre_mids)
    n_val = max(1, int(round(0.25 * len(pre_mids))))
    val_ids = set(pre_mids[:n_val].tolist())
    train_ids = set(pre_mids[n_val:].tolist())
    train_df = df[df["match_id"].isin(train_ids)].reset_index(drop=True)
    val_df = df[df["match_id"].isin(val_ids)].reset_index(drop=True)
    for d in (train_df, val_df):
        f64 = d.select_dtypes(include=["float64"]).columns
        d[f64] = d[f64].astype("float32")
    print(f"\ntrain: {len(train_ids)} matches / {len(train_df):,} balls")
    print(f"val:   {len(val_ids)} matches / {len(val_df):,} balls")

    print("\n== fitting chase classifier ==", flush=True)
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=600, learning_rate=0.05,
        num_leaves=63, min_data_in_leaf=20, verbose=-1,
        importance_type="gain",
    )
    m.fit(
        train_df[ALL_F], train_df["y_chase_won"],
        eval_set=[(val_df[ALL_F], val_df["y_chase_won"])],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )

    val = val_df.copy()
    val["p_chase"] = m.predict_proba(val[ALL_F])[:, 1]
    bs = brier_score_loss(val["y_chase_won"], val["p_chase"])
    ll = log_loss(val["y_chase_won"], np.clip(val["p_chase"], 1e-6, 1 - 1e-6))
    base_rate = val_df["y_chase_won"].mean()
    bs_baseline = brier_score_loss(val["y_chase_won"],
                                    np.full(len(val), base_rate))
    print(f"\n== overall (val) ==")
    print(f"  base-rate (constant {base_rate:.3f}) Brier: {bs_baseline:.4f}")
    print(f"  chase-prob               Brier: {bs:.4f}")
    print(f"  chase-prob               log-loss: {ll:.4f}")
    print(f"  Brier skill score: {1 - bs/bs_baseline:.3f}")

    # calibration
    val["pbin"] = pd.cut(val["p_chase"], bins=np.linspace(0, 1, 11),
                         include_lowest=True, right=False)
    cal = val.groupby("pbin", observed=True).agg(
        n=("y_chase_won", "size"),
        mean_pred=("p_chase", "mean"),
        empirical=("y_chase_won", "mean"),
    ).reset_index()
    print("\n== calibration ==")
    print(cal.to_string(index=False))

    # per-position
    val["ball_bin"] = pd.cut(val["balls_gone"],
                              bins=[0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 1000],
                              right=False,
                              labels=["0-29","30-59","60-89","90-119","120-149",
                                      "150-179","180-209","210-239","240-269","270+"])
    pos = val.groupby("ball_bin", observed=True).apply(
        lambda g: pd.Series({
            "n": len(g),
            "brier": brier_score_loss(g["y_chase_won"], g["p_chase"]),
        })
    ).reset_index()
    print("\n== per-position Brier ==")
    print(pos.to_string(index=False))

    # feature importance
    imp = pd.DataFrame({"feature": ALL_F,
                        "gain": m.feature_importances_}).sort_values("gain", ascending=False)
    print("\n== top 20 features ==")
    print(imp.head(20).to_string(index=False))

    # examples — pick 4 chases
    val_match_meta = val[["match_id", "y_chase_won", "target"]].drop_duplicates()
    sample_mids = val_match_meta.sample(4, random_state=7)["match_id"].tolist()
    examples = []
    for mid in sample_mids:
        m_rows = val[val["match_id"] == mid].sort_values("balls_gone")
        if len(m_rows) < 30:
            continue
        target = int(m_rows["target"].iloc[0])
        traj = []
        for cp in (30, 60, 90, 120, 150, 180, 210, 240):
            sub = m_rows[m_rows["balls_gone"] >= cp].head(1)
            if not sub.empty:
                r = sub.iloc[0]
                traj.append({
                    "balls": int(r["balls_gone"]),
                    "runs": int(r["runs"]),
                    "wickets": int(r["wickets"]),
                    "target_remaining": int(r["target_remaining"]),
                    "req_rr": round(float(r["req_rr"]), 2),
                    "p_chase": round(float(r["p_chase"]), 3),
                })
        examples.append({
            "match_id": int(mid),
            "target": target,
            "y_chase_won": int(m_rows["y_chase_won"].iloc[0]),
            "trajectory": traj,
        })

    md = []
    md.append("# Win probability — innings 2 chase model")
    md.append("")
    md.append(f"- Train: **{len(train_ids):,}** matches / {len(train_df):,} balls")
    md.append(f"- Val:   **{len(val_ids):,}** matches / {len(val_df):,} balls")
    md.append(f"- Base chase-success rate: **{base_rate:.3f}**")
    md.append("")
    md.append("## Headline (held-out val)")
    md.append("")
    md.append("| Metric | Value |")
    md.append("|---|---:|")
    md.append(f"| Base-rate Brier (no model) | {bs_baseline:.4f} |")
    md.append(f"| chase-prob Brier | **{bs:.4f}** |")
    md.append(f"| chase-prob log-loss | {ll:.4f} |")
    md.append(f"| Brier skill score | **{1 - bs/bs_baseline:.3f}** |")
    md.append("")
    md.append("## Calibration (10 bins of P(chase succeeds))")
    md.append("")
    md.append("| bucket | n | mean predicted | empirical chase-W |")
    md.append("|---|---:|---:|---:|")
    for _, r in cal.iterrows():
        md.append(f"| {r['pbin']} | {int(r['n']):,} | {r['mean_pred']:.3f} | {r['empirical']:.3f} |")
    md.append("")
    md.append("## Per-ball-position Brier")
    md.append("")
    md.append("| balls into chase | n | Brier |")
    md.append("|---|---:|---:|")
    for _, r in pos.iterrows():
        md.append(f"| {r['ball_bin']} | {int(r['n']):,} | {r['brier']:.4f} |")
    md.append("")
    md.append("## Top 20 features by gain")
    md.append("")
    md.append("| feature | gain |")
    md.append("|---|---:|")
    for _, r in imp.head(20).iterrows():
        md.append(f"| `{r['feature']}` | {int(r['gain']):,} |")
    md.append("")
    md.append("## Example chase trajectories")
    md.append("")
    for ex in examples:
        outcome = "✓ chased" if ex["y_chase_won"] else "✗ failed"
        md.append(f"### match {ex['match_id']} — chasing **{ex['target']}** — {outcome}")
        md.append("")
        md.append("| balls in | runs | wkts | need | req RR | P(chase) |")
        md.append("|---:|---:|---:|---:|---:|---:|")
        for t in ex["trajectory"]:
            md.append(f"| {t['balls']} | {t['runs']} | {t['wickets']} | "
                      f"{t['target_remaining']} | {t['req_rr']:.2f} | {t['p_chase']:.2f} |")
        md.append("")

    (OUT / "results_winprob_chase.md").write_text("\n".join(md) + "\n")
    (OUT / "results_winprob_chase.json").write_text(json.dumps({
        "n_train_matches": len(train_ids),
        "n_val_matches": len(val_ids),
        "base_rate": float(base_rate),
        "brier_baseline": float(bs_baseline),
        "brier": float(bs),
        "log_loss": float(ll),
        "brier_skill_score": float(1 - bs / bs_baseline),
        "calibration": cal.to_dict(orient="records"),
        "per_position": pos.to_dict(orient="records"),
        "top_features": imp.head(20).to_dict(orient="records"),
        "examples": examples,
    }, indent=2, default=str))
    print("\nwrote model/poc/results_winprob_chase.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
