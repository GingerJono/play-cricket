"""
Map NV Play scorecard balls to per-ball Play-Cricket player IDs.

NV's commentary text (the `C` field, populated when the request
includes `&commentary=true`) names the bowler and on-strike batter on
every ball. That sidesteps the striker-rotation reconstruction the
earlier version of this module attempted.

`C`-field grammar (from sampling 92k balls):

    "<Bowler> to <Striker>, <outcome>"

  - <Bowler> / <Striker> use **surname-only**, with an initial prefix
    when there's a name collision on the team (e.g. "B Patel" vs
    "S Patel", "R Nolde" vs "W Nolde").

  - <outcome> is one of:
        "no run"
        "<n> run" / "<n> runs"
        "<n>b"           bye
        "<n>lb"          leg-bye
        "w" / "w+<n>"    wide (+ N additional byes off the wide)
        "<n>nb"          no-ball
        "<n> run, <m>nb" run + no-ball (rare combination)
        "OUT\\n<Full Name> [c <Fielder>] b <Bowler> <runs> (<balls>)"
                         wicket — the full name on the second line is
                         the dismissed batter (may be the non-striker
                         on a run-out, so we use this rather than the
                         on-strike name).

For per-match name -> player_id resolution we lean on NV's own roster
data:
  * `Match.Team{1,2}Players[]` carries `Id` (NV UUID) plus
    `ExternalId` (= PC `player_id`) and `PlayerName` (full name) for
    every team-sheet member. We build a lookup table from these once
    per match, indexed by surname and by `(initial, surname)`.

When a surname is unique within a team's roster, surname suffices.
When two players share a surname, the C-field uses an initial prefix
("B Patel") which we resolve via the `(initial, surname)` index.

Output rows match the schema of `build_db.balls`.
"""

from __future__ import annotations

import re


# ---------- Display token -> runs / extras / wicket flag --------------------

EXTRAS_NB = 1
EXTRAS_WD = 2
EXTRAS_B  = 3
EXTRAS_LB = 4


def parse_outcome(tail: str) -> dict:
    """
    Parse the outcome part of a C-field (everything after the comma).

    Returns dict with keys: runs_bat, runs_extra, extras_type,
    is_legal_ball, is_wicket, dismissed_full_name | None.
    Unknown tokens fall through as a dot ball.
    """
    out = {
        "runs_bat": 0, "runs_extra": 0,
        "extras_type": None, "is_legal_ball": 1,
        "is_wicket": False, "dismissed_full_name": None,
    }
    t = (tail or "").strip()
    if not t or t.lower() == "no run":
        return out

    # Wicket: "OUT\n<Full Name> [c X] b Y <runs> (<balls>)"
    if t.startswith("OUT"):
        out["is_wicket"] = True
        rest = t[3:].lstrip("\n :")
        # Take everything up to the first " c " or " b " or "  " — the
        # leading clause IS the dismissed batter's full name.
        m = re.match(r"^(?P<name>[^\n]+?)\s+(?:c\s|b\s|st\s|run out|lbw|hit wkt|retired)", rest)
        if m:
            out["dismissed_full_name"] = m.group("name").strip()
        else:
            # fallback: first line
            out["dismissed_full_name"] = rest.split("\n", 1)[0].strip()
        return out

    # "N run, Mnb" combo
    cm = re.fullmatch(r"(?P<a>\d+)\s+runs?\s*,\s*(?P<b>\d+)\s*nb", t)
    if cm:
        out["runs_bat"] = int(cm.group("a"))
        out["extras_type"] = EXTRAS_NB; out["is_legal_ball"] = 0
        out["runs_extra"] = int(cm.group("b"))
        return out

    # "N run[s]"
    rm = re.fullmatch(r"(?P<n>\d+)\s+runs?", t)
    if rm:
        out["runs_bat"] = int(rm.group("n")); return out

    # "w" or "w+N"
    wm = re.fullmatch(r"w(?:\+(?P<n>\d+))?", t)
    if wm:
        out["extras_type"] = EXTRAS_WD; out["is_legal_ball"] = 0
        out["runs_extra"] = 1 + int(wm.group("n") or 0)
        return out

    # "<n>w" / "<n>nb" / "<n>lb" / "<n>b"
    em = re.fullmatch(r"(?P<n>\d+)(?P<t>w|nb|lb|b)", t)
    if em:
        n, tt = int(em.group("n")), em.group("t")
        if tt == "w":
            out["extras_type"] = EXTRAS_WD; out["is_legal_ball"] = 0
            out["runs_extra"] = n
        elif tt == "nb":
            out["extras_type"] = EXTRAS_NB; out["is_legal_ball"] = 0
            out["runs_extra"] = n
        elif tt == "lb":
            out["extras_type"] = EXTRAS_LB; out["runs_extra"] = n
        elif tt == "b":
            out["extras_type"] = EXTRAS_B; out["runs_extra"] = n
        return out

    return out


_C_RE = re.compile(r"^\s*(?P<bowler>.+?)\s+to\s+(?P<striker>.+?),\s*(?P<rest>.*)$",
                   re.DOTALL)


def parse_c(c: str) -> dict | None:
    """
    Parse a full NV `C` field. Returns dict with bowler_name,
    striker_name, plus the parse_outcome() fields. None on no match.
    """
    if not c:
        return None
    m = _C_RE.match(c)
    if not m:
        return None
    bowler = m.group("bowler").strip()
    striker = m.group("striker").strip()
    parsed = parse_outcome(m.group("rest"))
    parsed["bowler_name"] = bowler
    parsed["striker_name"] = striker
    return parsed


# ---------- Match-level helpers ---------------------------------------------

def innings_batting_side(scorecard: dict, inn_idx: int) -> str | None:
    """Returns 'team1' or 'team2' for which side bats in the inn_idx-th innings."""
    m = scorecard.get("Match") or {}
    team2_first = m.get("IsTeam2BattingFirst")
    if team2_first is None:
        return None
    if inn_idx == 0:
        return "team2" if team2_first else "team1"
    return "team1" if team2_first else "team2"


# ---------- Roster -> name resolver -----------------------------------------

def _norm(s: str | None) -> str:
    if not s:
        return ""
    return s.replace("†", "").replace("*", "").strip()


def build_team_index(team_players: list[dict]) -> dict:
    """
    Index a single NV `Match.TeamNPlayers[]` list by full name and
    `(initial, surname)`. The `ExternalId` field is the PC player_id.
    """
    by_full: dict[str, list[int]] = {}
    by_last: dict[str, list[int]] = {}
    by_init_last: dict[tuple, list[int]] = {}
    for p in team_players or []:
        ext = p.get("ExternalId")
        if not ext:
            continue
        try:
            pid = int(ext)
        except (TypeError, ValueError):
            continue
        full = _norm(p.get("PlayerName") or p.get("Name") or "").lower()
        if not full:
            continue
        parts = full.split()
        if len(parts) < 2:
            continue
        first, last = parts[0], parts[-1]
        by_full.setdefault(full, []).append(pid)
        by_last.setdefault(last, []).append(pid)
        by_init_last.setdefault((first[0], last), []).append(pid)
    return {"by_full": by_full, "by_last": by_last,
            "by_init_last": by_init_last}


def resolve(name: str, idx: dict) -> int | None:
    """
    Resolve a C-field-style name (e.g. "Little", "B Patel", "R Nolde")
    against a team index built by `build_team_index`.
    """
    n = _norm(name).lower()
    if not n:
        return None
    parts = n.split()
    # Single-word: surname only.
    if len(parts) == 1:
        cand = idx["by_last"].get(parts[0])
        if cand and len(cand) == 1:
            return cand[0]
        return None
    # Two words with a 1-letter initial: "B Patel"
    if len(parts) == 2 and len(parts[0]) <= 2:
        cand = idx["by_init_last"].get((parts[0][0], parts[-1]))
        if cand and len(cand) == 1:
            return cand[0]
        # also try surname alone
        cand = idx["by_last"].get(parts[-1])
        if cand and len(cand) == 1:
            return cand[0]
        return None
    # Full name (rare in C field)
    cand = idx["by_full"].get(n)
    if cand and len(cand) == 1:
        return cand[0]
    return None


# ---------- Per-innings ball walk -------------------------------------------

def reconstruct_innings(
    inn: dict,
    bat_team_players: list[dict],
    bowl_team_players: list[dict],
    bat_club_id: str,
    bowl_club_id: str,
) -> list[dict]:
    """
    Walk one NV innings and emit ball-row dicts ready for the `balls`
    table. Uses the C field to identify bowler / striker per ball;
    falls back to None when text is missing or names don't resolve.
    """
    overs = inn.get("Overs") or []
    bat_idx  = build_team_index(bat_team_players)
    bowl_idx = build_team_index(bowl_team_players)

    rows: list[dict] = []
    for over_i, over in enumerate(overs):
        over_no = over.get("OverNo") or (over_i + 1)
        ball_list = over.get("Balls") or []
        legal_in_over = 0
        for j, b in enumerate(ball_list):
            disp = b.get("Display") or ""
            c    = b.get("C") or ""
            parsed = parse_c(c)
            ball_no = j + 1
            if parsed:
                bowler_pid = resolve(parsed["bowler_name"], bowl_idx)
                striker_pid = resolve(parsed["striker_name"], bat_idx)
                if parsed["is_legal_ball"]:
                    legal_in_over += 1
                dismissed_pid = None
                if parsed["is_wicket"] and parsed["dismissed_full_name"]:
                    dismissed_pid = resolve(parsed["dismissed_full_name"], bat_idx)
                rows.append({
                    "innings_seq":    inn.get("innings_seq"),
                    "over_no":        int(over_no),
                    "ball_no":        ball_no,
                    "ball_no_disp":   legal_in_over if parsed["is_legal_ball"] else None,
                    "batter_id":      striker_pid,
                    "non_striker_id": None,
                    "bowler_id":      bowler_pid,
                    "team_batting_club_id":  bat_club_id,
                    "team_bowling_club_id":  bowl_club_id,
                    "runs_bat":       parsed["runs_bat"],
                    "runs_extra":     parsed["runs_extra"],
                    "extras_type":    parsed["extras_type"],
                    "is_legal_ball":  parsed["is_legal_ball"],
                    "dismissed_batter_id": dismissed_pid,
                    "s_desc":         disp,
                    "l_desc":         c.replace("\n", " | "),
                })
            else:
                # Display-only fallback for older cached scorecards that
                # were captured before commentary=true was used.
                from_disp = parse_outcome(disp)
                if from_disp["is_legal_ball"]:
                    legal_in_over += 1
                rows.append({
                    "innings_seq":    inn.get("innings_seq"),
                    "over_no":        int(over_no),
                    "ball_no":        ball_no,
                    "ball_no_disp":   legal_in_over if from_disp["is_legal_ball"] else None,
                    "batter_id":      None,
                    "non_striker_id": None,
                    "bowler_id":      None,
                    "team_batting_club_id":  bat_club_id,
                    "team_bowling_club_id":  bowl_club_id,
                    "runs_bat":       from_disp["runs_bat"],
                    "runs_extra":     from_disp["runs_extra"],
                    "extras_type":    from_disp["extras_type"],
                    "is_legal_ball":  from_disp["is_legal_ball"],
                    "dismissed_batter_id": None,
                    "s_desc":         disp,
                    "l_desc":         "",
                })
    return rows
