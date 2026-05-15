#!/usr/bin/env python3
"""
POC v2 — adds player skill features + league/ground context to the v1 baseline.

What changed vs v1 (model/poc/run_poc.py):
  - Joins each ball's batter / non-striker / current-bowler player_ids
    against `data/skill_snapshot.csv` (5 separate /100 metrics each,
    last-12-month percentiles, kept unblended).
  - Adds league_site_id (categorical) and ground_id (categorical) so
    the model can learn population-level priors.
  - Adds match_month + season as numeric features.
  - All other v1 features retained.

Innings 1 only, Limited Overs only. Train/val split is by match_id.

Outputs:
  model/poc/results_v2.md       summary + comparison vs v1
  model/poc/results_v2.json     metrics
"""

from __future__ import annotations

import json
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
MATCHES = RAW / "matches"
OUT = Path(__file__).resolve().parent
SKILL_PATH = ROOT / "data" / "skill_snapshot.csv"


# ---------- 1. lookups -------------------------------------------------------

def build_match_to_site_map() -> dict[int, int]:
    """match_id -> the site_id we cached its summary under."""
    m: dict[int, int] = {}
    for site_dir in MATCHES.iterdir():
        if not site_dir.is_dir():
            continue
        try:
            site_id = int(site_dir.name)
        except ValueError:
            continue
        for season_file in site_dir.glob("*.json"):
            try:
                d = json.loads(season_file.read_text())
            except Exception:
                continue
            for mm in d.get("matches", []):
                try:
                    mid = int(mm["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                # first writer wins (we only use this for league context;
                # if a match appears in multiple sites — e.g. cup that
                # spans leagues — we accept whichever site was scraped first)
                m.setdefault(mid, site_id)
    return m


def load_skill() -> pd.DataFrame:
    if not SKILL_PATH.exists():
        raise SystemExit(f"missing {SKILL_PATH} — run model/skill/build_snapshot.py")
    s = pd.read_csv(SKILL_PATH)
    s = s.rename(columns={"player_id": "pid"})
    return s.set_index("pid")


def find_bbb_matches() -> list[int]:
    out: list[int] = []
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


def load_match_meta(match_id: int) -> dict | None:
    p = DETAIL / f"{match_id}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    md = (d.get("match_details") or [{}])[0]
    if md.get("match_type") != "Limited Overs":
        return None
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
        "ground_id": md.get("ground_id"),
        "competition_name": md.get("competition_name"),
        "overs_per_innings": overs_per_innings,
    }


def yyyymmdd(d: str | None) -> int:
    if not d:
        return 0
    try:
        a, b, c = d.split("/")
        return int(c) * 10000 + int(b) * 100 + int(a)
    except Exception:
        return 0


# ---------- 2. emit ball features -------------------------------------------

SKILL_COLS = [
    "bat_avg_skill", "bat_sr_skill",
    "bowl_econ_skill", "bowl_avg_skill", "bowl_sr_skill",
]

def lookup_skill(pid, skill_df) -> dict:
    """Return five skill values for a player (NaN if not in cohort)."""
    if pid is None or pd.isna(pid):
        return {k: np.nan for k in SKILL_COLS}
    try:
        row = skill_df.loc[int(pid)]
    except KeyError:
        return {k: np.nan for k in SKILL_COLS}
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return {k: row.get(k, np.nan) for k in SKILL_COLS}


def emit_innings_rows(match_id: int, meta: dict, skill_df, m2s) -> list[dict] | None:
    bp = BALLS / str(match_id) / "1.json"
    if not bp.exists():
        return None
    balls = json.loads(bp.read_text())
    if not balls:
        return None
    final_runs = sum((b.get("runs_bat") or 0) + (b.get("runs_extra") or 0) for b in balls)

    ovs_total = meta.get("overs_per_innings") or 50.0
    legal_total = int(round(ovs_total * 6))
    ymd = yyyymmdd(meta.get("match_date"))
    month = (ymd // 100) % 100
    season = ymd // 10000
    ground_id = meta.get("ground_id")
    site_id = m2s.get(match_id)

    runs = 0
    wickets = 0
    legal_balls = 0
    bat_state: dict[int, dict] = defaultdict(lambda: {"runs": 0, "balls": 0})
    bowl_state: dict[int, dict] = defaultdict(lambda: {"runs": 0, "balls": 0, "wkts": 0})
    out_batters: set[int] = set()
    rows: list[dict] = []

    for b in balls:
        striker = b.get("batter_id")
        ns = b.get("batter_id_ns")
        bowler = b.get("bowler_id")
        runs_bat = b.get("runs_bat") or 0
        runs_extra = b.get("runs_extra") or 0
        extras_type = b.get("extras_type") or 0
        is_legal = extras_type not in (1, 2)

        if is_legal:
            sr = bat_state[striker] if striker else {"runs": 0, "balls": 0}
            nsr = bat_state[ns] if ns else {"runs": 0, "balls": 0}
            br = bowl_state[bowler] if bowler else {"runs": 0, "balls": 0, "wkts": 0}
            row = {
                "match_id": match_id,
                "innings_seq": 1,
                "ball_seq": legal_balls + 1,
                "match_date_yyyymmdd": ymd,
                "season": season,
                "match_month": month,
                "site_id": site_id,
                "ground_id": ground_id,
                "overs_per_innings": ovs_total,
                "balls_gone": legal_balls,
                "balls_left": max(0, legal_total - legal_balls),
                "frac_innings": legal_balls / max(1, legal_total),
                "runs": runs,
                "wickets": wickets,
                "run_rate": (runs / (legal_balls / 6.0)) if legal_balls else 0.0,
                "wickets_in_hand": 10 - wickets,
                "striker_id": striker,
                "striker_runs_so_far": sr["runs"],
                "striker_balls_so_far": sr["balls"],
                "striker_intra_sr": (sr["runs"] * 100 / sr["balls"]) if sr["balls"] else np.nan,
                "ns_id": ns,
                "ns_runs_so_far": nsr["runs"],
                "ns_balls_so_far": nsr["balls"],
                "ns_intra_sr": (nsr["runs"] * 100 / nsr["balls"]) if nsr["balls"] else np.nan,
                "bowler_id": bowler,
                "bowler_balls_in_innings": br["balls"],
                "bowler_runs_in_innings": br["runs"],
                "bowler_wkts_in_innings": br["wkts"],
                "bowler_econ_so_far": (br["runs"] * 6 / br["balls"]) if br["balls"] else np.nan,
                "batters_remaining_count": max(0, 11 - len(out_batters) - 2),
                "y_final_innings_runs": final_runs,
            }
            for who, pid in (("striker", striker), ("ns", ns), ("bowler", bowler)):
                sk = lookup_skill(pid, skill_df)
                for k, v in sk.items():
                    row[f"{who}_{k}"] = v
            rows.append(row)

        runs += runs_bat + runs_extra
        if is_legal:
            legal_balls += 1
        if striker:
            if extras_type != 2:
                bat_state[striker]["balls"] += 1
                bat_state[striker]["runs"] += runs_bat
        if bowler:
            if is_legal:
                bowl_state[bowler]["balls"] += 1
            bowl_state[bowler]["runs"] += runs_bat + runs_extra
        dis = b.get("dismissed_batter_id")
        if dis:
            wickets += 1
            out_batters.add(dis)
            if bowler:
                bowl_state[bowler]["wkts"] += 1

    return rows


# ---------- 3. driver -------------------------------------------------------

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
CONTEXT_FEATURES = ["season", "match_month", "match_date_yyyymmdd"]
ALL_FEATURES = V1_FEATURES + SKILL_FEATURE_COLS + CONTEXT_FEATURES
TARGET = "y_final_innings_runs"


def train(train_df, val_df, q, features):
    model = lgb.LGBMRegressor(
        objective="quantile", alpha=q,
        n_estimators=600, learning_rate=0.05, num_leaves=63,
        min_data_in_leaf=20, verbose=-1,
    )
    model.fit(
        train_df[features], train_df[TARGET],
        eval_set=[(val_df[features], val_df[TARGET])],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    return model


def naive_baseline(df):
    return df["runs"].astype(float) + df["run_rate"].astype(float) * (df["balls_left"] / 6.0)


def per_position_mae(df, preds):
    df = df.copy()
    df["err"] = (preds - df[TARGET]).abs()
    bins = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 1000]
    labels = ["0-29", "30-59", "60-89", "90-119", "120-149",
              "150-179", "180-209", "210-239", "240-269", "270+"]
    df["bin"] = pd.cut(df["balls_gone"], bins=bins, right=False, labels=labels)
    return df.groupby("bin", observed=True)["err"].mean().reset_index()


def main() -> int:
    print("== loading skill snapshot, match→site map, building dataset ==", flush=True)
    skill = load_skill()
    m2s = build_match_to_site_map()
    print(f"  skill players: {len(skill):,}")
    print(f"  match→site map: {len(m2s):,}")

    bbb_ids = find_bbb_matches()
    rows: list[dict] = []
    for mid in bbb_ids:
        meta = load_match_meta(mid)
        if meta is None:
            continue
        r = emit_innings_rows(mid, meta, skill, m2s)
        if r:
            rows.extend(r)
    df = pd.DataFrame(rows)
    df["site_id"] = df["site_id"].astype("category")
    df["ground_id"] = df["ground_id"].astype("category")
    print(f"  innings-1 ball rows: {len(df):,} from {df['match_id'].nunique()} matches")

    rng = np.random.default_rng(seed=42)
    match_ids = df["match_id"].unique()
    rng.shuffle(match_ids)
    n_val = max(1, int(round(0.25 * len(match_ids))))
    val_ids = set(match_ids[:n_val].tolist())
    train_ids = set(match_ids[n_val:].tolist())
    train_df = df[df["match_id"].isin(train_ids)].reset_index(drop=True)
    val_df = df[df["match_id"].isin(val_ids)].reset_index(drop=True)
    print(f"  train: {len(train_ids)} matches / {len(train_df):,} balls")
    print(f"  val:   {len(val_ids)} matches / {len(val_df):,} balls")

    print("\n== fitting v1 baseline (no skill, no context) ==", flush=True)
    m1_50 = train(train_df, val_df, 0.5, V1_FEATURES)
    val_v1 = m1_50.predict(val_df[V1_FEATURES])

    print("== fitting v2 (skill + context features) ==", flush=True)
    m2_10 = train(train_df, val_df, 0.1, ALL_FEATURES)
    m2_50 = train(train_df, val_df, 0.5, ALL_FEATURES)
    m2_90 = train(train_df, val_df, 0.9, ALL_FEATURES)

    val = val_df.copy()
    val["v1_p50"] = val_v1
    val["v2_p10"] = m2_10.predict(val[ALL_FEATURES])
    val["v2_p50"] = m2_50.predict(val[ALL_FEATURES])
    val["v2_p90"] = m2_90.predict(val[ALL_FEATURES])
    val["naive"] = naive_baseline(val)

    mae_v1 = (val["v1_p50"] - val[TARGET]).abs().mean()
    mae_v2 = (val["v2_p50"] - val[TARGET]).abs().mean()
    mae_naive = (val["naive"] - val[TARGET]).abs().mean()
    cov_v2 = ((val[TARGET] >= val["v2_p10"]) & (val[TARGET] <= val["v2_p90"])).mean()

    print(f"\n== overall (val) ==")
    print(f"  MAE naive RR: {mae_naive:.2f}")
    print(f"  MAE v1 p50:   {mae_v1:.2f}")
    print(f"  MAE v2 p50:   {mae_v2:.2f}  (Δ {mae_v2 - mae_v1:+.2f} vs v1)")
    print(f"  v2 80% interval coverage: {cov_v2:.3f}")

    bin_v1 = per_position_mae(val, val["v1_p50"].values)
    bin_v2 = per_position_mae(val, val["v2_p50"].values)
    bin_naive = per_position_mae(val, val["naive"].values)
    bins = bin_naive.merge(bin_v1, on="bin", suffixes=("_naive", "_v1")).merge(
        bin_v2.rename(columns={"err": "err_v2"}), on="bin"
    )
    print("\n== per-position MAE ==")
    print(bins.to_string(index=False))

    # feature importances (v2 p50)
    imp = pd.DataFrame({
        "feature": ALL_FEATURES,
        "importance": m2_50.feature_importances_,
    }).sort_values("importance", ascending=False)

    md = []
    md.append("# POC v2 — skill + ground/league features")
    md.append("")
    md.append(f"- Train: **{len(train_ids):,}** matches / {len(train_df):,} balls")
    md.append(f"- Val:   **{len(val_ids):,}** matches / {len(val_df):,} balls")
    md.append(f"- Skill snapshot: {len(skill):,} players (last 12 months)")
    md.append("")
    md.append("## Headline (held-out val)")
    md.append("")
    md.append("| Model | MAE | Δ vs v1 |")
    md.append("|---|---:|---:|")
    md.append(f"| naive RR | {mae_naive:.1f} | — |")
    md.append(f"| v1 (state only) | {mae_v1:.1f} | (baseline) |")
    md.append(f"| v2 (+ skill + context) | **{mae_v2:.1f}** | **{mae_v2 - mae_v1:+.1f}** |")
    md.append("")
    md.append(f"v2 80% interval (p10-p90) coverage: **{cov_v2:.2f}**")
    md.append("")
    md.append("## Per-ball-position MAE")
    md.append("")
    md.append("| balls in | naive | v1 | v2 | v2 vs v1 |")
    md.append("|---|---:|---:|---:|---:|")
    for _, r in bins.iterrows():
        md.append(
            f"| {r['bin']} | {r['err_naive']:.1f} | {r['err_v1']:.1f} | "
            f"{r['err_v2']:.1f} | {r['err_v2'] - r['err_v1']:+.1f} |"
        )
    md.append("")
    md.append("## Top 20 feature importances (v2 p50)")
    md.append("")
    md.append("| feature | importance |")
    md.append("|---|---:|")
    for _, r in imp.head(20).iterrows():
        md.append(f"| `{r['feature']}` | {int(r['importance'])} |")
    md.append("")
    md.append("## What's still missing")
    md.append("")
    md.append("- Skill snapshot is current-only (`as_of=today`); historical")
    md.append("  predictions ideally need a per-match snapshot (`as_of =`")
    md.append("  match_date − 1d). For v2 we use one snapshot for all matches.")
    md.append("- Remaining-batters / remaining-bowlers skill aggregates are")
    md.append("  not yet computed (need batting-order parsed from BBB stream).")
    md.append("- Ground rolling stats (avg first-innings total, avg overs)")
    md.append("  not yet joined.")

    (OUT / "results_v2.md").write_text("\n".join(md) + "\n")
    (OUT / "results_v2.json").write_text(json.dumps({
        "mae_naive": float(mae_naive),
        "mae_v1": float(mae_v1),
        "mae_v2": float(mae_v2),
        "lift_v2_vs_v1_runs": float(mae_v1 - mae_v2),
        "v2_interval_coverage_80": float(cov_v2),
        "n_train_matches": len(train_ids),
        "n_val_matches": len(val_ids),
        "n_train_balls": int(len(train_df)),
        "n_val_balls": int(len(val_df)),
        "bins": bins.to_dict(orient="records"),
        "top_features": imp.head(20).to_dict(orient="records"),
    }, indent=2, default=str))
    print("\nwrote model/poc/results_v2.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
