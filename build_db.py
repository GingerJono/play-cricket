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
DB_PATH = ROOT / "data" / "rainham.db"

SCHEMA = """
DROP TABLE IF EXISTS clubs;
DROP TABLE IF EXISTS matches;
DROP TABLE IF EXISTS match_players;
DROP TABLE IF EXISTS innings;
DROP TABLE IF EXISTS batting;
DROP TABLE IF EXISTS bowling;
DROP TABLE IF EXISTS fall_of_wickets;

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
        loaded += 1
        if loaded % 500 == 0:
            print(f"  loaded {loaded}", flush=True)

    conn.commit()
    print(f"Loaded {loaded} matches; skipped {skipped_empty} empty payloads", flush=True)

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

    conn.close()
    print(f"Wrote {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
