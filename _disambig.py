"""
Resolve name-ambiguous BBB rows by cross-referencing the scorecard
tables `batting` and `bowling` for the same (match, innings).

Rationale (from Jono):

  > if there are two with exactly the same name then it's going to be
  > a case of reconstructing based on the bowling figures. there are
  > many clues. who appears higher in bowling figures will have the
  > earlier overs. spells tend to be in blocks of odds/evens

The first-pass resolvers in `_rv_balls.py` and `_nvplay_balls.py`
return None when a name maps to multiple PC players. This module
takes the resulting NULL-id ball rows and tries to recover them by:

  1. Aggregating BBB statistics for the ambiguous group (per
     `rv_player_id` for RV, per name-text for NV).
  2. Comparing those aggregates to the scorecard rows of the
     candidate PC player ids.
  3. Picking the candidate whose scorecard row best matches.

Tie-breakers for bowlers (when multiple candidates have similar
total figures):
  * Higher position in `bowling.bowl_position` ⇒ bowled the earlier
    overs. We score by the absolute delta between the average over
    number observed in BBB for this group vs the bowler's expected
    "first appearance over" implied by their position.
  * Spells alternate ends (odd/even-indexed overs). We count how
    many BBB overs for the group fall into odd-indexed slots vs
    even-indexed; bowlers who only bowled odd or only bowled even
    overs are preferred when the score is tied.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict


# ---------- Scoring helpers --------------------------------------------------

def _abs_dev(card_val, bbb_val):
    return abs((card_val or 0) - (bbb_val or 0))


def _score_batter(card: dict, bbb: dict) -> float:
    """Lower is better. Aggregates absolute deviations on runs + balls."""
    s = _abs_dev(card.get("runs"), bbb.get("runs"))
    s += _abs_dev(card.get("balls"), bbb.get("balls"))
    return s


def _score_bowler(card: dict, bbb: dict) -> float:
    """Lower is better. Aggregates runs (+wides+nb) + wickets + legal-balls."""
    s  = _abs_dev(card.get("runs"),     bbb.get("runs"))
    s += _abs_dev(card.get("wickets"),  bbb.get("wickets")) * 3   # weight wickets
    # legal-ball count: bowling.overs is "X.Y" -> X*6 + Y; balls already legal
    overs_str = card.get("overs") or ""
    if "." in overs_str:
        a, b = overs_str.split(".", 1)
        try: card_balls = int(a) * 6 + int(b)
        except ValueError: card_balls = 0
    else:
        try: card_balls = int(overs_str) * 6
        except (TypeError, ValueError): card_balls = 0
    s += _abs_dev(card_balls, bbb.get("balls"))
    return s


# ---------- Tie-breakers (bowler-only) --------------------------------------

def _bowl_position_penalty(card: dict, bbb_avg_over: float | None) -> float:
    """
    Closer to 0 means a better fit. Bowlers higher up the bowling card
    (smaller bowl_position) tend to bowl the earlier overs.
    """
    if bbb_avg_over is None:
        return 0.0
    # Without absolute knowledge of the over distribution, use bowl_position
    # as a tiny tie-breaker: position 1 ↔ avg-over biased early; position 8
    # ↔ avg-over biased late. We just penalise the difference very gently.
    pos = card.get("bowl_position") or 0
    if not pos:
        return 0.0
    return 0.05 * abs(pos * 5 - bbb_avg_over)


def _odd_even_consistency_bonus(card_pid: int,
                                 bbb_overs: list[int]) -> float:
    """
    Bowlers usually bowl from a single end → only odd-indexed or only
    even-indexed overs. Reward bowlers whose BBB overs are entirely
    one parity; penalise mixes.
    """
    if not bbb_overs:
        return 0.0
    odd = sum(1 for o in bbb_overs if (o or 0) % 2 == 1)
    even = len(bbb_overs) - odd
    # 0 if all-one-parity, scales up to ~1 for 50/50
    purity = 1.0 - (min(odd, even) / max(len(bbb_overs), 1)) * 2
    return -0.5 * purity   # bonus (negative score) for purity


# ---------- Public API ------------------------------------------------------

def best_pc_pid(
    bbb_stats: dict,
    candidate_pids: list[int],
    scorecard_rows: list[dict],
    role: str,
    bbb_overs: list[int] | None = None,
) -> int | None:
    """
    Pick the PC player_id whose scorecard row best matches a BBB
    aggregate.

    `role` is "batter" or "bowler". For batters, scorecard_rows are
    rows from `batting` (with `runs`, `balls`, `position`). For
    bowlers they're rows from `bowling` (with `runs`, `wickets`,
    `overs`, `bowl_position`).

    `bbb_overs` is the list of over numbers the bowler actually
    bowled in BBB — used for the odd/even consistency tie-breaker.

    Returns None if no candidate has a scorecard row, or if the best
    candidate is not strictly better than the runner-up.
    """
    relevant = [r for r in scorecard_rows
                if r.get("player_id") in candidate_pids]
    if not relevant:
        return None
    if len(relevant) == 1:
        return relevant[0]["player_id"]

    score_fn = _score_batter if role == "batter" else _score_bowler
    avg_over = (sum(bbb_overs) / len(bbb_overs)) if bbb_overs else None

    scored = []
    for r in relevant:
        s = score_fn(r, bbb_stats)
        if role == "bowler":
            s += _bowl_position_penalty(r, avg_over)
            s += _odd_even_consistency_bonus(r["player_id"], bbb_overs or [])
        scored.append((s, r["player_id"]))
    scored.sort()

    if len(scored) == 1:
        return scored[0][1]
    # Require a strict margin to claim a winner; otherwise leave NULL.
    if scored[0][0] + 0.5 < scored[1][0]:
        return scored[0][1]
    return None


# ---------- Loader integration ---------------------------------------------

def disambiguate_innings(
    cur: sqlite3.Cursor,
    match_id: int,
    innings_seq: int,
    *,
    is_home_batting: bool,
    home_club_id: str,
    away_club_id: str,
) -> tuple[int, int]:
    """
    Walk the balls table for this innings and try to repair NULL
    batter_id / bowler_id values via scorecard cross-reference.

    The ambiguous "key" comes from the s_desc / l_desc fields:
      * RV stores l_desc = " <bowler> to <batter>: <outcome>"
      * NV stores l_desc = "<bowler> to <striker>, <outcome>"  (we
        replaced newlines with " | " when caching)
    For each null-id ball we extract the relevant name and group balls
    that share the same name. Then we score the candidate PC pids
    (whose match_players rows have a name compatible with the BBB text)
    against the batting/bowling rows for this innings.

    Returns (n_batters_recovered, n_bowlers_recovered).
    """
    bat_club  = home_club_id if is_home_batting else away_club_id
    bowl_club = away_club_id if is_home_batting else home_club_id

    # ---- Pull all balls for this innings (we need s_desc/l_desc) ----
    ball_cols = ["rowid", "over_no", "ball_no", "runs_bat", "runs_extra",
                 "extras_type", "is_legal_ball", "batter_id", "bowler_id",
                 "dismissed_batter_id", "s_desc", "l_desc"]
    rows = cur.execute(f"""
        SELECT {", ".join(ball_cols)}
        FROM balls
        WHERE match_id = ? AND innings_seq = ?
    """, (match_id, innings_seq)).fetchall()
    if not rows:
        return (0, 0)
    balls = [dict(zip(ball_cols, r)) for r in rows]

    # ---- Pull match_players for both teams, indexed by club_id ----
    pc_players = {"home": [], "away": []}
    for side, pid, name in cur.execute(
        "SELECT team_side, player_id, player_name FROM match_players "
        "WHERE match_id = ?", (match_id,)).fetchall():
        pc_players[side].append({"player_id": pid, "player_name": name})
    bat_pc  = pc_players["home"] if is_home_batting else pc_players["away"]
    bowl_pc = pc_players["away"] if is_home_batting else pc_players["home"]

    bat_cols  = ["player_id", "runs", "balls", "position"]
    bowl_cols = ["player_id", "runs", "wickets", "overs", "bowl_position"]
    batting_rows = [dict(zip(bat_cols, r)) for r in cur.execute(
        "SELECT batsman_id, runs, balls, position "
        "FROM batting WHERE match_id=? AND innings_seq=?",
        (match_id, innings_seq)).fetchall()]
    bowling_rows = [dict(zip(bowl_cols, r)) for r in cur.execute(
        "SELECT bowler_id, runs, wickets, overs, bowl_position "
        "FROM bowling WHERE match_id=? AND innings_seq=?",
        (match_id, innings_seq)).fetchall()]

    # ---- Helper: compatible PC pids for a given name text -----------
    def _norm(s):
        return (s or "").replace("†", "").replace("*", "").strip().lower()

    def name_candidates(name_text: str, players: list[dict]) -> list[int]:
        n = _norm(name_text)
        if not n:
            return []
        parts = n.split()
        last = parts[-1] if parts else ""
        first_init = parts[0][0] if parts and len(parts[0]) <= 2 else None
        out: list[int] = []
        for p in players:
            full = _norm(p["player_name"])
            pp = full.split()
            if not pp:
                continue
            p_last = pp[-1]
            p_first = pp[0]
            if p_last != last:
                continue
            # If BBB text used initial+last, require first-letter match.
            if first_init and p_first[0] != first_init:
                continue
            out.append(p["player_id"])
        return out

    # ---- Parse a name from an l_desc like "X to Y, ..." or "X to Y: ..." ----
    import re
    # NV stores newlines as " | "; format consolidates as "X to Y, ..."
    # RV format is "X to Y: ..."
    desc_re = re.compile(r"^\s*(?P<bowler>.+?)\s+to\s+(?P<batter>.+?)[,:](?P<rest>.*)$",
                         re.DOTALL)

    n_bat_fix = 0
    n_bowl_fix = 0

    # ---- Disambiguate BATTERS ----
    bat_groups: dict[str, list[dict]] = defaultdict(list)
    for b in balls:
        if b["batter_id"] is not None:
            continue
        m = desc_re.match(b["l_desc"] or "")
        if m:
            bat_groups[m.group("batter").strip()].append(b)

    for name_text, group in bat_groups.items():
        cands = name_candidates(name_text, bat_pc)
        if not cands:
            continue
        bbb = {
            "runs":  sum(b["runs_bat"] or 0 for b in group),
            "balls": sum(1 for b in group if b["is_legal_ball"]),
        }
        chosen = best_pc_pid(bbb, cands, batting_rows, role="batter")
        if chosen is None:
            continue
        for b in group:
            cur.execute("UPDATE balls SET batter_id = ? WHERE rowid = ?",
                        (chosen, b["rowid"]))
            n_bat_fix += 1

    # ---- Disambiguate BOWLERS ----
    bowl_groups: dict[str, list[dict]] = defaultdict(list)
    for b in balls:
        if b["bowler_id"] is not None:
            continue
        m = desc_re.match(b["l_desc"] or "")
        if m:
            bowl_groups[m.group("bowler").strip()].append(b)

    for name_text, group in bowl_groups.items():
        cands = name_candidates(name_text, bowl_pc)
        if not cands:
            continue
        bbb = {
            "runs":    sum((b["runs_bat"] or 0)
                           + ((b["runs_extra"] or 0) if b["extras_type"] in (1, 2) else 0)
                           for b in group),
            "wickets": sum(1 for b in group if b["dismissed_batter_id"] is not None),
            "balls":   sum(1 for b in group if b["is_legal_ball"]),
        }
        bbb_overs = sorted({b["over_no"] for b in group if b["over_no"] is not None})
        chosen = best_pc_pid(bbb, cands, bowling_rows, role="bowler",
                              bbb_overs=bbb_overs)
        if chosen is None:
            continue
        for b in group:
            cur.execute("UPDATE balls SET bowler_id = ? WHERE rowid = ?",
                        (chosen, b["rowid"]))
            n_bowl_fix += 1

    return (n_bat_fix, n_bowl_fix)
