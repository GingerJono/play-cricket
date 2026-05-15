#!/usr/bin/env python3
"""
Win-probability v0 for innings 1.

Reuses the v3d feature pipeline. Adds a binary target:
  y_win = 1 if the team batting first won the match, 0 if they lost.
Drops draws / ties / no-results from training and evaluation.

Trains a LightGBM binary classifier. Reports:
  * log-loss + Brier score on held-out val
  * calibration curve (predicted-bin vs empirical W rate)
  * trajectory of P(win) across the innings for 4 example matches

Output:
  model/poc/results_winprob_v0.md
  model/poc/results_winprob_v0.json
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
OUT = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))
from model.poc.run_poc_v3d import (
    load_universe_match_ids_from_db, load_balls, add_state, join_skill,
    join_ground, join_remaining_batters, join_bowl_team_strength,
    ALL_FEATURES, TARGET as REGRESSION_TARGET,
)


def main() -> int:
    print("== loading universe + balls (v3d pipeline) ==", flush=True)
    con0 = sqlite3.connect(DB)
    universe_ids = load_universe_match_ids_from_db(con0)
    con0.close()
    print(f"  universe match ids: {len(universe_ids):,}")

    balls, con = load_balls(universe_ids)
    # subsample matches BEFORE the heavy joins to keep memory bounded
    MAX_MATCHES = 18000
    rng_pre = np.random.default_rng(seed=42)
    pre_mids = balls["match_id"].unique()
    if len(pre_mids) > MAX_MATCHES:
        pre_mids = rng_pre.choice(pre_mids, MAX_MATCHES, replace=False)
        balls = balls[balls["match_id"].isin(pre_mids)].reset_index(drop=True)
        print(f"  subsampled balls to {MAX_MATCHES} matches / {len(balls):,} rows")
    df = add_state(balls)
    df, _ = join_skill(df)
    df = join_ground(df)
    df = join_remaining_batters(df, con)
    df = join_bowl_team_strength(df, con)

    # build win-label per match: did team_batting_club_id (innings 1) win?
    print("\n== building win-label per match ==", flush=True)
    matches = pd.read_sql(
        "SELECT match_id, result, result_description, result_applied_to, "
        "       home_team_id, away_team_id, home_club_id, away_club_id "
        "FROM matches",
        con,
    )
    con.close()

    # find which CLUB batted first in each match (= innings 1 batting club from balls)
    inn1_club = (df.sort_values(["match_id", "balls_gone"])
                 .drop_duplicates("match_id")[["match_id", "bowl_team_side"]]
                 .copy())
    # bowl_team_side is "home" or "away" — bat_first_side is the opposite
    inn1_club["bat_first_side"] = np.where(inn1_club["bowl_team_side"] == "home",
                                           "away", "home")
    matches = matches.merge(inn1_club, on="match_id", how="inner")
    matches["bat_first_team_id"] = np.where(matches["bat_first_side"] == "home",
                                            matches["home_team_id"],
                                            matches["away_team_id"])

    # win-label: 1 if bat_first_team won, 0 if lost. drop tied/draw/NR.
    matches["bat_first_team_id"] = matches["bat_first_team_id"].astype(str)
    matches["result_applied_to"] = matches["result_applied_to"].fillna("").astype(str)
    matches["winning_team"] = np.where(
        matches["result"] == "W", matches["result_applied_to"],
        np.where(matches["result"] == "L",
                 # losing-team applied; the winner is the OTHER side
                 np.where(matches["result_applied_to"] == matches["home_team_id"].astype(str),
                          matches["away_team_id"].astype(str),
                          matches["home_team_id"].astype(str)),
                 ""),
    )
    decisive = matches["result"].isin(["W", "L"]) & (matches["winning_team"] != "")
    matches = matches[decisive].copy()
    matches["y_win"] = (matches["winning_team"] == matches["bat_first_team_id"]).astype(int)
    print(f"  decisive matches: {len(matches):,}")
    print(f"  win-rate batting first: {matches['y_win'].mean():.3f}")

    df = df.merge(matches[["match_id", "y_win"]], on="match_id", how="inner")
    print(f"  ball rows with win label: {len(df):,}")

    # subsample matches for memory
    MAX_MATCHES = 18000
    rng = np.random.default_rng(seed=42)
    mids = df["match_id"].unique()
    if len(mids) > MAX_MATCHES:
        mids = rng.choice(mids, MAX_MATCHES, replace=False)
        df = df[df["match_id"].isin(mids)].reset_index(drop=True)
        print(f"  subsampled to {MAX_MATCHES} matches / {len(df):,} balls")
    rng.shuffle(mids)
    n_val = max(1, int(round(0.25 * len(mids))))
    val_ids = set(mids[:n_val].tolist())
    train_ids = set(mids[n_val:].tolist())
    train_df = df[df["match_id"].isin(train_ids)].reset_index(drop=True)
    val_df = df[df["match_id"].isin(val_ids)].reset_index(drop=True)
    for d in (train_df, val_df):
        f64 = d.select_dtypes(include=["float64"]).columns
        d[f64] = d[f64].astype("float32")
    print(f"\ntrain: {len(train_ids)} matches / {len(train_df):,} balls")
    print(f"val:   {len(val_ids)} matches / {len(val_df):,} balls")

    print("\n== fitting binary classifier ==", flush=True)
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=600, learning_rate=0.05,
        num_leaves=63, min_data_in_leaf=20, verbose=-1,
        importance_type="gain",
    )
    m.fit(
        train_df[ALL_FEATURES], train_df["y_win"],
        eval_set=[(val_df[ALL_FEATURES], val_df["y_win"])],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )

    val = val_df.copy()
    val["p_win"] = m.predict_proba(val[ALL_FEATURES])[:, 1]
    bs = brier_score_loss(val["y_win"], val["p_win"])
    ll = log_loss(val["y_win"], np.clip(val["p_win"], 1e-6, 1 - 1e-6))
    base_rate = val_df["y_win"].mean()
    bs_baseline = brier_score_loss(val["y_win"],
                                   np.full(len(val), base_rate))
    print(f"\n== overall (val) ==")
    print(f"  base-rate (constant {base_rate:.3f}) Brier: {bs_baseline:.4f}")
    print(f"  v0 win-prob              Brier: {bs:.4f}")
    print(f"  v0 win-prob              log-loss: {ll:.4f}")
    print(f"  Brier skill score: {1 - bs/bs_baseline:.3f}  (> 0 = better than base rate)")

    # calibration: 10 bins
    val["pbin"] = pd.cut(val["p_win"], bins=np.linspace(0, 1, 11),
                         include_lowest=True, right=False)
    cal = val.groupby("pbin", observed=True).agg(
        n=("y_win", "size"),
        mean_pred=("p_win", "mean"),
        empirical=("y_win", "mean"),
    ).reset_index()
    print("\n== calibration ==")
    print(cal.to_string(index=False))

    # per-position MAE-style: avg |p_win - y| at each ball position
    val["ball_bin"] = pd.cut(val["balls_gone"],
                              bins=[0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 1000],
                              right=False,
                              labels=["0-29","30-59","60-89","90-119","120-149",
                                      "150-179","180-209","210-239","240-269","270+"])
    pos = val.groupby("ball_bin", observed=True).apply(
        lambda g: pd.Series({
            "n": len(g),
            "brier": brier_score_loss(g["y_win"], g["p_win"]),
            "log_loss": log_loss(g["y_win"], np.clip(g["p_win"], 1e-6, 1-1e-6)),
        })
    ).reset_index()
    print("\n== per-position Brier on val ==")
    print(pos.to_string(index=False))

    # 4 example match trajectories
    print("\n== example trajectories ==")
    rng = np.random.default_rng(seed=7)
    sample_mids = rng.choice(list(val_ids), 4, replace=False)
    examples = []
    for mid in sample_mids:
        m_rows = val[val["match_id"] == mid].sort_values("balls_gone")
        if len(m_rows) < 30:
            continue
        out = {"match_id": int(mid), "y_win": int(m_rows["y_win"].iloc[0]),
               "trajectory": []}
        for cp in (30, 60, 90, 120, 150, 180, 210, 240):
            sub = m_rows[m_rows["balls_gone"] >= cp].head(1)
            if not sub.empty:
                r = sub.iloc[0]
                out["trajectory"].append({
                    "balls": int(r["balls_gone"]),
                    "runs": int(r["runs"]),
                    "wickets": int(r["wickets"]),
                    "p_win": round(float(r["p_win"]), 3),
                })
        examples.append(out)

    # write report
    md = []
    md.append("# Win probability v0 — innings 1 (team batting first)")
    md.append("")
    md.append(f"- Train: **{len(train_ids):,}** matches / {len(train_df):,} balls")
    md.append(f"- Val:   **{len(val_ids):,}** matches / {len(val_df):,} balls")
    md.append(f"- Base win-rate (batting first): **{base_rate:.3f}**")
    md.append("")
    md.append("## Headline (held-out val)")
    md.append("")
    md.append("| Metric | Value |")
    md.append("|---|---:|")
    md.append(f"| Base-rate Brier (no model) | {bs_baseline:.4f} |")
    md.append(f"| v0 Brier | **{bs:.4f}** |")
    md.append(f"| v0 log-loss | {ll:.4f} |")
    md.append(f"| Brier skill score | **{1 - bs/bs_baseline:.3f}** |")
    md.append("")
    md.append("## Calibration (10 bins of predicted P(win))")
    md.append("")
    md.append("| bucket | n | mean predicted | empirical W |")
    md.append("|---|---:|---:|---:|")
    for _, r in cal.iterrows():
        md.append(f"| {r['pbin']} | {int(r['n']):,} | {r['mean_pred']:.3f} | {r['empirical']:.3f} |")
    md.append("")
    md.append("## Per-ball-position metrics")
    md.append("")
    md.append("| balls in | n | Brier | log-loss |")
    md.append("|---|---:|---:|---:|")
    for _, r in pos.iterrows():
        md.append(f"| {r['ball_bin']} | {int(r['n']):,} | {r['brier']:.4f} | {r['log_loss']:.4f} |")
    md.append("")
    md.append("## Example match trajectories")
    md.append("")
    for ex in examples:
        won = "won" if ex["y_win"] == 1 else "lost"
        md.append(f"### match {ex['match_id']} — team batting first **{won}**")
        md.append("")
        md.append("| balls in | runs | wkts | P(team A wins) |")
        md.append("|---:|---:|---:|---:|")
        for t in ex["trajectory"]:
            md.append(f"| {t['balls']} | {t['runs']} | {t['wickets']} | {t['p_win']:.2f} |")
        md.append("")

    md.append("## Caveats / next")
    md.append("")
    md.append("- Innings 1 only. The chase model (innings 2 → P(chase succeeds))")
    md.append("  is the natural follow-up.")
    md.append("- Class label is `team batting first wins`. Draws / ties / NR")
    md.append("  matches are dropped from training and val.")
    md.append("- Features are exactly the v3d set — no opposition-batting-strength")
    md.append("  feature, just `bowl_team_strength` (= the team that will bat")
    md.append("  in innings 2's *bowling* skill). A separate `bat_team_strength`")
    md.append("  for the chasing side would help.")

    (OUT / "results_winprob_v0.md").write_text("\n".join(md) + "\n")
    (OUT / "results_winprob_v0.json").write_text(json.dumps({
        "n_train_matches": len(train_ids),
        "n_val_matches": len(val_ids),
        "base_rate": float(base_rate),
        "brier_baseline": float(bs_baseline),
        "brier": float(bs),
        "log_loss": float(ll),
        "brier_skill_score": float(1 - bs / bs_baseline),
        "calibration": cal.to_dict(orient="records"),
        "per_position": pos.to_dict(orient="records"),
        "examples": examples,
    }, indent=2, default=str))
    print("\nwrote model/poc/results_winprob_v0.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
