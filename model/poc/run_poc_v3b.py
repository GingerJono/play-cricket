#!/usr/bin/env python3
"""
POC v3b — vectorised merge of monthly skill snapshots onto ball features.

Same data + same intent as run_poc_v3.py, but with these fixes:
  - player_id is cast to Int64 (nullable) on both sides so the merge keys
    actually match (the parquet's player_id was float64, which silently
    broke the per-ball .loc lookup).
  - Skill join is a single vectorised pandas merge per role (striker /
    non-striker / bowler), not 3M repeated index lookups.
  - Reports feature importance by GAIN, not split count.

Innings 1 only, Limited Overs only, BBB era (2021+), train/val by match_id.
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data" / "raw"
BALLS = RAW / "balls"
DETAIL = RAW / "match_detail"
OUT = Path(__file__).resolve().parent

SKILL_PARQUET = ROOT / "data" / "skill_monthly.parquet"

SKILL_COLS = ["bat_avg_skill", "bat_sr_skill",
              "bowl_econ_skill", "bowl_avg_skill", "bowl_sr_skill"]


# ---------- 1. ball walker (no skill lookup) --------------------------------

def yyyymmdd(s):
    if not s:
        return 0
    try:
        a, b, c = s.split("/")
        return int(c) * 10000 + int(b) * 100 + int(a)
    except Exception:
        return 0


def find_bbb_matches() -> list[int]:
    out = []
    for p in sorted(BALLS.iterdir()):
        if not p.is_dir():
            continue
        for f in p.iterdir():
            try:
                if json.loads(f.read_text()):
                    out.append(int(p.name))
                    break
            except Exception:
                continue
    return out


def load_meta(mid):
    p = DETAIL / f"{mid}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    md = (d.get("match_details") or [{}])[0]
    if md.get("match_type") != "Limited Overs":
        return None
    overs = None
    for inn in md.get("innings", []):
        try:
            overs = float(inn.get("overs", "")) or overs
        except Exception:
            pass
    return {
        "match_id": mid,
        "match_date": md.get("match_date"),
        "ground_id": md.get("ground_id"),
        "overs_per_innings": overs,
    }


def emit_innings_rows(mid, meta) -> list[dict] | None:
    bp = BALLS / str(mid) / "1.json"
    if not bp.exists():
        return None
    balls = json.loads(bp.read_text())
    if not balls:
        return None
    final = sum((b.get("runs_bat") or 0) + (b.get("runs_extra") or 0) for b in balls)

    ymd = yyyymmdd(meta.get("match_date"))
    yyyy = ymd // 10000
    mm = (ymd // 100) % 100
    if yyyy < 2021 or yyyy > 2026:
        return None
    snap_ym = yyyy * 100 + mm

    overs_total = meta.get("overs_per_innings") or 50.0
    legal_total = int(round(overs_total * 6))

    runs = wickets = legal = 0
    bs = defaultdict(lambda: {"runs": 0, "balls": 0})
    bws = defaultdict(lambda: {"runs": 0, "balls": 0, "wkts": 0})
    out_set: set[int] = set()
    rows: list[dict] = []

    for b in balls:
        st = b.get("batter_id"); ns = b.get("batter_id_ns"); bw = b.get("bowler_id")
        rb = b.get("runs_bat") or 0; re_ = b.get("runs_extra") or 0
        et = b.get("extras_type") or 0; legal_ball = et not in (1, 2)
        if legal_ball:
            sr = bs[st] if st else {"runs": 0, "balls": 0}
            nsr = bs[ns] if ns else {"runs": 0, "balls": 0}
            br = bws[bw] if bw else {"runs": 0, "balls": 0, "wkts": 0}
            rows.append({
                "match_id": mid,
                "ball_seq": legal + 1,
                "snap_ym": snap_ym,
                "season": yyyy,
                "match_month": mm,
                "overs_per_innings": overs_total,
                "balls_gone": legal,
                "balls_left": max(0, legal_total - legal),
                "frac_innings": legal / max(1, legal_total),
                "runs": runs,
                "wickets": wickets,
                "run_rate": (runs / (legal / 6)) if legal else 0.0,
                "wickets_in_hand": 10 - wickets,
                "striker_id": st,
                "striker_runs_so_far": sr["runs"],
                "striker_balls_so_far": sr["balls"],
                "striker_intra_sr": (sr["runs"] * 100 / sr["balls"]) if sr["balls"] else np.nan,
                "ns_id": ns,
                "ns_runs_so_far": nsr["runs"],
                "ns_balls_so_far": nsr["balls"],
                "ns_intra_sr": (nsr["runs"] * 100 / nsr["balls"]) if nsr["balls"] else np.nan,
                "bowler_id": bw,
                "bowler_balls_in_innings": br["balls"],
                "bowler_runs_in_innings": br["runs"],
                "bowler_wkts_in_innings": br["wkts"],
                "bowler_econ_so_far": (br["runs"] * 6 / br["balls"]) if br["balls"] else np.nan,
                "batters_remaining_count": max(0, 11 - len(out_set) - 2),
                "y_final_innings_runs": final,
            })
        runs += rb + re_
        if legal_ball: legal += 1
        if st and et != 2:
            bs[st]["balls"] += 1; bs[st]["runs"] += rb
        if bw:
            if legal_ball: bws[bw]["balls"] += 1
            bws[bw]["runs"] += rb + re_
        d = b.get("dismissed_batter_id")
        if d:
            wickets += 1; out_set.add(d)
            if bw: bws[bw]["wkts"] += 1
    return rows


# ---------- 2. vectorised skill merge ---------------------------------------

def join_skill(df: pd.DataFrame, skill: pd.DataFrame) -> pd.DataFrame:
    """
    df has int columns: striker_id, ns_id, bowler_id, snap_ym.
    skill is the long-form (player_id, snapshot_yyyymm, 5 skill cols) frame.
    Returns df with 15 new columns: <role>_<skill> for role in {striker, ns, bowler}.
    """
    skill_slim = skill[["player_id", "snapshot_yyyymm"] + SKILL_COLS].copy()
    skill_slim["player_id"] = skill_slim["player_id"].astype("Int64")
    skill_slim["snapshot_yyyymm"] = skill_slim["snapshot_yyyymm"].astype("Int64")
    for role in ("striker", "ns", "bowler"):
        df[f"{role}_id"] = df[f"{role}_id"].astype("Int64")
    df["snap_ym"] = df["snap_ym"].astype("Int64")

    for role in ("striker", "ns", "bowler"):
        rename = {c: f"{role}_{c}" for c in SKILL_COLS}
        right = skill_slim.rename(columns={"player_id": f"{role}_id",
                                           "snapshot_yyyymm": "snap_ym",
                                           **rename})
        df = df.merge(right, on=[f"{role}_id", "snap_ym"], how="left")
    return df


# ---------- 3. modelling ----------------------------------------------------

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
TARGET = "y_final_innings_runs"


def train(train_df, val_df, q, features):
    m = lgb.LGBMRegressor(
        objective="quantile", alpha=q,
        n_estimators=600, learning_rate=0.05, num_leaves=63,
        min_data_in_leaf=20, verbose=-1, importance_type="gain",
    )
    m.fit(
        train_df[features], train_df[TARGET],
        eval_set=[(val_df[features], val_df[TARGET])],
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
    print("== loading skill parquet ==", flush=True)
    if not SKILL_PARQUET.exists():
        raise SystemExit(f"missing {SKILL_PARQUET}")
    skill = pd.read_parquet(SKILL_PARQUET)
    print(f"  {len(skill):,} rows, "
          f"{skill['snapshot_yyyymm'].nunique()} snapshot months, "
          f"{skill['player_id'].nunique():,} unique players")

    print("\n== walking BBB matches ==", flush=True)
    t0 = time.perf_counter()
    bbb_ids = find_bbb_matches()
    print(f"  bbb match dirs on disk: {len(bbb_ids):,}")
    rows = []
    for i, mid in enumerate(bbb_ids):
        meta = load_meta(mid)
        if meta is None:
            continue
        r = emit_innings_rows(mid, meta)
        if r:
            rows.extend(r)
        if (i + 1) % 2000 == 0:
            print(f"    walked {i+1:,} matches, {len(rows):,} rows so far "
                  f"({time.perf_counter() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    print(f"  innings-1 ball rows: {len(df):,} from {df['match_id'].nunique():,} matches "
          f"({time.perf_counter() - t0:.0f}s)")

    print("\n== merging skill snapshots ==", flush=True)
    t0 = time.perf_counter()
    df = join_skill(df, skill)
    nn = df[f"striker_{SKILL_COLS[0]}"].notna().mean()
    print(f"  striker skill non-null fraction: {nn:.1%}  ({time.perf_counter() - t0:.0f}s)")
    if nn < 0.05:
        print("WARNING: skill join produced almost no matches — check id types")

    rng = np.random.default_rng(seed=42)
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
    m1 = train(train_df, val_df, 0.5, V1_FEATURES)
    print("== fitting v3b (per-month skill) ==", flush=True)
    m3_10 = train(train_df, val_df, 0.1, ALL_FEATURES)
    m3_50 = train(train_df, val_df, 0.5, ALL_FEATURES)
    m3_90 = train(train_df, val_df, 0.9, ALL_FEATURES)

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
    print(f"  MAE v3b p50:  {mae_v3:.2f}  (Δ {mae_v3 - mae_v1:+.2f} vs v1)")
    print(f"  v3b 80% interval coverage: {cov:.3f}")

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
    md.append("# POC v3b — vectorised per-month skill features")
    md.append("")
    md.append("Bug fix vs v3a: player_id was float64 in parquet → silent merge")
    md.append("failure. v3b casts player_id and snapshot_yyyymm to Int64 on")
    md.append("both sides and uses one big pandas merge per role rather than")
    md.append("per-ball .loc lookups. ~50× faster too.")
    md.append("")
    md.append(f"- Train: **{len(train_ids):,}** matches / {len(train_df):,} balls")
    md.append(f"- Val:   **{len(val_ids):,}** matches / {len(val_df):,} balls")
    md.append(f"- Skill rows: {len(skill):,}")
    md.append(f"- Striker skill non-null after join: **{nn:.1%}**")
    md.append("")
    md.append("## Headline (held-out val)")
    md.append("")
    md.append("| Model | MAE | Δ vs v1 |")
    md.append("|---|---:|---:|")
    md.append(f"| naive RR | {mae_naive:.1f} | — |")
    md.append(f"| v1 (state only) | {mae_v1:.1f} | (baseline) |")
    md.append(f"| v3b (+ per-month skill) | **{mae_v3:.1f}** | **{mae_v3 - mae_v1:+.1f}** |")
    md.append("")
    md.append(f"v3b 80% interval coverage: **{cov:.2f}**")
    md.append("")
    md.append("## Per-ball-position MAE")
    md.append("")
    md.append("| balls in | naive | v1 | v3b | v3b vs v1 |")
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

    (OUT / "results_v3b.md").write_text("\n".join(md) + "\n")
    (OUT / "results_v3b.json").write_text(json.dumps({
        "mae_naive": float(mae_naive),
        "mae_v1": float(mae_v1),
        "mae_v3b": float(mae_v3),
        "lift_v3b_vs_v1_runs": float(mae_v1 - mae_v3),
        "v3b_interval_coverage_80": float(cov),
        "n_train_matches": len(train_ids),
        "n_val_matches": len(val_ids),
        "striker_skill_nn_frac": float(nn),
        "bins": bins.to_dict(orient="records"),
        "top_features": imp.head(20).to_dict(orient="records"),
    }, indent=2, default=str))
    print("\nwrote model/poc/results_v3b.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
