"""
Reconstruct per-ball metadata from an NV Play scorecard.

NV Play's `Overs[].Balls[]` only contains a compact `Display` string and a
`BallKey` like `"<innings>_<over>_<ball>"`. To get a row that matches the
ResultsVault `balls` schema (with `batter_id` / `non_striker_id` /
`bowler_id` / runs / extras / dismissal info), we have to:

  * Parse the Display token.
  * Walk balls in order, tracking striker / non-striker, swapping on odd
    runs and at end-of-over.
  * Resolve player names from BattingCard / BowlingCard / FOW back to
    Play-Cricket numeric `player_id`s using `match_players` rows.
  * Solve a simple constraint problem to assign a bowler to every over
    (each bowler bowls a known number of overs; no two consecutive overs
    by the same bowler; spells alternate ends).

Output rows match the schema of `build_db.balls`:

    (match_id, innings_seq, over_no, ball_no, ball_no_disp,
     batter_id, non_striker_id, bowler_id,
     team_batting_club_id, team_bowling_club_id,
     runs_bat, runs_extra, extras_type, is_legal_ball,
     dismissed_batter_id, s_desc, l_desc)
"""

from __future__ import annotations

import re

# Display tokens — see /home/user/play-cricket/data/raw/nv_match/*.json.
# - "."        dot ball (legal, 0 runs)
# - "1","2",.. runs off bat (legal)
# - "4","6"    boundary off bat (legal)
# - "W"        WICKET (legal ball, 0 runs unless something appended)
# - "w"        WIDE   (illegal, 1 extra; "w+N" = wide + N extra runs)
# - "Nw"       wide with N extra runs (e.g. "2w" = 2 extras off a wide)
# - "Nb"       BYE: legal ball, N extras
# - "Nlb"      LEG BYE: legal ball, N extras
# - "Nnb"      NO BALL: illegal, N runs (1 nb extra + N-1 off bat or byes)
# - "nb"       no-ball, 1 extra
# - "WD"       wide (alt spelling, rare)

# extras_type IDs match the ResultsVault encoding so the `balls` table
# stays consistent across both sources:
EXTRAS_NONE = None
EXTRAS_NB   = 1
EXTRAS_WD   = 2
EXTRAS_B    = 3
EXTRAS_LB   = 4
# 5/6 = no-ball + bye / no-ball + leg-bye, RV-only and uncommon


def parse_display(token: str) -> dict:
    """
    Parse a single Display token into runs / extras flags. Best-effort.

    Returns a dict with keys: runs_bat, runs_extra, extras_type,
    is_legal_ball, is_wicket. Unknown tokens decay to a dot ball.
    """
    t = (token or "").strip()
    out = {
        "runs_bat": 0, "runs_extra": 0,
        "extras_type": EXTRAS_NONE, "is_legal_ball": 1,
        "is_wicket": False,
        "raw": t,
    }
    if t == "" or t == ".":
        return out
    if t == "W":
        out["is_wicket"] = True
        return out
    # "w+2" / "w+3"  — wide + N additional runs (byes off the wide)
    m = re.fullmatch(r"w\+(\d+)", t)
    if m:
        out["extras_type"] = EXTRAS_WD
        out["is_legal_ball"] = 0
        out["runs_extra"] = 1 + int(m.group(1))
        return out
    # "Nw" / "WD" / "w" — wide with N total extras
    m = re.fullmatch(r"(\d*)w", t)
    if m:
        out["extras_type"] = EXTRAS_WD
        out["is_legal_ball"] = 0
        out["runs_extra"] = int(m.group(1) or "1")
        return out
    if t.upper() == "WD":
        out["extras_type"] = EXTRAS_WD
        out["is_legal_ball"] = 0
        out["runs_extra"] = 1
        return out
    # "Nnb" / "nb" — no-ball
    m = re.fullmatch(r"(\d*)nb", t)
    if m:
        out["extras_type"] = EXTRAS_NB
        out["is_legal_ball"] = 0
        # no-ball is always 1 extra; any extra runs are off-the-bat or byes
        n = int(m.group(1) or "1")
        out["runs_extra"] = 1
        out["runs_bat"] = n - 1
        return out
    # "Nlb" — leg bye
    m = re.fullmatch(r"(\d+)lb", t)
    if m:
        out["extras_type"] = EXTRAS_LB
        out["runs_extra"] = int(m.group(1))
        return out
    # "Nb" — bye
    m = re.fullmatch(r"(\d+)b", t)
    if m:
        out["extras_type"] = EXTRAS_B
        out["runs_extra"] = int(m.group(1))
        return out
    # Pure number — runs off bat
    m = re.fullmatch(r"(\d+)", t)
    if m:
        out["runs_bat"] = int(m.group(1))
        return out
    # Anything else: leave as a legal dot ball with raw note in s_desc
    return out


# ---------- Name → player_id resolution -------------------------------------

_INITIAL_RE = re.compile(r"^[A-Z]\s+")


def _normalise(name: str) -> str:
    if not name:
        return ""
    n = name.replace("†", "").replace("*", "").strip()
    return n


def _last_name(name: str) -> str:
    n = _normalise(name)
    return n.split()[-1] if n else ""


def resolve_name(nv_name: str, pc_players: list[dict]) -> int | None:
    """
    Best-effort name → play-cricket player_id resolution.

    `pc_players` is a list of {"player_id", "player_name"} dicts (one
    side of the match). Tries exact match, then last-name unique match,
    then "<initial> <surname>" match. Returns None if ambiguous.
    """
    target = _normalise(nv_name)
    if not target:
        return None
    # Exact match
    exact = [p for p in pc_players
             if _normalise(p["player_name"]).lower() == target.lower()]
    if len(exact) == 1:
        return exact[0]["player_id"]
    # Last-name match (handles "Wheeler" → "Will Wheeler")
    last = _last_name(nv_name)
    last_matches = [p for p in pc_players
                    if _last_name(p["player_name"]).lower() == last.lower()]
    if len(last_matches) == 1:
        return last_matches[0]["player_id"]
    # Initial + surname (handles "W Nolde" → "Will Nolde")
    parts = target.split()
    if len(parts) >= 2 and len(parts[0]) == 1:
        init, surname = parts[0].lower(), parts[-1].lower()
        cand = [p for p in pc_players
                if _last_name(p["player_name"]).lower() == surname
                and _normalise(p["player_name"])[:1].lower() == init]
        if len(cand) == 1:
            return cand[0]["player_id"]
    # Surname collision — caller falls back to NULL.
    return None


# ---------- Bowler-per-over assignment --------------------------------------

def _parse_overs(s) -> tuple[int, int]:
    """'9.4' -> (9, 4); '8' -> (8, 0)."""
    if s is None or s == "":
        return (0, 0)
    s = str(s)
    if "." in s:
        a, b = s.split(".", 1)
        return (int(a or 0), int(b or 0))
    return (int(s), 0)


def _has_end_partition(slots: list[int], target: int) -> bool:
    """Subset-sum DP — is there a subset of slots summing to `target`?"""
    if target < 0 or target > sum(slots):
        return False
    poss = {0}
    for s in slots:
        poss = poss | {p + s for p in poss}
        if target in poss:
            return True
    return target in poss


def assign_bowlers(n_overs: int, bowlers: list[dict]) -> list | None:
    """
    bowlers: list of {"player_id", "overs", "balls"} in BowlingCard order.
        `overs` is the int overs from "9.4" -> 9; `balls` the partial.
    n_overs: total overs in the innings (== len(Innings.Overs)).

    Returns a list[player_id] of length n_overs (one bowler per over).

    Strategy (kept robust + fast — exponential strict-search was hanging
    on innings whose slot counts can't be partitioned into matching
    halves):

      1. Compute slot counts (partials count as 1).
      2. Identify any bowler with a partial — they MUST bowl the last over.
      3. Try the strict end-parity rule (each bowler from one end only)
         only if subset-sum says it's even mathematically possible.
      4. Otherwise greedy-with-backtrack: at each over pick the bowler
         with the most remaining slots (BowlingCard order on ties),
         excluding the previous over's bowler.
    """
    # Slot counts.
    slots = []
    partial_idx = None
    for i, b in enumerate(bowlers):
        ov, bls = _parse_overs(b.get("overs"))
        n_slots = ov + (1 if bls else 0)
        slots.append(n_slots)
        if bls:
            partial_idx = i
    if sum(slots) != n_overs or n_overs == 0:
        return None
    pref = [b["player_id"] for b in bowlers]
    forced_last = (bowlers[partial_idx]["player_id"]
                   if partial_idx is not None else None)

    # ---- Path 1: strict end-parity, only if subset-sum is feasible ----
    odd = (n_overs + 1) // 2   # over indices 0,2,4,... (first, third...)
    even = n_overs // 2        # over indices 1,3,5,...
    if _has_end_partition(slots, odd):
        out: list = []
        remaining = {pid: n for pid, n in zip(pref, slots)}
        end_of: dict = {}
        cap = [200_000]   # iteration cap; bail out into greedy if exceeded

        def solve_strict(idx: int, prev) -> bool:
            if cap[0] <= 0:
                return False
            cap[0] -= 1
            if idx == n_overs:
                return all(v == 0 for v in remaining.values())
            parity = "A" if (idx % 2 == 0) else "B"
            candidates = pref
            if idx == n_overs - 1 and forced_last is not None:
                candidates = [forced_last]
            # Pick most-remaining first to balance.
            order = sorted(
                candidates,
                key=lambda bid: (-remaining[bid], pref.index(bid)),
            )
            for bid in order:
                if remaining[bid] <= 0 or bid == prev:
                    continue
                assigned = end_of.get(bid)
                if assigned is not None and assigned != parity:
                    continue
                new_end = assigned is None
                if new_end:
                    end_of[bid] = parity
                remaining[bid] -= 1
                out.append(bid)
                if solve_strict(idx + 1, bid):
                    return True
                out.pop()
                remaining[bid] += 1
                if new_end:
                    end_of.pop(bid, None)
            return False

        if solve_strict(0, None):
            return list(out)

    # ---- Path 2: relaxed (no-consecutive only) with a most-remaining
    #              greedy + bounded backtrack ----
    out = []
    remaining = {pid: n for pid, n in zip(pref, slots)}
    cap = [500_000]

    def solve_loose(idx: int, prev) -> bool:
        if cap[0] <= 0:
            return False
        cap[0] -= 1
        if idx == n_overs:
            return all(v == 0 for v in remaining.values())
        candidates = pref
        if idx == n_overs - 1 and forced_last is not None:
            candidates = [forced_last]
        order = sorted(
            candidates,
            key=lambda bid: (-remaining[bid], pref.index(bid)),
        )
        for bid in order:
            if remaining[bid] <= 0 or bid == prev:
                continue
            remaining[bid] -= 1
            out.append(bid)
            if solve_loose(idx + 1, bid):
                return True
            out.pop()
            remaining[bid] += 1
        return False

    if solve_loose(0, None):
        return list(out)
    return None


# ---------- Match-level resolution ------------------------------------------

def build_nv_id_map(scorecard: dict) -> dict:
    """
    Map every NV `Id` (UUID/scoring-engine id) -> play-cricket player_id
    using the rich `Match.Team{1,2}Players[]` rosters (which carry the
    `ExternalId` straight from PC). Falls back to {} on missing data.
    """
    out: dict[str, int] = {}
    m = scorecard.get("Match") or {}
    for key in ("Team1Players", "Team2Players"):
        for p in m.get(key) or []:
            nv_id = p.get("Id")
            ext = p.get("ExternalId")
            if not nv_id or not ext:
                continue
            try:
                out[nv_id] = int(ext)
            except (TypeError, ValueError):
                continue
    return out


def innings_batting_side(scorecard: dict, inn_idx: int) -> str | None:
    """
    Returns "team1" or "team2" for which side bats in the inn_idx-th
    innings. Uses `IsTeam2BattingFirst` when present.
    """
    m = scorecard.get("Match") or {}
    team2_first = m.get("IsTeam2BattingFirst")
    if team2_first is None:
        return None
    if inn_idx == 0:
        return "team2" if team2_first else "team1"
    return "team1" if team2_first else "team2"


# ---------- Per-innings ball walk -------------------------------------------

def reconstruct_innings(
    inn: dict,
    pc_batting_players: list[dict],
    pc_bowling_players: list[dict],
    bat_club_id: str,
    bowl_club_id: str,
    nv_id_map: dict | None = None,
) -> list[dict]:
    """
    Walk one NV-Play innings and yield a list of ball-dicts ready to
    insert into the `balls` table.

    `pc_batting_players` / `pc_bowling_players` are the PC `match_players`
    rows for the batting / bowling team respectively.
    """
    # Filter out summary rows; preserve array order = batting order.
    bat_order_nv = [b for b in inn.get("BattingCard") or []
                    if not b.get("IsSummary") and b.get("Id")]
    bowl_card = [b for b in inn.get("BowlingCard") or []
                 if not b.get("IsSummary") and b.get("Id")]
    overs = inn.get("Overs") or []

    # Resolve NV `Id` (UUID) -> PC player_id via the match-level map first
    # (most reliable; comes from Team{1,2}Players[].ExternalId), then fall
    # back to fuzzy name match.
    nv_id_map = nv_id_map or {}

    def resolve(card_row: dict, players: list[dict]) -> int | None:
        nvid = card_row.get("Id")
        pid = nv_id_map.get(nvid)
        if pid:
            return pid
        return resolve_name(card_row.get("PlayerName") or "", players)

    bat_pid: list[int | None] = [resolve(b, pc_batting_players) for b in bat_order_nv]
    bowl_pid: list[int | None] = [resolve(b, pc_bowling_players) for b in bowl_card]

    # Bowler-per-over assignment (returns None if infeasible).
    bowler_card = [
        {"player_id": pid,
         "overs": b.get("Overs"),
         "balls": _parse_overs(b.get("Overs"))[1]}
        for pid, b in zip(bowl_pid, bowl_card)
        if pid is not None
    ]
    bowler_per_over = None
    if bowler_card and overs:
        bowler_per_over = assign_bowlers(len(overs), bowler_card)

    # FOW lookup: {ball_global_no: dismissed_pc_player_id_or_None}
    fow_by_ball: dict[int, int | None] = {}
    for f in inn.get("FallOfWicketList") or []:
        b_no = f.get("Ball")
        if b_no is None:
            continue
        # Try to resolve the dismissed batter via FOW name.
        nv_name = f.get("Batter") or ""
        pid = resolve_name(nv_name, pc_batting_players)
        fow_by_ball[int(b_no)] = pid

    # Walk balls in playing order.
    striker_idx = 0       # index into bat_order_nv
    non_striker_idx = 1
    next_in_idx = 2
    rows: list[dict] = []
    ball_global = 0       # 1-indexed running ball count across the innings

    for over_i, over in enumerate(overs):
        over_no = over.get("OverNo") or (over_i + 1)
        ball_list = over.get("Balls") or []
        legal_in_over = 0
        bowler_pid = bowler_per_over[over_i] if bowler_per_over and over_i < len(bowler_per_over) else None

        for j, b in enumerate(ball_list):
            ball_global += 1
            disp = b.get("Display") or ""
            parsed = parse_display(disp)
            # Within-over ball number = j+1 (matches RV semantics: counts NB/Wd).
            ball_no = j + 1
            if parsed["is_legal_ball"]:
                legal_in_over += 1

            # Striker / non-striker pids (may be None if name didn't resolve).
            s_pid = bat_pid[striker_idx] if striker_idx < len(bat_pid) else None
            ns_pid = bat_pid[non_striker_idx] if non_striker_idx < len(bat_pid) else None

            dismissed_pid = None
            if parsed["is_wicket"]:
                # Prefer the FOW row mapping for this ball's global number;
                # falls back to the on-strike batter.
                dismissed_pid = fow_by_ball.get(ball_global) or s_pid

            rows.append({
                "innings_seq":        inn.get("innings_seq"),
                "over_no":            int(over_no),
                "ball_no":            ball_no,
                "ball_no_disp":       legal_in_over if parsed["is_legal_ball"] else None,
                "batter_id":          s_pid,
                "non_striker_id":     ns_pid,
                "bowler_id":          bowler_pid,
                "team_batting_club_id":  bat_club_id,
                "team_bowling_club_id":  bowl_club_id,
                "runs_bat":           parsed["runs_bat"],
                "runs_extra":         parsed["runs_extra"],
                "extras_type":        parsed["extras_type"],
                "is_legal_ball":      parsed["is_legal_ball"],
                "dismissed_batter_id": dismissed_pid,
                "s_desc":             disp,
                "l_desc":             "",   # NV Play doesn't carry long-form
            })

            # Strike rotation on odd batter runs.
            total_run = parsed["runs_bat"] + (
                parsed["runs_extra"] if parsed["extras_type"] in (EXTRAS_B, EXTRAS_LB)
                else 0
            )
            if total_run % 2 == 1:
                striker_idx, non_striker_idx = non_striker_idx, striker_idx

            # On wicket, replace dismissed batter with next from order.
            if parsed["is_wicket"]:
                if next_in_idx < len(bat_order_nv):
                    striker_idx = next_in_idx
                    next_in_idx += 1

        # End-of-over swap (only if the over ended on a legal ball).
        striker_idx, non_striker_idx = non_striker_idx, striker_idx

    return rows
