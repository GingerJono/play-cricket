#!/usr/bin/env python3
"""
predict_winprob — load saved LGBM models + isotonic calibrators and produce
P(team batting first wins) per ball / per snapshot for any match in the DB
that has ball-by-ball coverage.

Usage:
    from model.poc.predict_winprob import (
        load_artefacts, winprob_at_innings1_end, winprob_trajectory,
    )

    art = load_artefacts()
    p = winprob_at_innings1_end(art, match_id=7325340)   # → 0.0..1.0 or None

    # full per-ball trajectory (both innings) — DataFrame
    df = winprob_trajectory(art, match_id=7325340)

The predictor reuses the same feature-engineering as
run_winprob_v0.py / run_winprob_chase.py — it imports from those modules
to guarantee the live features match training exactly.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
ART = Path(__file__).resolve().parent / "winprob"
DB = ROOT / "data" / "rainham.db"

sys.path.insert(0, str(ROOT))
from model.poc.run_poc_v3d import (
    add_state, join_skill, join_ground, join_remaining_batters,
    join_bowl_team_strength, ALL_FEATURES,
)
from model.poc.run_winprob_chase import (
    add_chase_state, join_features as join_chase_features, ALL_F as CHASE_FEATURES,
)


@dataclass
class Artefacts:
    inn1_model: lgb.Booster
    inn1_iso_x: np.ndarray
    inn1_iso_y: np.ndarray
    chase_model: lgb.Booster
    chase_iso_x: np.ndarray
    chase_iso_y: np.ndarray


def load_artefacts(art_dir: Path = ART) -> Artefacts:
    inn1 = lgb.Booster(model_file=str(art_dir / "innings1_lgbm.txt"))
    chase = lgb.Booster(model_file=str(art_dir / "chase_lgbm.txt"))
    iso1 = json.loads((art_dir / "innings1_isotonic.json").read_text())
    iso2 = json.loads((art_dir / "chase_isotonic.json").read_text())
    return Artefacts(
        inn1_model=inn1,
        inn1_iso_x=np.asarray(iso1["x"]),
        inn1_iso_y=np.asarray(iso1["y"]),
        chase_model=chase,
        chase_iso_x=np.asarray(iso2["x"]),
        chase_iso_y=np.asarray(iso2["y"]),
    )


def apply_isotonic(p_raw: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Replay an IsotonicRegression as a piecewise-linear lookup."""
    return np.interp(np.clip(p_raw, x[0], x[-1]), x, y)


def _load_balls_for_match(match_id: int, innings_seq: int) -> tuple[pd.DataFrame, sqlite3.Connection]:
    """Mirror the prep done in run_winprob_v0 / chase but for one match."""
    con = sqlite3.connect(DB)
    matches = pd.read_sql(
        "SELECT match_id, match_date, season, "
        "       home_team_id, away_team_id, "
        "       home_club_id, away_club_id, ground_id "
        "FROM matches WHERE match_id = ?", con, params=[match_id])
    if matches.empty:
        con.close()
        return pd.DataFrame(), con
    matches["match_date_yyyymmdd"] = matches["match_date"].apply(
        lambda s: int(s.split("/")[2]) * 10000 + int(s.split("/")[1]) * 100
                   + int(s.split("/")[0]))
    matches["snap_ym"] = (matches["match_date_yyyymmdd"] // 100).astype("Int64")
    matches["match_month"] = matches["match_date"].apply(
        lambda s: int(s.split("/")[1]))
    matches["season"] = pd.to_numeric(matches["season"], errors="coerce")

    balls = pd.read_sql(
        "SELECT match_id, innings_seq, ball_no, ball_no_disp, over_no, "
        "       batter_id, non_striker_id, bowler_id, "
        "       team_batting_club_id, team_bowling_club_id, "
        "       runs_bat, runs_extra, extras_type, is_legal_ball, "
        "       dismissed_batter_id "
        "FROM balls WHERE match_id = ? AND innings_seq = ? "
        "ORDER BY ball_no", con, params=[match_id, innings_seq])
    if balls.empty:
        con.close()
        return balls, con

    # overs_per_innings — heuristic clamp by ball count (mirrors run_poc_v3d)
    legal = int(balls["is_legal_ball"].sum())
    overs_per_innings = (
        50 if legal > 270 else 45 if legal > 240 else 40 if legal > 210 else 50
    )
    balls = balls.merge(matches, on="match_id", how="inner")
    balls["overs_per_innings"] = overs_per_innings
    balls["bowl_team_side"] = np.where(
        balls["team_batting_club_id"].astype(str)
        == balls["home_club_id"].astype(str),
        "away", "home",
    )
    return balls, con


def winprob_innings1(art: Artefacts, match_id: int) -> pd.DataFrame:
    """Per-ball P(team batting first wins) across innings 1.

    Returns DataFrame with columns: ball_no, over_no, runs, wickets, p_bat_first_wins.
    Empty if no BBB or feature joins fail.
    """
    balls, con = _load_balls_for_match(match_id, innings_seq=1)
    if balls.empty:
        con.close()
        return pd.DataFrame()
    df = add_state(balls)
    df, _ = join_skill(df)
    df = join_ground(df)
    df = join_remaining_batters(df, con)
    df = join_bowl_team_strength(df, con)
    con.close()

    X = df[ALL_FEATURES].astype("float32")
    p_raw = art.inn1_model.predict(X)
    p_cal = apply_isotonic(p_raw, art.inn1_iso_x, art.inn1_iso_y)
    out = df[["ball_no", "over_no", "runs", "wickets"]].copy()
    out["p_bat_first_wins"] = p_cal
    return out.reset_index(drop=True)


def winprob_innings2(art: Artefacts, match_id: int) -> pd.DataFrame:
    """Per-ball P(team batting first wins) across innings 2.

    The chase model returns P(chasing side wins). Team batting first is
    the team bowling now → P(bat_first_wins) = 1 - P(chase wins).
    """
    balls, con = _load_balls_for_match(match_id, innings_seq=2)
    if balls.empty:
        con.close()
        return pd.DataFrame()

    # innings 2 needs target — prefer canonical innings.runs, fall back to
    # BBB SUM(runs_bat + runs_extra) for matches we only have via NV Play.
    inn1 = pd.read_sql(
        "SELECT runs FROM innings WHERE match_id = ? AND innings_seq = 1",
        con, params=[match_id])
    if not inn1.empty and inn1.iloc[0]["runs"] is not None:
        target = int(inn1.iloc[0]["runs"]) + 1
    else:
        bbb = con.execute(
            "SELECT COALESCE(SUM(runs_bat),0) + COALESCE(SUM(runs_extra),0) "
            "FROM balls WHERE match_id = ? AND innings_seq = 1",
            (match_id,)).fetchone()
        if not bbb or not bbb[0]:
            con.close()
            return pd.DataFrame()
        target = int(bbb[0]) + 1
    balls["target"] = target

    df = add_chase_state(balls)
    df["ns_runs_so_far"] = np.nan
    df["ns_balls_so_far"] = np.nan
    df["ns_intra_sr"] = np.nan
    df = join_chase_features(df, con)
    con.close()

    X = df[CHASE_FEATURES].astype("float32")
    p_chase_raw = art.chase_model.predict(X)
    p_chase_cal = apply_isotonic(p_chase_raw, art.chase_iso_x, art.chase_iso_y)
    out = df[["ball_no", "over_no", "runs", "wickets"]].copy()
    out["p_bat_first_wins"] = 1.0 - p_chase_cal
    return out.reset_index(drop=True)


def winprob_trajectory(art: Artefacts, match_id: int) -> dict:
    """Combined per-ball trajectory across both innings.

    Returns dict with keys:
      innings1: list of {ball, over, runs, wickets, p}
      innings2: list of {ball, over, runs, wickets, p}
      end_of_innings1_p:   float | None
      final_p:             float | None
    """
    out: dict = {"innings1": [], "innings2": [],
                 "end_of_innings1_p": None, "final_p": None}

    df1 = winprob_innings1(art, match_id)
    if not df1.empty:
        out["innings1"] = [
            {"ball": int(r.ball_no), "over": int(r.over_no),
             "runs": int(r.runs), "wickets": int(r.wickets),
             "p": float(r.p_bat_first_wins)}
            for r in df1.itertuples()
        ]
        out["end_of_innings1_p"] = float(df1.iloc[-1]["p_bat_first_wins"])

    df2 = winprob_innings2(art, match_id)
    if not df2.empty:
        out["innings2"] = [
            {"ball": int(r.ball_no), "over": int(r.over_no),
             "runs": int(r.runs), "wickets": int(r.wickets),
             "p": float(r.p_bat_first_wins)}
            for r in df2.itertuples()
        ]
        out["final_p"] = float(df2.iloc[-1]["p_bat_first_wins"])
    return out


def winprob_at_innings1_end(art: Artefacts, match_id: int) -> float | None:
    df = winprob_innings1(art, match_id)
    if df.empty:
        return None
    return float(df.iloc[-1]["p_bat_first_wins"])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id", type=int)
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    art = load_artefacts()
    traj = winprob_trajectory(art, args.match_id)
    if args.full:
        print(json.dumps(traj, indent=2))
    else:
        n1 = len(traj["innings1"])
        n2 = len(traj["innings2"])
        print(f"match {args.match_id}: innings1={n1} balls, innings2={n2} balls")
        print(f"  P(bat first wins) end of innings 1: "
              f"{traj['end_of_innings1_p']:.3f}" if traj["end_of_innings1_p"] is not None
              else "  no innings 1 BBB")
        if traj["final_p"] is not None:
            print(f"  P(bat first wins) final: {traj['final_p']:.3f}")
