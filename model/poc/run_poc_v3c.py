#!/usr/bin/env python3
"""
POC v3c — read balls from SQL (disambiguated PC IDs), join skill snapshots.

Why SQL: the raw JSON ball files use RV-namespace player_ids (11M-range),
which don't match the PC-namespace player_ids (≤7M range) used in the
batting / bowling aggregate tables, the metadata files, and the skill
snapshot. The build_db.py loader runs the disambiguation (_rv_balls.py +
_nvplay_balls.py + _disambig.py) and the SQL `balls` table carries PC
IDs only. Reading from there is the only way the skill join actually
links up.

Innings 1 only, Limited Overs only, BBB era (2021+), train/val by match_id.
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
OUT = Path(__file__).resolve().parent

SKILL_COLS = ["bat_avg_skill", "bat_sr_skill",
              "bowl_econ_skill", "bowl_avg_skill", "bowl_sr_skill"]
TARGET = "y_final_innings_runs"


# ---------- 1. load balls from SQL -----------------------------------------

def load_balls() -> pd.DataFrame:
    if not DB.exists():
        raise SystemExit(f"missing {DB}; run build_db.py first")
    con = sqlite3.connect(DB)
    print("  reading matches metadata ...", flush=True)
    matches = pd.read_sql(
        "SELECT match_id, match_date, match_type, "
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
    print(f"    BBB-era LO matches: {len(matches):,}", flush=True)

    # innings runs (final): from innings table directly
    print("  reading innings totals ...", flush=True)
    inns = pd.read_sql(
        "SELECT match_id, innings_seq, runs FROM innings WHERE innings_seq = 1",
        con,
    )
    inns = inns.rename(columns={"runs": "y_final_innings_runs"})

    # overs_per_innings — derive from innings table; balls / 6 from balls
    # table is more reliable
    print("  computing overs_per_innings from balls counts ...", flush=True)
    legal_per_match = pd.read_sql(
        "SELECT match_id, COUNT(*) AS legal_balls "
        "FROM balls WHERE innings_seq = 1 AND is_legal_ball = 1 "
        "GROUP BY match_id",
        con,
    )
    # round up legal_balls to nearest 6
    legal_per_match["overs_per_innings"] = (
        np.ceil(legal_per_match["legal_balls"] / 60.0) * 10  # heuristic floor
    )
    # better: take max(40, ceiling), but the precise value matters less than
    # the order of magnitude — clamp to known formats
    legal_per_match["overs_per_innings"] = legal_per_match["legal_balls"].apply(
        lambda b: 50 if b > 270 else 45 if b > 240 else 40 if b > 210 else 50
    )

    print("  reading balls (innings 1 only) ...", flush=True)
    balls = pd.read_sql(
        "SELECT match_id, ball_no, ball_no_disp, over_no, "
        "       batter_id, non_striker_id, bowler_id, "
        "       runs_bat, runs_extra, extras_type, is_legal_ball, "
        "       dismissed_batter_id "
        "FROM balls WHERE innings_seq = 1",
        con,
    )
    print(f"    {len(balls):,} ball rows from innings 1", flush=True)

    # join match-level fields
    balls = balls.merge(matches[["match_id", "season", "match_month",
                                 "snap_ym", "ground_id"]], on="match_id", how="inner")
    balls = balls.merge(inns[["match_id", "y_final_innings_runs"]],
                        on="match_id", how="inner")
    balls = balls.merge(legal_per_match[["match_id", "overs_per_innings"]],
                        on="match_id", how="inner")
    print(f"    after join: {len(balls):,} rows / {balls['match_id'].nunique():,} matches", flush=True)
    con.close()
    return balls


# ---------- 2. compute per-ball cumulative state ----------------------------

def add_state(balls: pd.DataFrame) -> pd.DataFrame:
    print("  computing per-ball state with pandas window funcs ...", flush=True)
    t0 = time.perf_counter()
    df = balls.sort_values(["match_id", "ball_no"]).reset_index(drop=True)
    df["runs_bat"] = pd.to_numeric(df["runs_bat"], errors="coerce").fillna(0)
    df["runs_extra"] = pd.to_numeric(df["runs_extra"], errors="coerce").fillna(0)
    df["is_legal_ball"] = pd.to_numeric(df["is_legal_ball"], errors="coerce").fillna(1).astype(int)
    df["delivery_runs"] = df["runs_bat"] + df["runs_extra"]
    df["is_wicket"] = df["dismissed_batter_id"].notna().astype(int)

    # PRE-ball cumulative (shift by 1 within match)
    g = df.groupby("match_id", sort=False)
    df["runs"] = g["delivery_runs"].cumsum().shift(1).fillna(0).astype(int)
    df["balls_gone"] = g["is_legal_ball"].cumsum().shift(1).fillna(0).astype(int)
    df["wickets"] = g["is_wicket"].cumsum().shift(1).fillna(0).astype(int)

    # legal-only filter — feature rows are emitted on legal deliveries
    df = df[df["is_legal_ball"] == 1].reset_index(drop=True)

    # batter intra-innings stats (per-match per-batter cumulative pre-ball)
    df["bat_runs_event"] = df["runs_bat"]
    df["bat_balls_event"] = (df["extras_type"] != 2).astype(int)  # not wide
    df = df.sort_values(["match_id", "batter_id", "ball_no"]).reset_index(drop=True)
    g_b = df.groupby(["match_id", "batter_id"], sort=False)
    df["striker_runs_so_far"] = g_b["bat_runs_event"].cumsum().shift(1).fillna(0).astype(int)
    df["striker_balls_so_far"] = g_b["bat_balls_event"].cumsum().shift(1).fillna(0).astype(int)
    df["striker_intra_sr"] = np.where(
        df["striker_balls_so_far"] > 0,
        df["striker_runs_so_far"] * 100.0 / df["striker_balls_so_far"], np.nan,
    )

    # non-striker stats: requires lookup of (match_id, non_striker_id) → cumulative
    # state at this ball. We can compute the same per-batter cumulative table
    # (already have it in df indexed by (match_id, batter_id)) and then look up
    # by (match_id, non_striker_id) at the same ball_no.
    bat_state = df[["match_id", "batter_id", "ball_no",
                    "striker_runs_so_far", "striker_balls_so_far"]].rename(
        columns={"batter_id": "ns_id"})  # rename for the merge
    # but we need PRE-ball state at the BALL where this batter is non-striker,
    # not striker. Simplification: assume non-striker's running state at ball N
    # is their running state from their LAST ON-STRIKE ball <= N. That's
    # close enough for this POC; full accuracy needs a sequence walk.
    # Apply asof-merge per match.
    df = df.sort_values(["match_id", "ball_no"]).reset_index(drop=True)
    bat_state = bat_state.sort_values(["match_id", "ns_id", "ball_no"]).reset_index(drop=True)

    # Approximation that's fast: take the batter's cumulative AT the previous
    # delivery they faced. Use merge_asof per group.
    parts = []
    for mid, sub in df.groupby("match_id", sort=False):
        bs_m = bat_state[bat_state["match_id"] == mid]
        if bs_m.empty:
            sub2 = sub.copy()
            sub2["ns_runs_so_far"] = np.nan
            sub2["ns_balls_so_far"] = np.nan
            parts.append(sub2)
            continue
        # asof merge per ns_id
        sub = sub.sort_values("ball_no")
        bs_m = bs_m.sort_values(["ns_id", "ball_no"])
        # backward asof: pick the most recent striker_runs_so_far for this ns_id
        merged = pd.merge_asof(
            sub.sort_values("ball_no"),
            bs_m[["ns_id", "ball_no", "striker_runs_so_far", "striker_balls_so_far"]]
                .rename(columns={"ball_no": "bs_ball_no"})
                .sort_values("bs_ball_no"),
            left_on="ball_no", right_on="bs_ball_no", by="ns_id",
            direction="backward",
        )
        merged = merged.rename(columns={
            "striker_runs_so_far_x": "striker_runs_so_far",
            "striker_balls_so_far_x": "striker_balls_so_far",
            "striker_runs_so_far_y": "ns_runs_so_far",
            "striker_balls_so_far_y": "ns_balls_so_far",
        })
        merged = merged.drop(columns=["bs_ball_no"], errors="ignore")
        parts.append(merged)
    df = pd.concat(parts, ignore_index=True)
    df["ns_intra_sr"] = np.where(
        df["ns_balls_so_far"] > 0,
        df["ns_runs_so_far"] * 100.0 / df["ns_balls_so_far"], np.nan,
    )

    # bowler intra-innings stats (per-match per-bowler cumulative)
    df = df.sort_values(["match_id", "bowler_id", "ball_no"]).reset_index(drop=True)
    g_w = df.groupby(["match_id", "bowler_id"], sort=False)
    df["bowler_balls_in_innings"] = g_w["is_legal_ball"].cumsum().shift(1).fillna(0).astype(int)
    df["bowler_runs_in_innings"] = g_w["delivery_runs"].cumsum().shift(1).fillna(0).astype(int)
    df["bowler_wkts_in_innings"] = g_w["is_wicket"].cumsum().shift(1).fillna(0).astype(int)
    df["bowler_econ_so_far"] = np.where(
        df["bowler_balls_in_innings"] > 0,
        df["bowler_runs_in_innings"] * 6.0 / df["bowler_balls_in_innings"], np.nan,
    )

    # final per-ball derived features
    df["legal_total"] = (df["overs_per_innings"] * 6).astype(int)
    df["balls_left"] = (df["legal_total"] - df["balls_gone"]).clip(lower=0)
    df["frac_innings"] = df["balls_gone"] / df["legal_total"]
    df["run_rate"] = np.where(df["balls_gone"] > 0,
                              df["runs"] * 6.0 / df["balls_gone"], 0.0)
    df["wickets_in_hand"] = (10 - df["wickets"]).clip(lower=0)
    df["batters_remaining_count"] = (11 - df["wickets"] - 2).clip(lower=0)

    df = df.rename(columns={
        "batter_id": "striker_id",
        "non_striker_id": "ns_id",
    })
    df = df.sort_values(["match_id", "ball_no"]).reset_index(drop=True)
    print(f"    state computed in {time.perf_counter() - t0:.0f}s", flush=True)
    return df


# ---------- 3. skill join ---------------------------------------------------

def join_skill(df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    skill = pd.read_parquet(SKILL_PARQUET)
    print(f"  skill: {len(skill):,} rows, "
          f"player_id range {skill['player_id'].min():.0f}–{skill['player_id'].max():.0f}",
          flush=True)
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


# ---------- 4. train --------------------------------------------------------

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
ALL_FEATURES = V1_FEATURES + SKILL_FEATURE_COLS + CONTEXT


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
    print("== loading balls from SQL ==", flush=True)
    balls = load_balls()

    print("\n== computing state ==", flush=True)
    df = add_state(balls)
    df = df[df["balls_gone"] >= 1]   # drop the "before ball 1" row (run_rate undefined etc.)
    print(f"  feature rows: {len(df):,}", flush=True)

    # drop balls with NULL striker_id / bowler_id (disambiguation gaps)
    before = len(df)
    df = df.dropna(subset=["striker_id", "bowler_id"])
    print(f"  dropped {before - len(df):,} rows with null striker/bowler "
          f"(disambiguation gaps); {len(df):,} remain", flush=True)

    print("\n== joining skill ==", flush=True)
    df, nn = join_skill(df)

    rng = np.random.default_rng(42)
    mids = df["match_id"].unique()
    rng.shuffle(mids)
    n_val = max(1, int(round(0.25 * len(mids))))
    val_ids = set(mids[:n_val].tolist())
    train_ids = set(mids[n_val:].tolist())
    train_df = df[df["match_id"].isin(train_ids)].reset_index(drop=True)
    val_df = df[df["match_id"].isin(val_ids)].reset_index(drop=True)
    print(f"\n== train/val ==")
    print(f"  train: {len(train_ids):,} matches / {len(train_df):,} balls")
    print(f"  val:   {len(val_ids):,} matches / {len(val_df):,} balls")

    print("\n== fitting v1 baseline ==", flush=True)
    m1 = fit(train_df, val_df, 0.5, V1_FEATURES)
    print("== fitting v3c (per-month skill, PC IDs) ==", flush=True)
    m3_10 = fit(train_df, val_df, 0.1, ALL_FEATURES)
    m3_50 = fit(train_df, val_df, 0.5, ALL_FEATURES)
    m3_90 = fit(train_df, val_df, 0.9, ALL_FEATURES)

    val = val_df.copy()
    val["v1_p50"] = m1.predict(val[V1_FEATURES])
    val["v3_p10"] = m3_10.predict(val[ALL_FEATURES])
    val["v3_p50"] = m3_50.predict(val[ALL_FEATURES])
    val["v3_p90"] = m3_90.predict(val[ALL_FEATURES])
    val["naive"] = naive(val)

    mae_v1 = (val["v1_p50"] - val[TARGET]).abs().mean()
    mae_v3 = (val["v3_p50"] - val[TARGET]).abs().mean()
    mae_naive = (val["naive"] - val[TARGET]).abs().mean()
    cov = ((val[TARGET] >= val["v3_p10"]) & (val[TARGET] <= val["v3_p90"])).mean()

    print(f"\n== overall (val) ==")
    print(f"  MAE naive RR: {mae_naive:.2f}")
    print(f"  MAE v1 p50:   {mae_v1:.2f}")
    print(f"  MAE v3c p50:  {mae_v3:.2f}  (Δ {mae_v3 - mae_v1:+.2f} vs v1)")
    print(f"  v3c 80% interval coverage: {cov:.3f}")

    bin_v1 = per_position(val, val["v1_p50"].values)
    bin_v3 = per_position(val, val["v3_p50"].values)
    bin_naive = per_position(val, val["naive"].values)
    bins = bin_naive.merge(bin_v1, on="bin", suffixes=("_naive", "_v1")).merge(
        bin_v3.rename(columns={"err": "err_v3"}), on="bin"
    )
    print("\n== per-position MAE ==")
    print(bins.to_string(index=False))

    imp = pd.DataFrame({
        "feature": ALL_FEATURES,
        "gain": m3_50.feature_importances_,
    }).sort_values("gain", ascending=False)
    print("\n== top 20 by GAIN ==")
    print(imp.head(20).to_string(index=False))

    md = []
    md.append("# POC v3c — disambiguated PC IDs from SQL + per-month skill")
    md.append("")
    md.append("Reads the `balls` SQL table (which carries PC-namespace player_ids")
    md.append("after `_rv_balls.py` / `_disambig.py` have run), not raw JSON.")
    md.append("Drops rows where disambiguation left batter_id or bowler_id NULL.")
    md.append("")
    md.append(f"- Train: **{len(train_ids):,}** matches / {len(train_df):,} balls")
    md.append(f"- Val:   **{len(val_ids):,}** matches / {len(val_df):,} balls")
    md.append(f"- Skill rows: per-month, joined by (player_id, year×100+month)")
    md.append(f"- Striker skill non-null after join: **{nn:.1%}**")
    md.append("")
    md.append("## Headline (held-out val)")
    md.append("")
    md.append("| Model | MAE | Δ vs v1 |")
    md.append("|---|---:|---:|")
    md.append(f"| naive RR | {mae_naive:.1f} | — |")
    md.append(f"| v1 (state only) | {mae_v1:.1f} | (baseline) |")
    md.append(f"| v3c (+ per-month skill) | **{mae_v3:.1f}** | **{mae_v3 - mae_v1:+.1f}** |")
    md.append("")
    md.append(f"v3c 80% interval coverage: **{cov:.2f}**")
    md.append("")
    md.append("## Per-ball-position MAE")
    md.append("")
    md.append("| balls in | naive | v1 | v3c | v3c vs v1 |")
    md.append("|---|---:|---:|---:|---:|")
    for _, r in bins.iterrows():
        md.append(f"| {r['bin']} | {r['err_naive']:.1f} | {r['err_v1']:.1f} | "
                  f"{r['err_v3']:.1f} | {r['err_v3'] - r['err_v1']:+.1f} |")
    md.append("")
    md.append("## Top 20 features by GAIN")
    md.append("")
    md.append("| feature | gain |")
    md.append("|---|---:|")
    for _, r in imp.head(20).iterrows():
        md.append(f"| `{r['feature']}` | {int(r['gain']):,} |")

    (OUT / "results_v3c.md").write_text("\n".join(md) + "\n")
    (OUT / "results_v3c.json").write_text(json.dumps({
        "mae_naive": float(mae_naive),
        "mae_v1": float(mae_v1),
        "mae_v3c": float(mae_v3),
        "lift_v3c_vs_v1_runs": float(mae_v1 - mae_v3),
        "v3c_interval_coverage_80": float(cov),
        "n_train_matches": len(train_ids),
        "n_val_matches": len(val_ids),
        "striker_skill_nn_frac": float(nn),
        "bins": bins.to_dict(orient="records"),
        "top_features": imp.head(20).to_dict(orient="records"),
    }, indent=2, default=str))
    print("\nwrote model/poc/results_v3c.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
