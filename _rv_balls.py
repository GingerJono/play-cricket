"""
Translate ResultsVault `player_id` values to Play-Cricket `player_id`s.

RV's per-ball stream is rich and unambiguous — every ball carries
numeric `batter_id`, `bowler_id`, `dismissed_batter_id` etc. The only
problem is they're in **RV's own player-id namespace** (11M-range
integers), distinct from Play-Cricket's (4M-range). To make those IDs
useful for joining against `match_players`, `metadata/players/...`
and the data-repo coverage queries, we translate at load time.

Where the mapping comes from
----------------------------
RV's `MatchTeams[].TeamMembers[]` is a roster record with both:
  * `player_id`     — RV-internal numeric (11M-range)
  * `player_name`, `player_name2`, `player_name3` — three formats
    (`"Last, First"`, `"First Last"`, `"F Last"`) for the same person

Play-Cricket's `match_players` row carries the PC `player_id` and
`player_name`. The two roster lists are the same 11+11 people; we
match by name and pair up the IDs once per match.

Why roster-level matching is the right place
--------------------------------------------
Per-ball name parsing (an earlier attempt) hit ambiguity whenever a
team had two "S Patel"s. The roster has full names and ID numbers, so
we can disambiguate cleanly there. Per-ball lookup then just becomes
`map[rv_pid]` — no string fuzziness.
"""

from __future__ import annotations


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return s.replace("†", "").replace("*", "").strip().lower()


def build_rv_to_pc_map(
    rv_team_members: list[dict],
    pc_match_players: list[dict],
) -> dict[int, int]:
    """
    Build {rv_player_id -> pc_player_id} for one match.

    Matches each RV team member to a PC match_players row by name, with
    progressively looser fallbacks. Skips RV members whose name is
    ambiguous on the PC side (multiple PC rows match the same key) so
    we never assign the wrong PC id.
    """
    # Index PC players by various name keys.
    pc_by_full: dict[str, list[int]] = {}
    pc_by_lastfirst: dict[str, list[int]] = {}
    pc_by_initial_last: dict[tuple, list[int]] = {}
    for p in pc_match_players or []:
        pid = p.get("player_id")
        if pid is None:
            continue
        full = _norm(p.get("player_name"))
        if not full:
            continue
        pc_by_full.setdefault(full, []).append(pid)
        parts = full.split()
        if len(parts) >= 2:
            last = parts[-1]
            first = parts[0]
            # "Last, First" form
            pc_by_lastfirst.setdefault(f"{last}, {first}", []).append(pid)
            # "F Last" form
            pc_by_initial_last.setdefault((first[0], last), []).append(pid)

    out: dict[int, int] = {}
    for m in rv_team_members or []:
        rv_pid = m.get("rv_player_id")
        if rv_pid is None:
            continue
        # Try each name field in turn.
        candidates = []
        for key_form, idx in (
            (_norm(m.get("player_name2")), pc_by_full),
            (_norm(m.get("player_name")),  pc_by_lastfirst),
        ):
            if key_form and key_form in idx and len(idx[key_form]) == 1:
                candidates.append(idx[key_form][0]); break
        if not candidates:
            # "F Last" via player_name3
            n3 = _norm(m.get("player_name3"))
            parts = n3.split()
            if len(parts) >= 2 and len(parts[0]) <= 2:
                key = (parts[0][0], parts[-1])
                if key in pc_by_initial_last and len(pc_by_initial_last[key]) == 1:
                    candidates.append(pc_by_initial_last[key][0])
        if not candidates:
            # Last name only — only if uniquely identified on PC side
            for n in (m.get("player_name2"), m.get("player_name"), m.get("l_name")):
                last = _norm(n).split()[-1] if _norm(n) else ""
                pc_match = [pid for k, lst in pc_by_full.items()
                                 if k.split()[-1] == last for pid in lst]
                if len(set(pc_match)) == 1:
                    candidates.append(pc_match[0]); break
        if candidates:
            out[int(rv_pid)] = int(candidates[0])
    return out
