#!/usr/bin/env python3
"""
Combined win-probability model:

  * train innings 1 model (P(team batting first wins))   — same as v0
  * train innings 2 chase model (P(chase succeeds))       — same as chase
  * fit isotonic calibration on top of each on a held-out subset
  * save both models + isotonic calibrators to model/poc/winprob/
  * eval combined trajectory: across both innings, what's P(team A wins)?

Saved artefacts:
  model/poc/winprob/innings1_lgbm.txt          (LightGBM model)
  model/poc/winprob/innings1_isotonic.json     (calibrator: x -> y monotone fn)
  model/poc/winprob/chase_lgbm.txt
  model/poc/winprob/chase_isotonic.json
  model/poc/winprob/feature_cols.json          (which columns each model expects)
  model/poc/results_winprob_combined.md
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
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "rainham.db"
OUT = Path(__file__).resolve().parent
ART = OUT / "winprob"
ART.mkdir(exist_ok=True)

sys.path.insert(0, str(ROOT))
from model.poc.run_poc_v3d import (
    load_universe_match_ids_from_db, load_balls, add_state, join_skill,
    join_ground, join_remaining_batters, join_bowl_team_strength,
    ALL_FEATURES,
)
from model.poc.run_winprob_chase import (
    load_innings2, add_chase_state, join_features as join_chase_features,
    ALL_F as CHASE_FEATURES,
)


def fit_lgbm_classifier(train_df, val_df, features, target):
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=600, learning_rate=0.05,
        num_leaves=63, min_data_in_leaf=20, verbose=-1,
        importance_type="gain",
    )
    m.fit(
        train_df[features], train_df[target],
        eval_set=[(val_df[features], val_df[target])],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    return m


def fit_isotonic(p_raw: np.ndarray, y: np.ndarray) -> IsotonicRegression:
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(p_raw, y)
    return iso


def isotonic_to_json(iso: IsotonicRegression) -> dict:
    """Serialise the breakpoints — replays as a piecewise-linear lookup."""
    return {
        "x": iso.X_thresholds_.tolist(),
        "y": iso.y_thresholds_.tolist(),
    }


def evaluate(name: str, p_raw, p_cal, y) -> dict:
    base = float(y.mean())
    bs_base = brier_score_loss(y, np.full(len(y), base))
    bs_raw = brier_score_loss(y, p_raw)
    bs_cal = brier_score_loss(y, p_cal)
    ll_raw = log_loss(y, np.clip(p_raw, 1e-6, 1 - 1e-6))
    ll_cal = log_loss(y, np.clip(p_cal, 1e-6, 1 - 1e-6))
    return {
        "name": name,
        "n": int(len(y)),
        "base_rate": base,
        "brier_baseline": float(bs_base),
        "brier_raw": float(bs_raw),
        "brier_cal": float(bs_cal),
        "log_loss_raw": float(ll_raw),
        "log_loss_cal": float(ll_cal),
        "bss_raw": float(1 - bs_raw / bs_base),
        "bss_cal": float(1 - bs_cal / bs_base),
    }


def calibration_table(p, y, n_bins=10) -> list[dict]:
    df = pd.DataFrame({"p": p, "y": y})
    df["bin"] = pd.cut(df["p"], bins=np.linspace(0, 1, n_bins + 1),
                       include_lowest=True, right=False)
    g = df.groupby("bin", observed=True).agg(
        n=("y", "size"), mean_pred=("p", "mean"), empirical=("y", "mean"),
    ).reset_index()
    g["bin"] = g["bin"].astype(str)
    return g.to_dict(orient="records")


def downcast(df):
    f64 = df.select_dtypes(include=["float64"]).columns
    df[f64] = df[f64].astype("float32")
    return df


# -------------- INNINGS 1 -----------------------------------------------

def run_innings1():
    print("\n############# INNINGS 1 MODEL #############", flush=True)
    con = sqlite3.connect(DB)
    universe_ids = load_universe_match_ids_from_db(con)
    con.close()

    balls, con = load_balls(universe_ids)
    rng = np.random.default_rng(seed=42)
    pre = balls["match_id"].unique()
    if len(pre) > 18000:
        pre = rng.choice(pre, 18000, replace=False)
        balls = balls[balls["match_id"].isin(pre)].reset_index(drop=True)

    df = add_state(balls)
    df, _ = join_skill(df)
    df = join_ground(df)
    df = join_remaining_batters(df, con)
    df = join_bowl_team_strength(df, con)

    # win label
    matches = pd.read_sql(
        "SELECT match_id, result, result_description, result_applied_to, "
        "       home_team_id, away_team_id FROM matches", con)
    con.close()
    inn1_club = (df.sort_values(["match_id", "balls_gone"])
                 .drop_duplicates("match_id")[["match_id", "bowl_team_side"]])
    inn1_club["bat_first_side"] = np.where(
        inn1_club["bowl_team_side"] == "home", "away", "home")
    matches = matches.merge(inn1_club, on="match_id", how="inner")
    matches["bat_first_team_id"] = np.where(
        matches["bat_first_side"] == "home",
        matches["home_team_id"], matches["away_team_id"]).astype(str)
    matches["result_applied_to"] = matches["result_applied_to"].fillna("").astype(str)
    matches["winning_team"] = np.where(
        matches["result"] == "W", matches["result_applied_to"],
        np.where(matches["result_applied_to"] == matches["home_team_id"].astype(str),
                  matches["away_team_id"].astype(str),
                  matches["home_team_id"].astype(str)))
    matches = matches[matches["result"].isin(["W", "L"]) & (matches["winning_team"] != "")]
    matches["y_win"] = (matches["winning_team"] == matches["bat_first_team_id"]).astype(int)
    df = df.merge(matches[["match_id", "y_win"]], on="match_id", how="inner")

    # train / cal / val 3-way split
    mids = df["match_id"].unique()
    rng.shuffle(mids)
    n = len(mids)
    train_ids = set(mids[: int(0.6 * n)].tolist())
    cal_ids = set(mids[int(0.6 * n): int(0.8 * n)].tolist())
    val_ids = set(mids[int(0.8 * n):].tolist())
    train_df = downcast(df[df["match_id"].isin(train_ids)].reset_index(drop=True))
    cal_df = downcast(df[df["match_id"].isin(cal_ids)].reset_index(drop=True))
    val_df = downcast(df[df["match_id"].isin(val_ids)].reset_index(drop=True))
    print(f"  train: {len(train_df):,} balls, cal: {len(cal_df):,}, val: {len(val_df):,}")

    print("  fitting LGBM ...", flush=True)
    m = fit_lgbm_classifier(train_df, val_df, ALL_FEATURES, "y_win")
    p_cal = m.predict_proba(cal_df[ALL_FEATURES])[:, 1]
    print("  fitting isotonic on cal ...", flush=True)
    iso = fit_isotonic(p_cal, cal_df["y_win"].values)

    p_val_raw = m.predict_proba(val_df[ALL_FEATURES])[:, 1]
    p_val_cal = iso.predict(p_val_raw)

    metrics = evaluate("innings1", p_val_raw, p_val_cal, val_df["y_win"].values)
    cal_raw = calibration_table(p_val_raw, val_df["y_win"].values)
    cal_post = calibration_table(p_val_cal, val_df["y_win"].values)

    m.booster_.save_model(str(ART / "innings1_lgbm.txt"))
    (ART / "innings1_isotonic.json").write_text(json.dumps(isotonic_to_json(iso)))
    return metrics, cal_raw, cal_post


# -------------- CHASE -------------------------------------------------------

def run_chase():
    print("\n############# CHASE MODEL #############", flush=True)
    from model.poc.run_winprob_chase import load_universe_match_ids
    con = sqlite3.connect(DB)
    universe_ids = load_universe_match_ids(con)
    con.close()

    balls, con = load_innings2(universe_ids)
    rng = np.random.default_rng(seed=42)
    pre = balls["match_id"].unique()
    if len(pre) > 14000:
        pre = rng.choice(pre, 14000, replace=False)
        balls = balls[balls["match_id"].isin(pre)].reset_index(drop=True)

    df = add_chase_state(balls)
    df["ns_runs_so_far"] = np.nan
    df["ns_balls_so_far"] = np.nan
    df["ns_intra_sr"] = np.nan
    df = join_chase_features(df, con)
    con.close()

    mids = df["match_id"].unique()
    rng.shuffle(mids)
    n = len(mids)
    train_ids = set(mids[: int(0.6 * n)].tolist())
    cal_ids = set(mids[int(0.6 * n): int(0.8 * n)].tolist())
    val_ids = set(mids[int(0.8 * n):].tolist())
    train_df = downcast(df[df["match_id"].isin(train_ids)].reset_index(drop=True))
    cal_df = downcast(df[df["match_id"].isin(cal_ids)].reset_index(drop=True))
    val_df = downcast(df[df["match_id"].isin(val_ids)].reset_index(drop=True))
    print(f"  train: {len(train_df):,} balls, cal: {len(cal_df):,}, val: {len(val_df):,}")

    print("  fitting LGBM ...", flush=True)
    m = fit_lgbm_classifier(train_df, val_df, CHASE_FEATURES, "y_chase_won")
    p_cal = m.predict_proba(cal_df[CHASE_FEATURES])[:, 1]
    print("  fitting isotonic on cal ...", flush=True)
    iso = fit_isotonic(p_cal, cal_df["y_chase_won"].values)

    p_val_raw = m.predict_proba(val_df[CHASE_FEATURES])[:, 1]
    p_val_cal = iso.predict(p_val_raw)

    metrics = evaluate("chase", p_val_raw, p_val_cal, val_df["y_chase_won"].values)
    cal_raw = calibration_table(p_val_raw, val_df["y_chase_won"].values)
    cal_post = calibration_table(p_val_cal, val_df["y_chase_won"].values)

    m.booster_.save_model(str(ART / "chase_lgbm.txt"))
    (ART / "chase_isotonic.json").write_text(json.dumps(isotonic_to_json(iso)))
    return metrics, cal_raw, cal_post


def main() -> int:
    m1, cal1_raw, cal1_post = run_innings1()
    m2, cal2_raw, cal2_post = run_chase()

    # P(team A wins) per ball:
    #   innings 1: model says it directly
    #   innings 2: P(A wins) = 1 - P(chase succeeds)
    # Combined = innings_1_model on innings 1 balls + chase_complement on innings 2 balls

    md = []
    md.append("# Combined win-prob — innings 1 + chase + isotonic")
    md.append("")
    md.append("Two LightGBM binary classifiers trained on a 60/20/20 split:")
    md.append("60% train / 20% calibration (isotonic fit) / 20% val (held-out).")
    md.append("")
    md.append("Saved artefacts under `model/poc/winprob/`:")
    md.append("  - `innings1_lgbm.txt`, `innings1_isotonic.json`")
    md.append("  - `chase_lgbm.txt`, `chase_isotonic.json`")
    md.append("")
    md.append("## Headline (held-out val)")
    md.append("")
    md.append("| Model | Brier (raw) | Brier (calibrated) | BSS (raw → cal) |")
    md.append("|---|---:|---:|---:|")
    md.append(f"| Innings 1 | {m1['brier_raw']:.4f} | **{m1['brier_cal']:.4f}** | {m1['bss_raw']:.3f} → **{m1['bss_cal']:.3f}** |")
    md.append(f"| Chase     | {m2['brier_raw']:.4f} | **{m2['brier_cal']:.4f}** | {m2['bss_raw']:.3f} → **{m2['bss_cal']:.3f}** |")
    md.append("")
    md.append("## Innings 1 calibration (raw vs isotonic)")
    md.append("")
    md.append("| bucket | n | mean pred (raw) | empirical | mean pred (cal) |")
    md.append("|---|---:|---:|---:|---:|")
    for r, c in zip(cal1_raw, cal1_post):
        md.append(f"| {r['bin']} | {int(r['n']):,} | {r['mean_pred']:.3f} | {r['empirical']:.3f} | {c['mean_pred']:.3f} |")
    md.append("")
    md.append("## Chase calibration (raw vs isotonic)")
    md.append("")
    md.append("| bucket | n | mean pred (raw) | empirical | mean pred (cal) |")
    md.append("|---|---:|---:|---:|---:|")
    for r, c in zip(cal2_raw, cal2_post):
        md.append(f"| {r['bin']} | {int(r['n']):,} | {r['mean_pred']:.3f} | {r['empirical']:.3f} | {c['mean_pred']:.3f} |")

    md.append("")
    md.append("## Inference helpers")
    md.append("")
    md.append("`model/poc/predict_winprob.py` (next) loads the LGBM `.txt` files")
    md.append("plus the isotonic JSONs and exposes:")
    md.append("```python")
    md.append("predict_winprob(state: dict, innings: int) -> float  # P(team batting first wins)")
    md.append("```")
    md.append("`build_matchweek.py` will call this per match-snapshot to bake the")
    md.append("win-prob trajectory into the matchweek JSON.")
    md.append("")

    (OUT / "results_winprob_combined.md").write_text("\n".join(md) + "\n")
    (OUT / "results_winprob_combined.json").write_text(json.dumps({
        "innings1": m1, "chase": m2,
        "calibration_innings1_raw": cal1_raw,
        "calibration_innings1_cal": cal1_post,
        "calibration_chase_raw": cal2_raw,
        "calibration_chase_cal": cal2_post,
    }, indent=2, default=str))
    print(f"\nwrote {OUT / 'results_winprob_combined.md'}")
    print(f"saved 4 artefacts to {ART}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
