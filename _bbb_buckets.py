"""
Pure-functional bucket maths for ball-by-ball stats.

Every function in here takes plain iterables of dicts (the ball
records bundled into the per-Rainham-player JSON) and returns
aggregate dicts keyed by bucket. No SQL, no IO, no Python state —
that way the same module is callable both at build time (Python) and
in concept again from the dashboard JS (which re-implements the same
buckets in plain JS).

Ball record shape (keys we rely on):
    over_no            int — over index, 0-based for first over
    ball_no            int — within over (legal balls only matter for
                             rate stats; we count physical deliveries
                             when summing runs/wickets)
    is_legal_ball      0 / 1
    runs_bat           int
    runs_extra         int
    extras_type        int | None
    wicket             0 / 1   (assigned at build time from
                                dismissed_batter_id)
    batter_id          int | None
    bowler_id          int | None
    batting_hand       'right' | 'left' | 'unknown' | None  (snapshot
                                of the batter's metadata at build
                                time, only present on bowler-view
                                ball records)
    bowling_type       'pace' | 'spin' | 'none' | 'unknown' | None
    bowling_arm        'right' | 'left' | 'unknown' | None
    pace_type          'fast' | 'medium' | 'slow' | 'unknown' | None
    spin_type          'wrist' | 'finger' | 'unknown' | None
"""

from __future__ import annotations

from typing import Iterable


# -------------------------------------------------------- bucket labels --

PHASE_LABELS_T20 = ["1-6", "7-15", "16-20"]
PHASE_LABELS_50  = ["1-10", "11-20", "21-30", "31-40", "41-50"]
PHASE_LABELS_DEC = ["1-15", "16-30", "31-50", "51-75", "76+"]

PLAYER_INNS_LABELS = ["0-10", "11-20", "21-50", "51-100", "101+"]


def phase_50(over_no: int) -> str:
    """Bucket an `over_no` (0-based) into a 10-over-block phase label."""
    o = (over_no or 0) + 1     # convert 0-based to 1-based over count
    if o <= 10:  return "1-10"
    if o <= 20:  return "11-20"
    if o <= 30:  return "21-30"
    if o <= 40:  return "31-40"
    return "41-50"


def player_inns_bucket(legal_faced_so_far: int) -> str:
    """
    `legal_faced_so_far` = number of legal balls the player has faced
    in this innings BEFORE this delivery (so the very first ball of an
    innings has legal_faced_so_far == 0 and lands in '0-10').
    """
    n = legal_faced_so_far
    if n < 10:    return "0-10"
    if n < 20:    return "11-20"
    if n < 50:    return "21-50"
    if n < 100:   return "51-100"
    return "101+"


# -------------------------------------------------------- core aggregator --

def _empty_stats() -> dict:
    return {
        "balls":        0,   # physical deliveries (incl extras)
        "legal":        0,
        "runs_bat":     0,
        "runs_extra":   0,
        "wickets":      0,
        "dots":         0,   # legal balls with 0 off the bat AND no extras
        "fours":        0,
        "sixes":        0,
        "innings":      set(),  # (match_id, innings_seq) pairs
    }


def _accumulate(stats: dict, b: dict) -> None:
    stats["balls"] += 1
    legal = int(b.get("is_legal_ball") or 0)
    runs_bat = int(b.get("runs_bat") or 0)
    runs_extra = int(b.get("runs_extra") or 0)
    stats["runs_bat"] += runs_bat
    stats["runs_extra"] += runs_extra
    if legal:
        stats["legal"] += 1
        if runs_bat == 0 and runs_extra == 0:
            stats["dots"] += 1
    if runs_bat == 4:
        stats["fours"] += 1
    elif runs_bat == 6:
        stats["sixes"] += 1
    if int(b.get("wicket") or 0):
        stats["wickets"] += 1
    mid = b.get("match_id")
    iseq = b.get("innings_seq")
    if mid is not None and iseq is not None:
        stats["innings"].add((mid, iseq))


def _finalise(stats: dict) -> dict:
    """Convert raw counts into derived rates. Innings set -> count."""
    s = dict(stats)
    s["innings"] = len(s["innings"])
    runs_total = s["runs_bat"] + s["runs_extra"]
    legal = s["legal"] or 0
    balls = s["balls"] or 0
    s["runs"] = runs_total
    s["sr"]   = (s["runs_bat"] / legal * 100) if legal else None
    s["econ"] = (runs_total / legal * 6) if legal else None
    s["dot_pct"] = (s["dots"] / legal * 100) if legal else None
    s["bowl_avg"] = (runs_total / s["wickets"]) if s["wickets"] else None
    s["bowl_sr"]  = (legal / s["wickets"]) if s["wickets"] else None
    s["bat_avg"]  = (s["runs_bat"] / s["wickets"]) if s["wickets"] else None
    return s


# -------------------------------------------------------- bucket APIs --

def by_phase(balls: Iterable[dict]) -> dict[str, dict]:
    """Group balls into team-innings phases (over-bucket) and aggregate."""
    out = {label: _empty_stats() for label in PHASE_LABELS_50}
    for b in balls:
        _accumulate(out[phase_50(b.get("over_no", 0))], b)
    return {k: _finalise(v) for k, v in out.items()}


def by_player_innings(balls_faced: Iterable[dict]) -> dict[str, dict]:
    """
    Group a single player's faced balls by how-many-balls-deep into
    the innings the delivery happened. Caller must pass balls
    pre-sorted so that they appear in the order the player faced
    them within each innings.

    The function tracks `legal_faced_so_far` per `(match_id,
    innings_seq)`, so it's safe to pass balls from multiple innings.
    """
    out = {label: _empty_stats() for label in PLAYER_INNS_LABELS}
    counters: dict[tuple, int] = {}
    for b in balls_faced:
        key = (b.get("match_id"), b.get("innings_seq"))
        legal_so_far = counters.get(key, 0)
        bucket = player_inns_bucket(legal_so_far)
        _accumulate(out[bucket], b)
        if int(b.get("is_legal_ball") or 0):
            counters[key] = legal_so_far + 1
    return {k: _finalise(v) for k, v in out.items()}


def by_batting_hand(balls_bowled: Iterable[dict]) -> dict[str, dict]:
    """
    For a bowler: split their deliveries by the batter's
    batting_hand metadata (snapshotted at build time onto each ball
    record).
    """
    out = {"right": _empty_stats(),
           "left":  _empty_stats(),
           "unknown": _empty_stats()}
    for b in balls_bowled:
        h = b.get("batting_hand") or "unknown"
        if h not in out:
            h = "unknown"
        _accumulate(out[h], b)
    return {k: _finalise(v) for k, v in out.items()}


def by_bowler_type(balls_faced: Iterable[dict]) -> dict[str, dict[str, dict]]:
    """
    For a batter: split their faced balls by opposition-bowler
    metadata. Returns nested dict:
      { 'type':   {'pace': stats, 'spin': stats, 'unknown': stats},
        'arm':    {'right': stats, 'left': stats, 'unknown': stats},
        'flavour':{'fast': ..., 'medium': ..., 'slow': ...,
                   'wrist': ..., 'finger': ..., 'unknown': ...} }
    """
    type_buckets   = {"pace": _empty_stats(), "spin": _empty_stats(),
                      "unknown": _empty_stats()}
    arm_buckets    = {"right": _empty_stats(), "left": _empty_stats(),
                      "unknown": _empty_stats()}
    flavour_keys   = ("fast", "medium", "slow", "wrist", "finger", "unknown")
    flav_buckets   = {k: _empty_stats() for k in flavour_keys}

    for b in balls_faced:
        t = b.get("bowling_type") or "unknown"
        if t not in type_buckets:
            t = "unknown"
        _accumulate(type_buckets[t], b)

        a = b.get("bowling_arm") or "unknown"
        if a not in arm_buckets:
            a = "unknown"
        _accumulate(arm_buckets[a], b)

        if t == "pace":
            f = b.get("pace_type") or "unknown"
        elif t == "spin":
            f = b.get("spin_type") or "unknown"
        else:
            f = "unknown"
        if f not in flav_buckets:
            f = "unknown"
        _accumulate(flav_buckets[f], b)

    return {
        "type":    {k: _finalise(v) for k, v in type_buckets.items()},
        "arm":     {k: _finalise(v) for k, v in arm_buckets.items()},
        "flavour": {k: _finalise(v) for k, v in flav_buckets.items()},
    }


# -------------------------------------------------------- spell detection --

def detect_spells(balls_bowled: Iterable[dict],
                  *, gap_threshold: int = 3) -> list[dict]:
    """
    Walk a single bowler's deliveries (across many innings) and label
    each one with a spell_index (1-based) within its innings.

    Standard cricket convention: a bowler's "spell" is a contiguous
    run of overs at the same end. Bowlers usually alternate ends, so
    overs 1, 3, 5, 7 are one spell. A break of more than `gap_threshold`
    overs between two of their deliveries breaks the spell.

    Returns a list of spell dicts keyed by (match_id, innings_seq,
    spell_index): each dict holds the aggregated stats plus
    `first_over` / `last_over` for context. Spells are renumbered 1..N
    per innings.

    Default `gap_threshold = 3` means: if previous over was N and the
    next is > N + 2 (i.e. gap of 3 overs or more), that's a new spell.
    Standard alternating pattern (gap of 2) keeps the same spell.
    """
    by_inn: dict[tuple, list[dict]] = {}
    for b in balls_bowled:
        key = (b.get("match_id"), b.get("innings_seq"))
        by_inn.setdefault(key, []).append(b)

    spells: list[dict] = []
    for (mid, iseq), inn_balls in by_inn.items():
        inn_balls.sort(key=lambda b: ((b.get("over_no") or 0),
                                       (b.get("ball_no") or 0)))
        spell_index = 0
        cur: dict | None = None
        last_over: int | None = None
        for b in inn_balls:
            o = b.get("over_no") or 0
            if cur is None or (last_over is not None and o > last_over + gap_threshold - 1):
                # finalise previous
                if cur is not None:
                    spells.append(_finalise_spell(cur))
                spell_index += 1
                cur = {
                    "match_id":    mid,
                    "innings_seq": iseq,
                    "spell_index": spell_index,
                    "first_over":  o,
                    "last_over":   o,
                    "stats":       _empty_stats(),
                }
            _accumulate(cur["stats"], b)
            cur["last_over"] = o
            last_over = o
        if cur is not None:
            spells.append(_finalise_spell(cur))
    return spells


def _finalise_spell(spell: dict) -> dict:
    out = dict(spell)
    out["stats"] = _finalise(out["stats"])
    return out


def aggregate_spells(spells: list[dict]) -> dict[str, dict]:
    """
    Roll a list of per-innings spells into "1st spell" vs "2nd+
    spells" buckets across the player's career.
    """
    first  = _empty_stats()
    later  = _empty_stats()
    for sp in spells:
        target = first if sp["spell_index"] == 1 else later
        # We can't re-_accumulate finalised stats — so sum the raw
        # counts back. Use the raw totals embedded in the finalised
        # stats dict.
        s = sp["stats"]
        target["balls"]      += s["balls"]
        target["legal"]      += s["legal"]
        target["runs_bat"]   += s["runs_bat"]
        target["runs_extra"] += s["runs_extra"]
        target["wickets"]    += s["wickets"]
        target["dots"]       += s["dots"]
        target["fours"]      += s["fours"]
        target["sixes"]      += s["sixes"]
        target["innings"].add((sp["match_id"], sp["innings_seq"]))
    return {"1st": _finalise(first), "later": _finalise(later)}


# -------------------------------------------------------- self-tests --

if __name__ == "__main__":
    # Quick sanity-check the spell detector with the docstring example:
    # overs 1,3,5,7,18,20,22,24 -> 2 spells, first one of 4 overs.
    fake = []
    for over_idx in (0, 2, 4, 6, 17, 19, 21, 23):
        fake.append({
            "match_id": 1, "innings_seq": 1,
            "over_no": over_idx, "ball_no": 1,
            "is_legal_ball": 1, "runs_bat": 0, "runs_extra": 0,
            "wicket": 0,
        })
    sp = detect_spells(fake)
    assert len(sp) == 2, sp
    assert sp[0]["spell_index"] == 1
    assert sp[0]["first_over"] == 0 and sp[0]["last_over"] == 6
    assert sp[1]["spell_index"] == 2
    assert sp[1]["first_over"] == 17 and sp[1]["last_over"] == 23
    print("OK — spell detector recovered the docstring example")
    print("   spell 1:", sp[0]["first_over"], "→", sp[0]["last_over"])
    print("   spell 2:", sp[1]["first_over"], "→", sp[1]["last_over"])

    agg = aggregate_spells(sp)
    print("aggregate:", agg["1st"]["balls"], "balls in 1st spell,",
          agg["later"]["balls"], "in later spells")
