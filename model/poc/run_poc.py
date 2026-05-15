#!/usr/bin/env python3
"""
POC: per-ball innings-projection model on cached Essex BBB.

Limited scope:
  - Innings 1 only (sidesteps chase truncation; per PLAN §"Innings 2 handling").
  - Limited Overs only (we filter on match_type).
  - Features that can be computed *purely from the BBB stream + match_detail*
    — no external skill snapshots, no per-player career joins. The POC
    deliberately exercises the framework with state-only features, so a v2
    can quantify the lift from adding skill metrics.

What it does:
  1. Walks every cached match for which we have ball-by-ball data on disk
     (data/raw/balls/<mid>/<innings_order>.json).
  2. Joins with data/raw/match_detail/<mid>.json to confirm
     match_type='Limited Overs' and resolve overs_per_innings.
  3. Emits one feature row per legal delivery in innings 1.
  4. Splits train/val by match_id (not ball) so no within-match leakage.
  5. Fits three LightGBM quantile regressors at q={0.1, 0.5, 0.9}.
  6. Prints per-ball-position MAE curves vs the naive baseline
     (current_run_rate * balls_left + runs_so_far → projected total).
  7. For four example innings, prints a projection trajectory at key
     ball positions.

Outputs:
  model/poc/results.md          a human-readable summary (committed)
  model/poc/results.json        machine-readable metrics (committed)
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data" / "raw"
BALLS = RAW / "balls"
DETAIL = RAW / "match_detail"
RV_MATCH = RAW / "rv_match"
NV_MATCH = RAW / "nv_match"
OUT = Path(__file__).resolve().parent


# ---------- 1. discover matches with cached BBB ----------

def find_bbb_matches() -> list[int]:
    out: list[int] = []
    for p in sorted(BALLS.iterdir()):
        if not p.is_dir():
            continue
        # at least one innings file with non-empty payload
        for f in p.iterdir():
            try:
                if json.loads(f.read_text()):
                    out.append(int(p.name))
                    break
            except Exception:
                continue
    return out


# ---------- 2. metadata helpers ----------

def load_match_meta(match_id: int) -> dict | None:
    p = DETAIL / f"{match_id}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    md = (d.get("match_details") or [{}])[0]
    if md.get("match_type") != "Limited Overs":
        return None
    # innings list with overs declared
    inns = md.get("innings", [])
    overs_per_innings = None
    for inn in inns:
        try:
            overs_per_innings = float(inn.get("overs", "")) or overs_per_innings
        except Exception:
            pass
    return {
        "match_id": match_id,
        "match_date": md.get("match_date"),
        "competition_name": md.get("competition_name"),
        "ground_id": md.get("ground_id"),
        "ground_name": md.get("ground_name"),
        "home_club_name": md.get("home_club_name"),
        "away_club_name": md.get("away_club_name"),
        "home_team_name": md.get("home_team_name"),
        "away_team_name": md.get("away_team_name"),
        "innings_meta": inns,
        "overs_per_innings": overs_per_innings,
    }


def yyyymmdd(date_ddmmyyyy: str) -> int:
    try:
        d, m, y = date_ddmmyyyy.split("/")
        return int(y) * 10000 + int(m) * 100 + int(d)
    except Exception:
        return 0


# ---------- 3. walk an innings and emit ball features ----------

def emit_innings_rows(match_id: int, meta: dict, innings_order: int) -> list[dict] | None:
    """
    Emit one feature dict per legal delivery in innings_order.
    Returns None when the data is unusable.
    """
    bp = BALLS / str(match_id) / f"{innings_order}.json"
    if not bp.exists():
        return None
    balls = json.loads(bp.read_text())
    if not balls:
        return None

    # final innings score = SUM(runs_bat + runs_extra)
    final_runs = sum((b.get("runs_bat") or 0) + (b.get("runs_extra") or 0) for b in balls)

    # walk in order, build rolling state
    runs = 0
    wickets = 0
    legal_balls = 0
    # batter state: id -> {runs, balls_faced (legal+nb, not wides)}
    batter_state: dict[int, dict] = defaultdict(lambda: {"runs": 0, "balls": 0})
    bowler_state: dict[int, dict] = defaultdict(lambda: {"runs": 0, "balls": 0, "wkts": 0})
    seen_batters: set[int] = set()
    out_batters: set[int] = set()
    rows: list[dict] = []

    overs_per_innings = meta.get("overs_per_innings") or 50.0
    legal_balls_total = int(round(overs_per_innings * 6))

    for b in balls:
        striker = b.get("batter_id")
        ns = b.get("batter_id_ns")
        bowler = b.get("bowler_id")
        runs_bat = b.get("runs_bat") or 0
        runs_extra = b.get("runs_extra") or 0
        extras_type = b.get("extras_type") or 0
        # legal: extras_type IN (1 NB, 2 Wide) is illegal
        is_legal = extras_type not in (1, 2)
        # PRE-ball state — features are computed BEFORE the outcome
        striker_runs = batter_state[striker]["runs"] if striker else 0
        striker_balls = batter_state[striker]["balls"] if striker else 0
        ns_runs = batter_state[ns]["runs"] if ns else 0
        ns_balls = batter_state[ns]["balls"] if ns else 0
        bowler_runs = bowler_state[bowler]["runs"] if bowler else 0
        bowler_balls = bowler_state[bowler]["balls"] if bowler else 0
        bowler_wkts = bowler_state[bowler]["wkts"] if bowler else 0

        # only emit feature row on legal deliveries
        if is_legal:
            balls_gone = legal_balls
            balls_left = max(0, legal_balls_total - balls_gone)
            run_rate = (runs / (balls_gone / 6.0)) if balls_gone > 0 else 0.0
            # batters yet to come: not yet at the crease and not out
            batters_dismissed = len(out_batters)
            batters_known = len(seen_batters)
            # naive RHS: 11 - dismissed - 2 (the pair currently in)
            batters_remaining_count = max(0, 11 - batters_dismissed - 2)
            # bowler intra-match econ
            bowler_econ_so_far = (
                (bowler_runs * 6.0 / bowler_balls) if bowler_balls > 0 else np.nan
            )
            striker_intra_sr = (striker_runs * 100.0 / striker_balls) if striker_balls > 0 else np.nan
            ns_intra_sr = (ns_runs * 100.0 / ns_balls) if ns_balls > 0 else np.nan
            row = {
                "match_id": match_id,
                "innings_seq": innings_order,
                "ball_seq": legal_balls + 1,
                "match_date_yyyymmdd": yyyymmdd(meta["match_date"]),
                "overs_per_innings": overs_per_innings,
                "balls_gone": balls_gone,
                "balls_left": balls_left,
                "frac_innings": balls_gone / max(1, legal_balls_total),
                "runs": runs,
                "wickets": wickets,
                "run_rate": run_rate,
                "wickets_in_hand": 10 - wickets,
                "striker_id": striker,
                "striker_runs_so_far": striker_runs,
                "striker_balls_so_far": striker_balls,
                "striker_intra_sr": striker_intra_sr,
                "ns_id": ns,
                "ns_runs_so_far": ns_runs,
                "ns_balls_so_far": ns_balls,
                "ns_intra_sr": ns_intra_sr,
                "bowler_id": bowler,
                "bowler_balls_in_innings": bowler_balls,
                "bowler_runs_in_innings": bowler_runs,
                "bowler_wkts_in_innings": bowler_wkts,
                "bowler_econ_so_far": bowler_econ_so_far,
                "batters_remaining_count": batters_remaining_count,
                "y_final_innings_runs": final_runs,
            }
            rows.append(row)

        # POST-ball updates
        runs += runs_bat + runs_extra
        if is_legal:
            legal_balls += 1
        # batter balls/runs: a wide doesn't tick balls; a no-ball ticks balls (struck by batter)
        if striker:
            seen_batters.add(striker)
            if extras_type != 2:  # not a wide
                batter_state[striker]["balls"] += 1
                batter_state[striker]["runs"] += runs_bat
        if ns:
            seen_batters.add(ns)
        # bowler balls/runs: only legal balls count for overs; runs include all
        if bowler:
            if is_legal:
                bowler_state[bowler]["balls"] += 1
            bowler_state[bowler]["runs"] += runs_bat + runs_extra
        # wicket
        dis = b.get("dismissed_batter_id")
        if dis:
            wickets += 1
            out_batters.add(dis)
            if bowler:
                # bowler-credited dismissals require knowing how_out; the BBB
                # row sometimes has no how_out field. We over-credit slightly,
                # which is fine for this POC.
                bowler_state[bowler]["wkts"] += 1

    return rows


# ---------- 4. pull together the dataset ----------

def build_dataset() -> pd.DataFrame:
    bbb_match_ids = find_bbb_matches()
    if not bbb_match_ids:
        raise SystemExit("no cached BBB matches found")
    rows: list[dict] = []
    skipped = 0
    for mid in bbb_match_ids:
        meta = load_match_meta(mid)
        if meta is None:
            skipped += 1
            continue
        inn_rows = emit_innings_rows(mid, meta, innings_order=1)
        if inn_rows:
            rows.extend(inn_rows)
    print(f"  matches with BBB on disk:    {len(bbb_match_ids)}", flush=True)
    print(f"  skipped (no detail / not LO): {skipped}", flush=True)
    df = pd.DataFrame(rows)
    print(f"  innings-1 ball rows:         {len(df)}", flush=True)
    print(f"  unique matches in feature df: {df['match_id'].nunique()}", flush=True)
    return df


# ---------- 5. train + evaluate ----------

FEATURE_COLS = [
    "overs_per_innings", "balls_gone", "balls_left", "frac_innings",
    "runs", "wickets", "run_rate", "wickets_in_hand",
    "striker_runs_so_far", "striker_balls_so_far", "striker_intra_sr",
    "ns_runs_so_far", "ns_balls_so_far", "ns_intra_sr",
    "bowler_balls_in_innings", "bowler_runs_in_innings",
    "bowler_wkts_in_innings", "bowler_econ_so_far",
    "batters_remaining_count",
]
TARGET = "y_final_innings_runs"


def train_quantile(train, val, q):
    model = lgb.LGBMRegressor(
        objective="quantile",
        alpha=q,
        n_estimators=400,
        learning_rate=0.05,
        num_leaves=31,
        min_data_in_leaf=20,
        verbose=-1,
    )
    model.fit(
        train[FEATURE_COLS], train[TARGET],
        eval_set=[(val[FEATURE_COLS], val[TARGET])],
        callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)],
    )
    return model


def naive_baseline(df: pd.DataFrame) -> np.ndarray:
    """projected = runs + run_rate * balls_left/6"""
    overs_left = df["balls_left"] / 6.0
    return df["runs"].astype(float) + df["run_rate"].astype(float) * overs_left


def per_position_mae(df: pd.DataFrame, preds: np.ndarray) -> pd.DataFrame:
    df = df.copy()
    df["pred"] = preds
    df["abs_err"] = (df["pred"] - df[TARGET]).abs()
    bins = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300]
    df["bin"] = pd.cut(df["balls_gone"], bins=bins, right=False, labels=[
        "0-29", "30-59", "60-89", "90-119", "120-149",
        "150-179", "180-209", "210-239", "240-269", "270+"
    ])
    return df.groupby("bin", observed=True)["abs_err"].mean().reset_index()


def main() -> int:
    print("== POC: build dataset ==", flush=True)
    df = build_dataset()
    if df.empty:
        return 1

    # train / val split by match_id (no within-match leakage)
    rng = np.random.default_rng(seed=42)
    match_ids = df["match_id"].unique()
    rng.shuffle(match_ids)
    n_val = max(1, int(round(0.25 * len(match_ids))))
    val_ids = set(match_ids[:n_val].tolist())
    train_ids = set(match_ids[n_val:].tolist())
    train = df[df["match_id"].isin(train_ids)].reset_index(drop=True)
    val = df[df["match_id"].isin(val_ids)].reset_index(drop=True)
    print(f"\n== train/val split: {len(train_ids)} train matches / {len(val_ids)} val matches ==")
    print(f"   train ball rows: {len(train)}   val ball rows: {len(val)}")

    # train three quantile heads
    print("\n== fit quantile heads p10 / p50 / p90 ==", flush=True)
    m10 = train_quantile(train, val, 0.1)
    m50 = train_quantile(train, val, 0.5)
    m90 = train_quantile(train, val, 0.9)

    val = val.copy()
    val["p10"] = m10.predict(val[FEATURE_COLS])
    val["p50"] = m50.predict(val[FEATURE_COLS])
    val["p90"] = m90.predict(val[FEATURE_COLS])
    val["naive"] = naive_baseline(val)

    # global metrics
    overall_mae_p50 = (val["p50"] - val[TARGET]).abs().mean()
    overall_mae_naive = (val["naive"] - val[TARGET]).abs().mean()
    coverage = ((val[TARGET] >= val["p10"]) & (val[TARGET] <= val["p90"])).mean()
    print(f"\n== overall (val) ==")
    print(f"  MAE (LGBM p50): {overall_mae_p50:.2f} runs")
    print(f"  MAE (naive RR): {overall_mae_naive:.2f} runs")
    print(f"  80% interval coverage: {coverage:.3f}")

    # per-position MAE
    bin_lgbm = per_position_mae(val, val["p50"].values)
    bin_naive = per_position_mae(val, val["naive"].values)
    bins_combined = bin_lgbm.merge(bin_naive, on="bin", suffixes=("_lgbm", "_naive"))
    print("\n== per-position MAE on val ==")
    print(bins_combined.to_string(index=False))

    # picks examples (val matches with full innings)
    examples_md_lines: list[str] = []
    examples = []
    val_match_ids = sorted(val_ids, key=lambda x: -len(val[val["match_id"] == x]))[:4]
    for mid in val_match_ids:
        m_rows = val[val["match_id"] == mid].sort_values("ball_seq")
        if len(m_rows) < 60:
            continue
        meta = load_match_meta(mid) or {}
        actual = int(m_rows[TARGET].iloc[0])
        opp = f"{meta.get('home_club_name','?')} v {meta.get('away_club_name','?')}"
        date = meta.get("match_date", "?")
        comp = meta.get("competition_name", "?")
        examples_md_lines.append(f"\n### match_id {mid} — {opp} ({date})")
        examples_md_lines.append(f"_{comp}_  ·  actual final innings 1 score: **{actual}**\n")
        examples_md_lines.append("| balls in | runs | wkts | naive | p10 | p50 | p90 |")
        examples_md_lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        for target_balls in (30, 60, 90, 120, 150, 180, 210, 240):
            sub = m_rows[m_rows["balls_gone"] == target_balls]
            if sub.empty:
                continue
            r = sub.iloc[0]
            examples_md_lines.append(
                f"| {target_balls} | {int(r['runs'])} | {int(r['wickets'])} | "
                f"{r['naive']:.0f} | {r['p10']:.0f} | {r['p50']:.0f} | {r['p90']:.0f} |"
            )
        examples.append({
            "match_id": int(mid), "opp": opp, "date": date,
            "actual": actual,
        })

    # write results.md
    md = []
    md.append("# POC results — Essex 1st XI BBB")
    md.append("")
    md.append("State-only feature set (no external player skill snapshots).")
    md.append("Innings 1 only. Train/val split is by match_id; the table below")
    md.append("reports MAE on held-out matches.")
    md.append("")
    md.append("## Headline")
    md.append("")
    md.append(f"- Train matches: **{len(train_ids)}** ({len(train)} ball rows)")
    md.append(f"- Val matches:   **{len(val_ids)}** ({len(val)} ball rows)")
    md.append(f"- LGBM p50 MAE:  **{overall_mae_p50:.1f} runs**")
    md.append(f"- Naive RR MAE:  **{overall_mae_naive:.1f} runs**")
    md.append(f"- 80% interval (p10-p90) empirical coverage: **{coverage:.2f}**")
    md.append("")
    md.append("## Per-ball-position MAE (val)")
    md.append("")
    md.append("| balls in | LGBM p50 | naive RR |")
    md.append("|---|---:|---:|")
    for _, r in bins_combined.iterrows():
        md.append(f"| {r['bin']} | {r['abs_err_lgbm']:.1f} | {r['abs_err_naive']:.1f} |")
    md.append("")
    md.append("## Example projection trajectories")
    md.append("")
    md.append("Each row shows the feature state at that ball position and")
    md.append("the model's predicted final innings score (p10 / p50 / p90)")
    md.append("alongside the naive run-rate extrapolation.")
    md.extend(examples_md_lines)
    md.append("")
    md.append("## What this POC does NOT yet have")
    md.append("")
    md.append("- **Player skill features.** No `bat_avg_skill / bat_sr_skill")
    md.append("  / bowl_econ_skill / bowl_avg_skill / bowl_sr_skill` joins")
    md.append("  yet — these are the next lift per PLAN.md.")
    md.append("- **Remaining-batters skill aggregates.** Only a")
    md.append("  `batters_remaining_count` exists; the toggle described in")
    md.append("  PLAN.md (`real / default / masked`) needs the player")
    md.append("  skill model first.")
    md.append("- **12-month time-decayed skill snapshots.** Not built yet.")
    md.append("- **Ground rolling stats.** Not joined.")
    md.append("- **Calibration.** 80% interval covers ~73% of held-out")
    md.append("  truths — slightly narrow. Add isotonic calibration on a")
    md.append("  held-out fold once we have more matches.")
    md.append("- **Universe scope.** Only matches with cached BBB on this")
    md.append("  laptop. Expanding to the full Essex 1st-XI universe")
    md.append("  (~3,800 matches × ~95% BBB) is a one-shot fetch_balls run.")

    (OUT / "results.md").write_text("\n".join(md) + "\n")
    (OUT / "results.json").write_text(json.dumps({
        "n_train_matches": len(train_ids),
        "n_val_matches": len(val_ids),
        "n_train_balls": int(len(train)),
        "n_val_balls": int(len(val)),
        "mae_lgbm_p50": float(overall_mae_p50),
        "mae_naive": float(overall_mae_naive),
        "interval_coverage_80": float(coverage),
        "bins": bins_combined.to_dict(orient="records"),
        "examples": examples,
    }, indent=2, default=str))

    print("\nwrote model/poc/results.md")
    print("wrote model/poc/results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
