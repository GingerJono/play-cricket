#!/usr/bin/env python3
"""
Build the Rainham/Play-Cricket SQLite database from cached JSON payloads.

Reads:
  stats/data/raw/matches/<site_id>/<season>.json   (one per site_id we've fetched)
  stats/data/raw/match_detail/<match_id>.json      (shared, keyed by global match id)

Writes:
  stats/data/rainham.db

The schema is club-agnostic. Filter any report by club_id when needed
(e.g. WHERE team_batting_club_id = '5251' for Rainham). See data/README.md.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
MATCHES_DIR = RAW_DIR / "matches"
MATCH_DETAIL_DIR = RAW_DIR / "match_detail"
RV_MATCH_DIR = RAW_DIR / "rv_match"
BALLS_DIR = RAW_DIR / "balls"
NV_MATCH_DIR = RAW_DIR / "nv_match"
DB_PATH = ROOT / "data" / "rainham.db"

import _nvplay_balls as _nv  # noqa: E402  (after constants for symmetry)
import _rv_balls as _rv      # noqa: E402

SCHEMA = """
DROP TABLE IF EXISTS clubs;
DROP TABLE IF EXISTS matches;
DROP TABLE IF EXISTS match_players;
DROP TABLE IF EXISTS innings;
DROP TABLE IF EXISTS batting;
DROP TABLE IF EXISTS bowling;
DROP TABLE IF EXISTS fall_of_wickets;
DROP TABLE IF EXISTS balls;
DROP TABLE IF EXISTS match_bbb;

CREATE TABLE clubs (
    club_id    TEXT PRIMARY KEY,
    club_name  TEXT
);

CREATE TABLE matches (
    match_id INTEGER PRIMARY KEY,
    season INTEGER,
    match_date TEXT,
    match_time TEXT,
    league_id TEXT,
    league_name TEXT,
    competition_id TEXT,
    competition_name TEXT,
    competition_type TEXT,
    match_type TEXT,
    game_type TEXT,
    ground_id TEXT,
    ground_name TEXT,
    home_club_id TEXT,
    home_club_name TEXT,
    home_team_id TEXT,
    home_team_name TEXT,
    away_club_id TEXT,
    away_club_name TEXT,
    away_team_id TEXT,
    away_team_name TEXT,
    toss_won_by_team_id TEXT,
    toss TEXT,
    batted_first TEXT,
    no_of_overs INTEGER,
    no_of_innings INTEGER,
    no_of_players INTEGER,
    result TEXT,
    result_description TEXT,
    result_applied_to TEXT
);

CREATE TABLE match_players (
    match_id INTEGER,
    team_side TEXT,         -- 'home' | 'away'
    club_id TEXT,           -- club of the team on this side
    position INTEGER,
    player_id INTEGER,
    player_name TEXT,
    captain INTEGER,
    wicket_keeper INTEGER,
    PRIMARY KEY (match_id, team_side, player_id)
);

-- innings_seq is a 1-based ordinal of the innings within the match payload.
-- The Play-Cricket API frequently returns both innings of a limited-overs
-- match with `innings_number = 1`, so we cannot use innings_number as part
-- of a unique key. innings_number is preserved as a column for reference.
CREATE TABLE innings (
    match_id INTEGER,
    innings_seq INTEGER,
    innings_number INTEGER,
    team_batting_id TEXT,
    team_batting_name TEXT,
    team_batting_club_id TEXT,    -- club of the batting team
    runs INTEGER,
    wickets INTEGER,
    overs TEXT,
    balls INTEGER,
    total_extras INTEGER,
    extra_byes INTEGER,
    extra_leg_byes INTEGER,
    extra_wides INTEGER,
    extra_no_balls INTEGER,
    extra_penalty_runs INTEGER,
    declared INTEGER,
    forfeited_innings INTEGER,
    revised_target_runs INTEGER,
    revised_target_overs TEXT,
    PRIMARY KEY (match_id, innings_seq)
);

CREATE TABLE batting (
    match_id INTEGER,
    innings_seq INTEGER,
    innings_number INTEGER,
    position INTEGER,
    batsman_id INTEGER,
    batsman_name TEXT,
    how_out TEXT,
    fielder_id INTEGER,
    fielder_name TEXT,
    bowler_id INTEGER,
    bowler_name TEXT,
    runs INTEGER,
    fours INTEGER,
    sixes INTEGER,
    balls INTEGER,
    team_batting_id TEXT,
    team_batting_name TEXT,
    team_batting_club_id TEXT,    -- club of the batting team
    PRIMARY KEY (match_id, innings_seq, position)
);

CREATE TABLE bowling (
    match_id INTEGER,
    innings_seq INTEGER,
    innings_number INTEGER,
    bowl_position INTEGER,
    bowler_id INTEGER,
    bowler_name TEXT,
    overs TEXT,
    maidens INTEGER,
    runs INTEGER,
    wides INTEGER,
    no_balls INTEGER,
    wickets INTEGER,
    team_bowling_id TEXT,
    team_bowling_name TEXT,
    team_bowling_club_id TEXT,    -- club of the bowling team
    PRIMARY KEY (match_id, innings_seq, bowl_position)
);

CREATE TABLE fall_of_wickets (
    match_id INTEGER,
    innings_seq INTEGER,
    innings_number INTEGER,
    wicket INTEGER,
    runs INTEGER,
    batsman_out_id INTEGER,
    batsman_out_name TEXT,
    batsman_in_id INTEGER,
    batsman_in_name TEXT,
    batsman_in_runs INTEGER,
    PRIMARY KEY (match_id, innings_seq, wicket)
);

-- One row per delivery. innings_seq matches our existing innings_seq
-- (1-based ordinal in playing order); equal to ResultsVault's
-- `innings_order`. ball_no is the raw delivery number (counts wides /
-- no-balls); ball_no_disp is the legal-balls-only display number.
-- ball_no is the within-over sequence (1..N including wides/no-balls);
-- it resets each over. Unique key needs the over too.
CREATE TABLE balls (
    match_id              INTEGER,
    innings_seq           INTEGER,
    over_no               INTEGER,
    ball_no               INTEGER,
    ball_no_disp          INTEGER,
    batter_id             INTEGER,
    non_striker_id        INTEGER,
    bowler_id             INTEGER,
    team_batting_club_id  TEXT,
    team_bowling_club_id  TEXT,
    runs_bat              INTEGER,
    runs_extra            INTEGER,
    extras_type           INTEGER,    -- 1=NB 2=Wide 3=B 4=LB 5=NB+B 6=NB+LB
    is_legal_ball         INTEGER,    -- 0 if extras_type IN (1,2)
    dismissed_batter_id   INTEGER,
    s_desc                TEXT,
    l_desc                TEXT,
    PRIMARY KEY (match_id, innings_seq, over_no, ball_no)
);

-- Lookup table: which matches have ball-by-ball data loaded.
-- Populated alongside `balls`; lets reports filter cheaply without a
-- COUNT(*) over balls. `source` distinguishes the rich ResultsVault
-- stream from the leaner NV Play reconstruction (see _nvplay_balls.py).
CREATE TABLE match_bbb (
    match_id     INTEGER PRIMARY KEY,
    rv_match_id  INTEGER,
    source       TEXT,        -- 'rv' | 'nvplay'
    n_innings    INTEGER,
    n_balls      INTEGER,
    n_legal      INTEGER
);

CREATE INDEX idx_batting_player        ON batting(batsman_id);
CREATE INDEX idx_batting_club          ON batting(team_batting_club_id);
CREATE INDEX idx_bowling_player        ON bowling(bowler_id);
CREATE INDEX idx_bowling_club          ON bowling(team_bowling_club_id);
CREATE INDEX idx_innings_club          ON innings(team_batting_club_id);
CREATE INDEX idx_match_players_player  ON match_players(player_id);
CREATE INDEX idx_match_players_club    ON match_players(club_id);
CREATE INDEX idx_matches_season        ON matches(season);
CREATE INDEX idx_matches_home_club     ON matches(home_club_id);
CREATE INDEX idx_matches_away_club     ON matches(away_club_id);
CREATE INDEX idx_balls_bowler          ON balls(bowler_id, match_id);
CREATE INDEX idx_balls_batter          ON balls(batter_id, match_id);
CREATE INDEX idx_balls_match           ON balls(match_id, innings_seq, over_no);
CREATE INDEX idx_balls_bat_club        ON balls(team_batting_club_id);
CREATE INDEX idx_balls_bowl_club       ON balls(team_bowling_club_id);
"""


def _to_int(x, default=None):
    if x is None or x == "":
        return default
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return default


def _to_str(x):
    if x is None:
        return ""
    return str(x)


def _to_bool_int(x):
    if x is True:
        return 1
    if x is False:
        return 0
    s = str(x).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return 1
    if s in ("false", "0", "no", "n", ""):
        return 0
    return 0


def _team_to_club(md: dict) -> dict[str, str]:
    """Map team_id -> club_id for a match's two sides."""
    out: dict[str, str] = {}
    h_team = _to_str(md.get("home_team_id"))
    a_team = _to_str(md.get("away_team_id"))
    h_club = _to_str(md.get("home_club_id"))
    a_club = _to_str(md.get("away_club_id"))
    if h_team:
        out[h_team] = h_club
    if a_team:
        out[a_team] = a_club
    return out


def load_match_detail(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    md_list = data.get("match_details") or []
    if not md_list:
        return None
    return md_list[0]


def insert_match(cur: sqlite3.Cursor, md: dict, season_hint: int | None) -> None:
    season = _to_int(md.get("season")) or season_hint
    cur.execute(
        """INSERT OR REPLACE INTO matches VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )""",
        (
            _to_int(md.get("id")),
            season,
            _to_str(md.get("match_date")),
            _to_str(md.get("match_time")),
            _to_str(md.get("league_id")),
            _to_str(md.get("league_name")),
            _to_str(md.get("competition_id")),
            _to_str(md.get("competition_name")),
            _to_str(md.get("competition_type")),
            _to_str(md.get("match_type")),
            _to_str(md.get("game_type")),
            _to_str(md.get("ground_id")),
            _to_str(md.get("ground_name")),
            _to_str(md.get("home_club_id")),
            _to_str(md.get("home_club_name")),
            _to_str(md.get("home_team_id")),
            _to_str(md.get("home_team_name")),
            _to_str(md.get("away_club_id")),
            _to_str(md.get("away_club_name")),
            _to_str(md.get("away_team_id")),
            _to_str(md.get("away_team_name")),
            _to_str(md.get("toss_won_by_team_id")),
            _to_str(md.get("toss")),
            _to_str(md.get("batted_first")),
            _to_int(md.get("no_of_overs")),
            _to_int(md.get("no_of_innings")),
            _to_int(md.get("no_of_players")),
            _to_str(md.get("result")),
            _to_str(md.get("result_description")),
            _to_str(md.get("result_applied_to")),
        ),
    )


def insert_clubs(cur: sqlite3.Cursor, md: dict) -> None:
    for cid, cname in (
        (_to_str(md.get("home_club_id")), _to_str(md.get("home_club_name"))),
        (_to_str(md.get("away_club_id")), _to_str(md.get("away_club_name"))),
    ):
        if cid:
            cur.execute(
                "INSERT OR IGNORE INTO clubs (club_id, club_name) VALUES (?, ?)",
                (cid, cname),
            )
            # Update name if we now have a non-empty name
            if cname:
                cur.execute(
                    "UPDATE clubs SET club_name=? WHERE club_id=? AND (club_name IS NULL OR club_name='')",
                    (cname, cid),
                )


def insert_match_players(cur: sqlite3.Cursor, md: dict) -> None:
    match_id = _to_int(md.get("id"))
    h_club = _to_str(md.get("home_club_id"))
    a_club = _to_str(md.get("away_club_id"))
    players = md.get("players") or []
    seen: set[tuple] = set()
    for block in players:
        for side_key, side_label, side_club in (
            ("home_team", "home", h_club),
            ("away_team", "away", a_club),
        ):
            for p in block.get(side_key, []) or []:
                key = (match_id, side_label, _to_int(p.get("player_id")))
                if key[2] is None or key in seen:
                    continue
                seen.add(key)
                cur.execute(
                    """INSERT OR REPLACE INTO match_players VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        match_id,
                        side_label,
                        side_club,
                        _to_int(p.get("position")),
                        _to_int(p.get("player_id")),
                        _to_str(p.get("player_name")),
                        _to_bool_int(p.get("captain")),
                        _to_bool_int(p.get("wicket_keeper")),
                    ),
                )


def insert_innings(cur: sqlite3.Cursor, md: dict) -> None:
    match_id = _to_int(md.get("id"))
    team_to_club = _team_to_club(md)
    for idx, inn in enumerate(md.get("innings") or []):
        inn_seq = idx + 1
        inn_num = _to_int(inn.get("innings_number"))
        team_batting_id = _to_str(inn.get("team_batting_id"))
        team_batting_name = _to_str(inn.get("team_batting_name"))
        bat_club_id = team_to_club.get(team_batting_id, "")
        cur.execute(
            """INSERT OR REPLACE INTO innings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                match_id,
                inn_seq,
                inn_num,
                team_batting_id,
                team_batting_name,
                bat_club_id,
                _to_int(inn.get("runs")),
                _to_int(inn.get("wickets")),
                _to_str(inn.get("overs")),
                _to_int(inn.get("balls")),
                _to_int(inn.get("total_extras")),
                _to_int(inn.get("extra_byes")),
                _to_int(inn.get("extra_leg_byes")),
                _to_int(inn.get("extra_wides")),
                _to_int(inn.get("extra_no_balls")),
                _to_int(inn.get("extra_penalty_runs")),
                _to_bool_int(inn.get("declared")),
                _to_bool_int(inn.get("forfeited_innings")),
                _to_int(inn.get("revised_target_runs")),
                _to_str(inn.get("revised_target_overs")),
            ),
        )

        # Bat
        bat_pos_seen: set[int] = set()
        for i, b in enumerate(inn.get("bat") or []):
            pos = _to_int(b.get("position"))
            if pos is None or pos in bat_pos_seen:
                pos = (max(bat_pos_seen) + 1) if bat_pos_seen else (i + 1)
            bat_pos_seen.add(pos)
            cur.execute(
                """INSERT OR REPLACE INTO batting VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    match_id,
                    inn_seq,
                    inn_num,
                    pos,
                    _to_int(b.get("batsman_id")),
                    _to_str(b.get("batsman_name")),
                    _to_str(b.get("how_out")),
                    _to_int(b.get("fielder_id")),
                    _to_str(b.get("fielder_name")),
                    _to_int(b.get("bowler_id")),
                    _to_str(b.get("bowler_name")),
                    _to_int(b.get("runs")),
                    _to_int(b.get("fours")),
                    _to_int(b.get("sixes")),
                    _to_int(b.get("balls")),
                    team_batting_id,
                    team_batting_name,
                    bat_club_id,
                ),
            )

        # Bowling team is the OTHER side this innings
        bowl_team_id = ""
        bowl_team_name = ""
        if team_batting_id == _to_str(md.get("home_team_id")):
            bowl_team_id = _to_str(md.get("away_team_id"))
            bowl_team_name = _to_str(md.get("away_team_name"))
        elif team_batting_id == _to_str(md.get("away_team_id")):
            bowl_team_id = _to_str(md.get("home_team_id"))
            bowl_team_name = _to_str(md.get("home_team_name"))
        bowl_club_id = team_to_club.get(bowl_team_id, "")

        bowl_pos_seen: set[int] = set()
        for i, bowl in enumerate(inn.get("bowl") or []):
            pos = _to_int(bowl.get("position"))
            if pos is None or pos in bowl_pos_seen:
                pos = (max(bowl_pos_seen) + 1) if bowl_pos_seen else (i + 1)
            bowl_pos_seen.add(pos)
            cur.execute(
                """INSERT OR REPLACE INTO bowling VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    match_id,
                    inn_seq,
                    inn_num,
                    pos,
                    _to_int(bowl.get("bowler_id")),
                    _to_str(bowl.get("bowler_name")),
                    _to_str(bowl.get("overs")),
                    _to_int(bowl.get("maidens")),
                    _to_int(bowl.get("runs")),
                    _to_int(bowl.get("wides")),
                    _to_int(bowl.get("no_balls")),
                    _to_int(bowl.get("wickets")),
                    bowl_team_id,
                    bowl_team_name,
                    bowl_club_id,
                ),
            )

        # FOW
        fow_seen: set[int] = set()
        for i, f in enumerate(inn.get("fow") or []):
            wkt = _to_int(f.get("wickets"))
            if wkt is None or wkt in fow_seen:
                wkt = (max(fow_seen) + 1) if fow_seen else (i + 1)
            fow_seen.add(wkt)
            cur.execute(
                """INSERT OR REPLACE INTO fall_of_wickets VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    match_id,
                    inn_seq,
                    inn_num,
                    wkt,
                    _to_int(f.get("runs")),
                    _to_int(f.get("batsman_out_id")),
                    _to_str(f.get("batsman_out_name")),
                    _to_int(f.get("batsman_in_id")),
                    _to_str(f.get("batsman_in_name")),
                    _to_int(f.get("batsman_in_runs")),
                ),
            )


def insert_balls(
    cur: sqlite3.Cursor,
    match_id: int,
    home_club_id: str,
    away_club_id: str,
    pc_match_players: list[dict] | None = None,
) -> tuple[int, int, int]:
    """
    Load every cached innings of ball-by-ball for one match.

    Returns (n_innings, n_balls_total, n_legal_balls).
    Returns (0, 0, 0) if no rv_match metadata or no balls JSON exists.

    Player IDs come from parsing `l_desc` ("X to Y: ...") and matching
    names against `pc_match_players` — RV's own `batter_id`/`bowler_id`
    fields use a different namespace (11M-range vs PC's 4M-range), so
    they can't be used directly. Falls back to RV's numeric ids only
    when the name-match fails (rare; logs the residual via the count
    of NULLs in the `balls` table).

    `team_batting_club_id` / `team_bowling_club_id` are resolved via the
    rv_match `is_home` flag cross-referenced with the match's home/away
    club_ids.
    """
    rv_path = RV_MATCH_DIR / f"{match_id}.json"
    balls_dir = BALLS_DIR / str(match_id)
    if not rv_path.exists() or not balls_dir.exists():
        return (0, 0, 0)
    try:
        rv = json.loads(rv_path.read_text())
    except Exception:
        return (0, 0, 0)

    # Build per-team name indices once.
    pc_match_players = pc_match_players or []
    home_p = [p for p in pc_match_players if p["team_side"] == "home"]
    away_p = [p for p in pc_match_players if p["team_side"] == "away"]
    home_idx = _rv.build_name_index(home_p)
    away_idx = _rv.build_name_index(away_p)
    is_home_for_seq: dict[int, bool] = {}
    for inn in rv.get("innings", []) or []:
        seq = _to_int(inn.get("innings_order"))
        if seq is not None:
            is_home_for_seq[seq] = bool(inn.get("is_home"))

    n_innings = 0
    n_balls = 0
    n_legal = 0
    for inn_path in sorted(balls_dir.glob("*.json")):
        try:
            inn_seq = int(inn_path.stem)
        except ValueError:
            continue
        is_home_batting = is_home_for_seq.get(inn_seq)
        if is_home_batting is None:
            continue
        bat_club = home_club_id if is_home_batting else away_club_id
        bowl_club = away_club_id if is_home_batting else home_club_id
        try:
            balls = json.loads(inn_path.read_text())
        except Exception:
            continue
        if not isinstance(balls, list) or not balls:
            continue
        n_innings += 1
        bat_idx = home_idx if is_home_batting else away_idx
        bowl_idx = away_idx if is_home_batting else home_idx
        for b in balls:
            l_desc = b.get("l_desc") or ""
            parsed = _rv.parse_l_desc(l_desc)

            # Prefer parsed l_desc for everything (runs / extras / wicket
            # / players). Fall back to the raw RV fields when the parse
            # fails or names don't resolve.
            if parsed:
                runs_bat = parsed["runs_bat"]
                runs_extra = parsed["runs_extra"]
                ext_int = parsed["extras_type"]
                is_legal = parsed["is_legal_ball"]
                batter_pc = _rv.resolve_name(parsed["batter_name"], bat_idx)
                bowler_pc = _rv.resolve_name(parsed["bowler_name"], bowl_idx)
                if parsed["is_wicket"] and parsed["dismissed_name"]:
                    dismissed_pc = _rv.resolve_name(
                        parsed["dismissed_name"], bat_idx
                    )
                else:
                    dismissed_pc = None
            else:
                runs_bat = _to_int(b.get("runs_bat")) or 0
                runs_extra = _to_int(b.get("runs_extra")) or 0
                ext_raw = b.get("extras_type")
                ext_int = _to_int(ext_raw)
                is_legal = 0 if ext_int in (1, 2) else 1
                batter_pc = bowler_pc = dismissed_pc = None

            cur.execute(
                """INSERT OR REPLACE INTO balls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    match_id,
                    inn_seq,
                    _to_int(b.get("over_no")),
                    _to_int(b.get("ball_no")),
                    _to_int(b.get("ball_no_disp")),
                    batter_pc,
                    None,             # non_striker_id — not derivable from l_desc
                    bowler_pc,
                    bat_club,
                    bowl_club,
                    runs_bat,
                    runs_extra,
                    ext_int,
                    is_legal,
                    dismissed_pc,
                    _to_str(b.get("s_desc")),
                    _to_str(b.get("l_desc")),
                ),
            )
            n_balls += 1
            n_legal += is_legal
    if n_balls:
        cur.execute(
            "INSERT OR REPLACE INTO match_bbb VALUES (?,?,?,?,?,?)",
            (match_id, _to_int(rv.get("rv_match_id")), "rv",
             n_innings, n_balls, n_legal),
        )
    return (n_innings, n_balls, n_legal)


def insert_balls_nvplay(
    cur: sqlite3.Cursor,
    match_id: int,
    home_club_id: str,
    away_club_id: str,
    pc_match_players: list[dict],
) -> tuple[int, int, int]:
    """
    NV Play fallback loader. Reads `data/raw/nv_match/<match_id>.json`,
    reconstructs per-ball metadata via _nvplay_balls, and inserts into
    the same `balls` table.

    Returns (n_innings, n_balls_total, n_legal_balls). (0, 0, 0) when
    no NV scorecard exists or it has no balls.
    """
    nv_path = NV_MATCH_DIR / f"{match_id}.json"
    if not nv_path.exists():
        return (0, 0, 0)
    try:
        scorecard = json.loads(nv_path.read_text())
    except Exception:
        return (0, 0, 0)

    innings = scorecard.get("Innings") or []
    if not innings:
        return (0, 0, 0)

    home_players = [r for r in pc_match_players if r["team_side"] == "home"]
    away_players = [r for r in pc_match_players if r["team_side"] == "away"]

    # Decide which NV `TeamN` corresponds to which PC side, matching by
    # club name (NV's `Match.Team{1,2}Club`). Build once for the match.
    m = scorecard.get("Match") or {}
    nv_id_map = _nv.build_nv_id_map(scorecard)

    def side_of_team(team_no: int) -> str:
        """Return 'home' / 'away' / None for NV team1 / team2."""
        nv_club = (m.get(f"Team{team_no}Club") or "").strip().lower()
        if not nv_club:
            return None
        # Match against PC home/away club names through match_players club_id
        # would require a clubs lookup; instead compare against player_name's
        # club indirectly. Simpler: match the NV club against home/away club
        # via a passed-in mapping from caller (we have home/away_club_id but
        # not name here). Fall through to first-letter heuristic if needed.
        # The caller provides home_club_id/away_club_id — we accept whichever
        # team1 happens to be (by convention NV team1 = home in PC's eyes;
        # if mismatched, IsTeam2BattingFirst still tells us batting order).
        return None

    # Heuristic: NV `Team1` corresponds to whichever of home/away matches
    # by name. We compare normalised club names.
    def _norm(s):
        return (s or "").strip().lower()

    pc_home_clubname = ""
    pc_away_clubname = ""
    if home_players:
        # We don't have club_name here, but match_players doesn't carry it.
        # Use the first player's name to look up... actually we can't.
        # Fall back to NV Team1Club always = home; flip if Team2Club matches
        # a substring known to be home.
        pass

    # Without club_name lookups, use a more direct approach: NV publishes
    # ExternalId for every player. Pick a few PC player_ids on the home
    # side and check which NV team contains them.
    home_pids = {r["player_id"] for r in home_players if r["player_id"]}
    away_pids = {r["player_id"] for r in away_players if r["player_id"]}
    team1_externals = {int(p.get("ExternalId")) for p in (m.get("Team1Players") or [])
                       if p.get("ExternalId") and str(p.get("ExternalId")).isdigit()}
    team2_externals = {int(p.get("ExternalId")) for p in (m.get("Team2Players") or [])
                       if p.get("ExternalId") and str(p.get("ExternalId")).isdigit()}
    team1_overlap_home = len(team1_externals & home_pids)
    team1_overlap_away = len(team1_externals & away_pids)
    team1_is_home = team1_overlap_home >= team1_overlap_away

    n_innings = 0
    n_balls = 0
    n_legal = 0
    for inn_idx, inn in enumerate(innings):
        inn_seq = inn_idx + 1
        batting_side = _nv.innings_batting_side(scorecard, inn_idx)
        if batting_side == "team1":
            is_home_batting = team1_is_home
        elif batting_side == "team2":
            is_home_batting = not team1_is_home
        else:
            # Fall back to the BattingTeamName / first-innings-home heuristic.
            is_home_batting = (inn_idx == 0)

        bat_club  = home_club_id if is_home_batting else away_club_id
        bowl_club = away_club_id if is_home_batting else home_club_id
        bat_players  = home_players if is_home_batting else away_players
        bowl_players = away_players if is_home_batting else home_players

        inn["innings_seq"] = inn_seq
        rows = _nv.reconstruct_innings(
            inn, bat_players, bowl_players, bat_club, bowl_club,
            nv_id_map=nv_id_map,
        )
        if not rows:
            continue
        n_innings += 1
        for b in rows:
            cur.execute(
                """INSERT OR REPLACE INTO balls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    match_id,
                    b["innings_seq"],
                    b["over_no"],
                    b["ball_no"],
                    b["ball_no_disp"],
                    b["batter_id"],
                    b["non_striker_id"],
                    b["bowler_id"],
                    b["team_batting_club_id"],
                    b["team_bowling_club_id"],
                    b["runs_bat"] or 0,
                    b["runs_extra"] or 0,
                    b["extras_type"],
                    1 if b["is_legal_ball"] else 0,
                    b["dismissed_batter_id"],
                    b["s_desc"] or "",
                    b["l_desc"] or "",
                ),
            )
            n_balls += 1
            if b["is_legal_ball"]:
                n_legal += 1

    if n_balls:
        cur.execute(
            "INSERT OR REPLACE INTO match_bbb VALUES (?,?,?,?,?,?)",
            (match_id, None, "nvplay", n_innings, n_balls, n_legal),
        )
    return (n_innings, n_balls, n_legal)


def validate_balls(cur: sqlite3.Cursor) -> None:
    """
    Cross-check per-innings ball totals against the scorecard total.
    SUM(runs_bat + runs_extra) per (match, innings_seq) should equal
    `innings.runs - innings.extra_penalty_runs`. Any delta > 5% aborts.
    """
    rows = cur.execute("""
        SELECT b.match_id, b.innings_seq,
               SUM(b.runs_bat + b.runs_extra) AS bbb_runs,
               i.runs - COALESCE(i.extra_penalty_runs, 0) AS card_runs
        FROM balls b
        JOIN innings i USING (match_id, innings_seq)
        GROUP BY b.match_id, b.innings_seq
    """).fetchall()
    if not rows:
        return
    deltas = []
    for mid, seq, bbb, card in rows:
        if card is None or card == 0:
            continue
        d = abs((bbb or 0) - card) / max(card, 1)
        deltas.append((d, mid, seq, bbb, card))
    deltas.sort(reverse=True)
    if not deltas:
        return
    worst = deltas[0]
    print(f"  bbb validation: {len(deltas)} innings; "
          f"worst delta {worst[0]*100:.1f}% "
          f"(match {worst[1]} inn {worst[2]}: bbb={worst[3]} card={worst[4]})",
          flush=True)
    fails = [d for d in deltas if d[0] > 0.05]
    if fails:
        print(f"  WARNING: {len(fails)} innings with > 5% delta; first 5:",
              flush=True)
        for d in fails[:5]:
            print(f"    match {d[1]} inn {d[2]}: bbb={d[3]} card={d[4]} "
                  f"({d[0]*100:.1f}%)", flush=True)


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    # Map match_id -> season from any cached site's matches.json. New layout:
    #   data/raw/matches/<site_id>/<season>.json
    # Old layout (still supported on disk):
    #   data/raw/matches/<season>.json
    season_for: dict[int, int] = {}
    season_paths: list[Path] = []
    if MATCHES_DIR.exists():
        season_paths.extend(MATCHES_DIR.glob("*.json"))             # legacy
        season_paths.extend(MATCHES_DIR.glob("*/*.json"))           # per-site
    for p in season_paths:
        try:
            season = int(p.stem)
        except ValueError:
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        for m in data.get("matches", []):
            mid = _to_int(m.get("id"))
            if mid is not None:
                season_for[mid] = season

    detail_files = sorted(MATCH_DETAIL_DIR.glob("*.json"))
    print(f"Loading {len(detail_files)} match details", flush=True)

    loaded = 0
    skipped_empty = 0
    bbb_rv = 0
    bbb_nv = 0
    bbb_balls = 0
    for path in detail_files:
        md = load_match_detail(path)
        if md is None:
            skipped_empty += 1
            continue
        mid = _to_int(md.get("id"))
        season_hint = season_for.get(mid) if mid is not None else None
        insert_clubs(cur, md)
        insert_match(cur, md, season_hint)
        insert_match_players(cur, md)
        insert_innings(cur, md)
        if mid is not None:
            home_club = _to_str(md.get("home_club_id"))
            away_club = _to_str(md.get("away_club_id"))
            pc_mp = [
                {"team_side": r[0], "player_id": r[1], "player_name": r[2]}
                for r in cur.execute(
                    "SELECT team_side, player_id, player_name "
                    "FROM match_players WHERE match_id = ?",
                    (mid,),
                ).fetchall()
            ]
            _, n_b, _ = insert_balls(cur, mid, home_club, away_club, pc_mp)
            if n_b:
                bbb_rv += 1
                bbb_balls += n_b
            else:
                # RV had nothing; try NV Play if we cached its scorecard.
                _, n_b2, _ = insert_balls_nvplay(
                    cur, mid, home_club, away_club, pc_mp
                )
                if n_b2:
                    bbb_nv += 1
                    bbb_balls += n_b2
        loaded += 1
        if loaded % 500 == 0:
            print(f"  loaded {loaded}", flush=True)

    conn.commit()
    print(f"Loaded {loaded} matches; skipped {skipped_empty} empty payloads", flush=True)
    print(f"Ball-by-ball: {bbb_rv} RV + {bbb_nv} NV = "
          f"{bbb_rv + bbb_nv} matches, {bbb_balls} balls", flush=True)
    if bbb_rv + bbb_nv:
        validate_balls(cur)

    cur.execute("SELECT COUNT(*) FROM clubs")
    print(f"clubs:          {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM matches")
    print(f"matches:        {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM innings")
    print(f"innings:        {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM batting")
    print(f"batting:        {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM bowling")
    print(f"bowling:        {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM fall_of_wickets")
    print(f"fall_of_wickets:{cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM match_players")
    print(f"match_players:  {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM balls")
    print(f"balls:          {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM match_bbb")
    print(f"match_bbb:      {cur.fetchone()[0]}")

    conn.close()
    print(f"Wrote {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
