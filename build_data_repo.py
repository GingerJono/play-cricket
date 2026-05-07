#!/usr/bin/env python3
"""
Generate the static data-repository page at `app/index.html`.

One card per **played Rainham 1st-XI fixture** in our cache,
filtered to League and non-T20 Cup games:

  * date, opposition (with format chip), result pill
  * BBB ✓/✗  (whether ball-by-ball is loaded for this match_id)
  * opp batting metadata coverage %  (only when BBB is loaded)
  * opp bowling metadata coverage %  (only when BBB is loaded)

Coverage = fraction of opposition legal-balls whose batter / bowler has
a player metadata file with status `complete`. Numbers stay at 0 % until
`data/metadata/players/` is populated; that's expected.

Reads:  data/rainham.db, data/metadata/players/
Writes: app/index.html
"""

from __future__ import annotations

import datetime as dt
import sys
from html import escape

import _app_lib as L


PC_MATCH_URL = "https://rainhamcc.play-cricket.com/website/results/{mid}"
# None = no season cap (every Rainham 1st XI League/non-T20 Cup fixture
# we have a record of). Set to e.g. 10 to limit to the recent decade.
SEASONS_BACK: int | None = None


def opposition_for(row) -> str:
    """Returns the opposition club name on a Rainham fixture."""
    if row["home_club_id"] == L.RAINHAM_CLUB_ID:
        return row["away_club_name"] or "?"
    return row["home_club_name"] or "?"


def result_pill_class(result: str, applied_to: str,
                       home_team_id: str, away_team_id: str) -> str:
    """Map (result, result_applied_to) -> WLDTANR from RCC's perspective."""
    if result in ("D", "T", "A", ""):
        return result or "NR"
    rainham_team_id = L.RAINHAM_FIRST_XI_TEAM_ID
    rainham_was_home = (home_team_id == rainham_team_id)
    rainham_was_away = (away_team_id == rainham_team_id)
    if not (rainham_was_home or rainham_was_away):
        return "NR"
    rainham_is_applied = (
        applied_to == home_team_id and rainham_was_home
        or applied_to == away_team_id and rainham_was_away
    )
    if result == "CON":
        return "W" if rainham_is_applied else "L"
    if result == "W":
        return "W" if rainham_is_applied else "L"
    if result == "L":
        return "L" if rainham_is_applied else "W"
    return "NR"


def collect_rows(conn, since_season: int | None = None) -> list[dict]:
    cur = conn.cursor()
    sql_filter, params = L.first_xi_fixture_where(alias="m")
    sql = f"""
        SELECT m.*, b.n_balls, b.n_legal
        FROM matches m
        LEFT JOIN match_bbb b ON b.match_id = m.match_id
        WHERE {sql_filter}
    """
    if since_season is not None:
        sql += " AND m.season >= ?"
        params.append(since_season)
    sql += " ORDER BY m.match_date DESC"
    matches = cur.execute(sql, params).fetchall()

    today_yyyymmdd = dt.date.today().strftime("%Y%m%d")
    covered = L.covered_player_ids()
    cov_csv = ",".join(str(p) for p in covered) or "NULL"

    out: list[dict] = []
    for m in matches:
        ymd = L.date_yyyymmdd(m["match_date"] or "")
        if ymd and ymd > today_yyyymmdd:
            continue
        opp_club = opposition_for(m)
        has_bbb = (m["n_balls"] or 0) > 0

        bat_cov = bowl_cov = None
        if has_bbb:
            row = cur.execute(f"""
                SELECT
                  SUM(CASE WHEN b.is_legal_ball=1 THEN 1 ELSE 0 END) AS legal,
                  SUM(CASE WHEN b.is_legal_ball=1 AND b.batter_id IN ({cov_csv})
                           THEN 1 ELSE 0 END) AS covered
                FROM balls b
                WHERE b.match_id = ?
                  AND b.team_batting_club_id <> '{L.RAINHAM_CLUB_ID}'
            """, (m["match_id"],)).fetchone()
            if row and (row["legal"] or 0) > 0:
                bat_cov = (row["covered"] or 0) / row["legal"]

            row = cur.execute(f"""
                SELECT
                  SUM(CASE WHEN b.is_legal_ball=1 THEN 1 ELSE 0 END) AS legal,
                  SUM(CASE WHEN b.is_legal_ball=1 AND b.bowler_id IN ({cov_csv})
                           THEN 1 ELSE 0 END) AS covered
                FROM balls b
                WHERE b.match_id = ?
                  AND b.team_bowling_club_id <> '{L.RAINHAM_CLUB_ID}'
            """, (m["match_id"],)).fetchone()
            if row and (row["legal"] or 0) > 0:
                bowl_cov = (row["covered"] or 0) / row["legal"]

        out.append({
            "match_id": m["match_id"],
            "date": m["match_date"],
            "ymd": ymd,
            "season": m["season"],
            "opp_club_name": opp_club,
            "comp_type": m["competition_type"] or "",
            "comp_name": m["competition_name"] or "",
            "result_letter": result_pill_class(
                m["result"] or "", m["result_applied_to"] or "",
                m["home_team_id"] or "", m["away_team_id"] or "",
            ),
            "has_bbb": has_bbb,
            "bat_cov": bat_cov,
            "bowl_cov": bowl_cov,
        })
    return out


def cov_row(label: str, pct: float | None) -> str:
    """One coverage row: <label> <bar> <pct>. Renders an em-dash row when
    pct is None (no BBB for the match)."""
    if pct is None:
        return (f'<div class="cov-row"><span class="lbl">{escape(label)}</span>'
                f'<span class="bar zero"><span style="width:0"></span></span>'
                f'<span class="num">—</span></div>')
    cls = ""
    if pct == 0:
        cls = " zero"
    elif pct < 0.5:
        cls = " low"
    return (f'<div class="cov-row"><span class="lbl">{escape(label)}</span>'
            f'<span class="bar{cls}"><span style="width:{pct*100:.0f}%"></span></span>'
            f'<span class="num">{pct*100:.0f}%</span></div>')


def render_hero(rows: list[dict]) -> str:
    n = len(rows)
    n_bbb = sum(1 for r in rows if r["has_bbb"])
    by_season: dict[int, dict[str, int]] = {}
    for r in rows:
        s = by_season.setdefault(r["season"] or 0, {"n": 0, "bbb": 0})
        s["n"] += 1
        if r["has_bbb"]:
            s["bbb"] += 1

    chips = "".join(
        f'<span class="season-chip">'
        f'<span class="y">{s}</span> {d["bbb"]}/{d["n"]}'
        f'</span>'
        for s, d in sorted(by_season.items(), reverse=True)[:6]
    )

    stats: list[tuple[str, str]] = [
        (str(n), "fixtures"),
        (f"{n_bbb}", "with BBB"),
        (f"{n_bbb*100//max(n,1)}%", "BBB coverage"),
    ]
    span = ""
    if rows:
        years = sorted({r["season"] for r in rows if r.get("season")})
        if years:
            span = f"{years[0]}–{years[-1]}"
    return L.hero(
        "Data repository",
        crumbs=[("Rainham CC", "")],
        lead=(f"Every played 1st-XI fixture (League + non-T20 Cup)"
              f"{(' in the cache (' + span + ')') if span else ''}. "
              f"Coverage % counts the share of opposition legal balls "
              f"whose batter/bowler has a complete metadata record. "
              f"Both columns sit at 0 % until that queue is bootstrapped "
              f"— that's expected."),
        stats=stats,
        extra_html=f'<div class="season-chips">{chips}</div>',
    )


def render_fixture_card(r: dict) -> str:
    pill = (f'<span class="pill {r["result_letter"]}">'
            f'{escape(r["result_letter"])}</span>')
    bbb_pill = ('<span class="tag yes">BBB ✓</span>' if r["has_bbb"]
                else '<span class="tag no">no BBB</span>')
    fmt_chip = ''
    if r["comp_type"]:
        fmt_chip = f'<span class="tag no">{escape(r["comp_type"])}</span>'

    pc_link = PC_MATCH_URL.format(mid=r["match_id"])
    coverage = ""
    if r["has_bbb"]:
        coverage = (
            cov_row("opp bat",  r["bat_cov"])
            + cov_row("opp bowl", r["bowl_cov"])
        )
    return (
        '<div class="fix">'
        f'<div class="row1">'
        f'<div class="left">'
        f'<div class="date">{escape(r["date"])} · {escape(str(r["season"] or ""))}'
        f'</div>'
        f'<div class="opp"><a href="{escape(pc_link)}" target="_blank" '
        f'rel="noopener">vs {escape(r["opp_club_name"])}</a></div>'
        f'<div class="meta">{escape(r["comp_name"])}</div>'
        f'</div>'
        f'<div class="right">{fmt_chip}{pill}{bbb_pill}</div>'
        f'</div>'
        f'{coverage}'
        '</div>'
    )


def build() -> int:
    L.write_static_assets()
    conn = L.open_db()
    since = (dt.date.today().year - SEASONS_BACK + 1
             if SEASONS_BACK is not None else None)
    rows = collect_rows(conn, since_season=since)
    if not rows:
        print("No matches found in window — abort.", file=sys.stderr)
        return 1

    body = (
        render_hero(rows)
        + '<main>'
        + '<div class="card">'
        + '<h2>Browse</h2>'
        + '<div class="row-list">'
          '<a class="row-link" href="rcc/index.html">'
          '<div class="name">RCC player dashboard'
          '<div class="row-meta">Slice any Rainham 1st-XI player\'s '
          'career across home/away, position, season, opposition, '
          'bowler type, innings phase and bowling spells.</div></div>'
          '<div class="right"><span class="count bbb">§ live</span></div>'
          '</a>'
          '<a class="row-link" href="metadata/clubs.html">'
          '<div class="name">Opposition clubs'
          '<div class="row-meta">Capture batting hand and bowling '
          'style for opposition players. Drives the coverage % '
          'columns below.</div></div>'
          '</a>'
        '</div>'
        + '</div>'
        + '<div class="card">'
        + '<h2>Fixtures</h2>'
        + '<p class="note">Coverage % counts the share of opposition '
          'legal balls whose batter/bowler has a complete metadata '
          'record. Lifts off zero as the metadata queue is filled.</p>'
        + '<div class="fix-list">'
        + "".join(render_fixture_card(r) for r in rows)
        + '</div>'
        + '</div>'
        + f'<p class="meta" style="text-align:center">Rebuilt '
          f'{dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}.</p>'
        + '</main>'
    )

    out = L.APP_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(L.page(body, title="Rainham CC — data repository"))
    print(f"Wrote {out}  ({len(rows)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(build())
