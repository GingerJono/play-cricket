"""
Parse ResultsVault `l_desc` text to extract per-ball metadata.

RV's payload is rich — every ball carries a long-form commentary string
like:

    " D Noller to G McWilliams: 4 runs"
    " M Stander to W Hunt:  R Manota dismissed"

The bowler + striker + outcome (and dismissed batter on wickets) are
all named in plaintext. RV does also give us numeric `batter_id` /
`bowler_id` fields, but those are in RV's own player-id namespace
(11M-range ids that don't match Play-Cricket's 4M-range ones), so we
can't cross-reference them against `match_players` or the metadata
files.

Parsing the names out of `l_desc` and matching them back to PC
`match_players.player_name` sidesteps the ID-namespace problem
entirely. Coverage: ~99% of l_desc rows match the expected pattern.

Outcome taxonomy (from a sample of 92k balls, top 30 patterns):
    "No run" / "N run[s]"
    "N b" / "N lb"      bye / leg-bye
    "N w" / "N nb"      wide / no-ball
    "N runs, N nb"      runs off no-ball
    "N runs,N nb"       same, no space
    "<player> dismissed [optional reason]"
    "N run, <player> dismissed"   (run + wicket on same ball, rare)
"""

from __future__ import annotations

import re

EXTRAS_NB = 1
EXTRAS_WD = 2
EXTRAS_B  = 3
EXTRAS_LB = 4

# "<bowler> to <batter>: <outcome>" — names can have spaces, apostrophes,
# hyphens. The colon-space separator is consistent.
_DESC_RE = re.compile(r"^\s*(?P<bowler>.+?)\s+to\s+(?P<batter>.+?):\s*(?P<rest>.*)$")


def parse_l_desc(l_desc: str) -> dict | None:
    """
    Parse an RV `l_desc` string into a dict:

        {bowler_name, batter_name, runs_bat, runs_extra, extras_type,
         is_legal_ball, is_wicket, dismissed_name | None}

    Returns None if the string doesn't match the expected shape.
    """
    if not l_desc:
        return None
    m = _DESC_RE.match(l_desc)
    if not m:
        return None
    bowler = m.group("bowler").strip()
    batter = m.group("batter").strip()
    rest = m.group("rest").strip()
    out = {
        "bowler_name": bowler,
        "batter_name": batter,
        "runs_bat": 0,
        "runs_extra": 0,
        "extras_type": None,
        "is_legal_ball": 1,
        "is_wicket": False,
        "dismissed_name": None,
    }

    # Wicket: detect "<name> dismissed [...]" and split off any preceding
    # runs-portion (e.g. "1 run,  R Fiddler dismissed"). Treat the runs
    # part the same as a non-wicket outcome.
    wicket_re = re.compile(r"(?:^|,)\s*(?P<name>.+?)\s+dismissed(?:\b.*)?$")
    wm = wicket_re.search(rest)
    runs_part = rest
    if wm:
        out["is_wicket"] = True
        out["dismissed_name"] = wm.group("name").strip()
        runs_part = rest[:wm.start()].strip().rstrip(",")

    rp = runs_part.strip()
    if not rp or rp.lower() == "no run":
        return out
    if rp.lower() == "wide":
        out["extras_type"] = EXTRAS_WD; out["is_legal_ball"] = 0
        out["runs_extra"] = 1; return out
    if rp.lower() == "no ball":
        out["extras_type"] = EXTRAS_NB; out["is_legal_ball"] = 0
        out["runs_extra"] = 1; return out

    # "N run[s], M nb" or "N run[s],M nb" — combine.
    cm = re.fullmatch(r"(?P<a>\d+)\s+runs?\s*,\s*(?P<b>\d+)\s*nb", rp)
    if cm:
        out["runs_bat"] = int(cm.group("a"))
        out["extras_type"] = EXTRAS_NB; out["is_legal_ball"] = 0
        out["runs_extra"] = int(cm.group("b"))
        return out

    # "N run[s]" off the bat
    rm = re.fullmatch(r"(?P<n>\d+)\s+runs?", rp)
    if rm:
        out["runs_bat"] = int(rm.group("n")); return out

    # Single-token extras: "N w" / "N nb" / "N b" / "N lb"
    em = re.fullmatch(r"(?P<n>\d+)\s+(?P<t>w|nb|b|lb)", rp)
    if em:
        n, t = int(em.group("n")), em.group("t")
        if t == "w":
            out["extras_type"] = EXTRAS_WD; out["is_legal_ball"] = 0
            out["runs_extra"] = n
        elif t == "nb":
            out["extras_type"] = EXTRAS_NB; out["is_legal_ball"] = 0
            out["runs_extra"] = n
        elif t == "lb":
            out["extras_type"] = EXTRAS_LB
            out["runs_extra"] = n
        elif t == "b":
            out["extras_type"] = EXTRAS_B
            out["runs_extra"] = n
        return out

    return out  # unknown outcome — leave as a dot


# ---------- Name → player_id resolver ---------------------------------------

def _norm(name: str) -> str:
    return (name or "").replace("†", "").replace("*", "").strip()


def _last(name: str) -> str:
    n = _norm(name)
    return n.split()[-1] if n else ""


def build_name_index(players: list[dict]) -> dict:
    """
    Returns a tuple of three lookups for fast name resolution:

        ({normalised lower-case full name -> [player_id, ...]},
         {normalised lower-case last name  -> [player_id, ...]},
         {(initial, last)                  -> [player_id, ...]})

    `players` is a list of dicts with at least `player_id` and
    `player_name` keys (typically a slice of `match_players` for one
    team).
    """
    by_full: dict[str, list[int]] = {}
    by_last: dict[str, list[int]] = {}
    by_init_last: dict[tuple, list[int]] = {}
    for p in players:
        pid = p.get("player_id")
        if pid is None:
            continue
        full = _norm(p["player_name"]).lower()
        last = _last(p["player_name"]).lower()
        by_full.setdefault(full, []).append(pid)
        by_last.setdefault(last, []).append(pid)
        first = full.split()[0] if full else ""
        if first and last:
            by_init_last.setdefault((first[0], last), []).append(pid)
    return by_full, by_last, by_init_last


def resolve_name(rv_name: str, idx) -> int | None:
    """
    Resolve an RV-formatted name (typically "I Surname" with a single
    initial, but may be a full name) against a name index built from
    `match_players` for the right team.
    """
    by_full, by_last, by_init_last = idx
    n = _norm(rv_name)
    if not n:
        return None
    # Exact full-name match
    cand = by_full.get(n.lower())
    if cand and len(cand) == 1:
        return cand[0]
    # "I Surname" -> initial+last lookup
    parts = n.split()
    if len(parts) == 2 and len(parts[0]) <= 2:
        cand = by_init_last.get((parts[0][0].lower(), parts[-1].lower()))
        if cand and len(cand) == 1:
            return cand[0]
    # Last-name only (rare in RV but possible)
    cand = by_last.get(_last(n).lower())
    if cand and len(cand) == 1:
        return cand[0]
    return None
