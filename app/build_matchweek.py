#!/usr/bin/env python3
"""
Build the matchweek data file: 25 Essex 1st-XI matches (5 from each of the
top 5 divisions) for a given Saturday, with live scores + top scorers/
wicket-takers per innings.

DOES hit the Play-Cricket API live (server-side, with the project token).
NOT the local SQLite. The whole point is fresh data, even mid-innings.

Output:
  app/data/matchweek/<YYYY-MM-DD>.json     per-Saturday data
  app/data/matchweek/index.json            list of available matchweeks
                                            (newest first; current = default)

Matchweek selection rule (per user):
  - Sun / Mon / Tue / Wed: matchweek = previous Saturday (yesterday's results)
  - Thu / Fri / Sat: matchweek = upcoming/current Saturday (preview / live)

Usage:
  python3 app/build_matchweek.py
  python3 app/build_matchweek.py --date 2025-09-06
  python3 app/build_matchweek.py --season 2025

Cron: hourly during match season is plenty. Commit the resulting JSONs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent / "data" / "matchweek"
DATA_DIR.mkdir(parents=True, exist_ok=True)

API_BASE = "https://play-cricket.com/api/v2"
ESSEX_SITE_ID = 7300
API_TOKEN = os.environ.get("PC_API_TOKEN", "683aa06cb15ca7e565c28cca8aac77ab")

# Top-5 1st-XI Limited Overs cids per season for Essex League.
# From homepage scrape (model/bench/universe.json) + earlier survey.
ESSEX_TOP5_BY_SEASON = {
    2026: {
        135282: "1st XI Premier Division",
        135283: "1st XI Division One",
        135296: "1st XI Division Two",
        135297: "1st XI Division Three",
        135298: "1st XI Division Four",
    },
    2025: {
        125062: "1st XI Premier Division",
        125063: "1st XI Division One",
        125064: "1st XI Division Two",
        125065: "1st XI Division Three",
        125066: "1st XI Division Four",
    },
    2024: {
        117997: "1st XI Premier Division",
        117998: "1st XI Division One",
        117999: "1st XI Division Two",
        118000: "1st XI Division Three",
        118001: "1st XI Division Four",
    },
    2023: {
        110435: "1st XI Premier Division",
        110436: "1st XI Division One",
        110437: "1st XI Division Two",
        110439: "1st XI Division Three",
        110438: "1st XI Division Four",
    },
}

USER_AGENT = "rainham-cc-stats/matchweek (+https://github.com/gingerjono/play-cricket)"


def http_get_json(url: str, attempts: int = 3) -> dict:
    backoff = 1.0
    last_err = None
    for _ in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=45) as r:
                return json.loads(r.read())
        except Exception as e:
            last_err = e
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"GET failed {attempts}x: {url} ({last_err})")


def matchweek_for_today(today: date | None = None) -> date:
    today = today or date.today()
    wd = today.weekday()  # Mon=0..Sun=6
    # Sun/Mon/Tue/Wed -> previous Saturday
    # Thu/Fri/Sat -> next/current Saturday
    if wd in (6, 0, 1, 2):
        # rewind to last Saturday
        delta = (wd - 5) % 7
        if delta == 0:
            delta = 7
        return today - timedelta(days=delta)
    # Thu (3) → +2; Fri (4) → +1; Sat (5) → 0
    return today + timedelta(days=(5 - wd))


def parse_match_date(s: str) -> date:
    """Play-Cricket uses dd/mm/yyyy."""
    d, m, y = s.split("/")
    return date(int(y), int(m), int(d))


def fetch_season_matches(site_id: int, season: int) -> list[dict]:
    url = f"{API_BASE}/matches.json?" + urlencode({
        "site_id": site_id, "api_token": API_TOKEN, "season": season,
    })
    return http_get_json(url).get("matches", [])


def fetch_match_detail(match_id: int) -> dict:
    url = f"{API_BASE}/match_detail.json?" + urlencode({
        "match_id": match_id, "api_token": API_TOKEN,
    })
    d = http_get_json(url)
    md = (d.get("match_details") or [{}])[0]
    return md


def parse_overs(s) -> float:
    if s is None:
        return 0.0
    try:
        s = str(s).strip()
        if not s:
            return 0.0
        if "." in s:
            w, b = s.split(".", 1)
            return float(w) + (int(b) / 6.0)
        return float(s)
    except (ValueError, AttributeError):
        return 0.0


def summarise_innings(inn: dict) -> dict:
    """Pull team, score, top 3 batters by runs, top 3 bowlers by wickets."""
    bat = inn.get("bat") or []
    bowl = inn.get("bowl") or []

    def b_runs(b):
        try:
            return int(b.get("runs") or 0)
        except (ValueError, TypeError):
            return 0

    def w_wkts(w):
        try:
            return int(w.get("wickets") or 0)
        except (ValueError, TypeError):
            return 0

    top_bat = sorted(
        [b for b in bat if (b.get("how_out") or "") not in ("did not bat", "absent", "")],
        key=b_runs, reverse=True,
    )[:3]
    top_bowl = sorted(bowl, key=lambda x: (w_wkts(x), -parse_overs(x.get("overs"))),
                      reverse=True)[:3]

    return {
        "team_batting_name": inn.get("team_batting_name"),
        "team_batting_id": inn.get("team_batting_id"),
        "runs": inn.get("runs"),
        "wickets": inn.get("wickets"),
        "overs": inn.get("overs"),
        "declared": bool(inn.get("declared")),
        "all_out": bool(inn.get("all_out")),
        "extras": inn.get("extras"),
        "top_batters": [
            {"name": b.get("batsman_name"),
             "runs": b.get("runs"),
             "balls": b.get("balls"),
             "fours": b.get("fours"),
             "sixes": b.get("sixes"),
             "how_out": b.get("how_out"),
             "fielder_name": b.get("fielder_name"),
             "bowler_name": b.get("bowler_name")}
            for b in top_bat
        ],
        "top_bowlers": [
            {"name": w.get("bowler_name"),
             "overs": w.get("overs"),
             "maidens": w.get("maidens"),
             "runs": w.get("runs"),
             "wickets": w.get("wickets")}
            for w in top_bowl
        ],
    }


def play_cricket_url(slug: str, match_id: int) -> str:
    return f"https://{slug}.play-cricket.com/website/results/{match_id}"


_WINPROB_ART = None


def winprob_for_match(match_id: int, completed: bool, in_progress: bool) -> dict | None:
    """Return {at_innings1_end, final, n_balls_inn1, n_balls_inn2} or None."""
    global _WINPROB_ART
    if _WINPROB_ART is None:
        try:
            sys.path.insert(0, str(ROOT))
            from model.poc.predict_winprob import load_artefacts
            _WINPROB_ART = load_artefacts()
        except Exception as e:
            print(f"  (winprob disabled: {e})", file=sys.stderr)
            _WINPROB_ART = False
    if _WINPROB_ART is False:
        return None
    if not (completed or in_progress):
        return None
    try:
        from model.poc.predict_winprob import winprob_trajectory
        traj = winprob_trajectory(_WINPROB_ART, match_id)
    except Exception as e:
        print(f"  winprob {match_id}: {e}", file=sys.stderr)
        return None
    n1 = len(traj["innings1"])
    n2 = len(traj["innings2"])
    if n1 == 0 and n2 == 0:
        return None  # no BBB → no chip
    return {
        "at_innings1_end": traj["end_of_innings1_p"],
        "final": traj["final_p"],
        "n_balls_inn1": n1,
        "n_balls_inn2": n2,
    }


def build_match_card(m: dict, division: str) -> dict:
    md = fetch_match_detail(m["id"])
    # status — Play-Cricket result codes:
    # '' = not played yet, 'W'=Won (applied_to gives winner team_id), 'L'=Lost,
    # 'T'=Tied, 'D'=Draw, 'A'=Abandoned, 'C'=Cancelled, 'NR'=No Result
    result = md.get("result") or ""
    desc = md.get("result_description") or ""
    in_progress = result == "" and (md.get("innings") or [])
    completed = result in ("W", "L", "T", "D")

    innings = [summarise_innings(inn) for inn in (md.get("innings") or [])]

    winprob = winprob_for_match(int(m["id"]), completed, bool(in_progress))

    return {
        "match_id": int(m["id"]),
        "division": division,
        "winprob": winprob,
        "competition_id": int(m.get("competition_id") or 0),
        "match_date": m.get("match_date"),
        "match_time": m.get("match_time"),
        "ground_name": md.get("ground_name") or m.get("ground_name"),
        "home_team_name": md.get("home_team_name") or m.get("home_team_name"),
        "away_team_name": md.get("away_team_name") or m.get("away_team_name"),
        "home_club_name": md.get("home_club_name") or m.get("home_club_name"),
        "away_club_name": md.get("away_club_name") or m.get("away_club_name"),
        "toss": md.get("toss"),
        "result": result,
        "result_description": desc,
        "result_applied_to": md.get("result_applied_to"),
        "status": (
            "completed" if completed
            else "in_progress" if in_progress
            else "abandoned" if result in ("A", "C", "NR")
            else "scheduled"
        ),
        "innings": innings,
        "play_cricket_url": play_cricket_url("essexcl", int(m["id"])),
    }


def build_index() -> None:
    """Refresh app/data/matchweek/index.json with all available files."""
    files = sorted(DATA_DIR.glob("[0-9]*.json"), reverse=True)
    entries = []
    for f in files:
        try:
            d = json.loads(f.read_text())
            entries.append({
                "matchweek": f.stem,
                "n_games": len(d.get("games", [])),
                "completed": sum(1 for g in d.get("games", []) if g.get("status") == "completed"),
                "in_progress": sum(1 for g in d.get("games", []) if g.get("status") == "in_progress"),
                "scheduled": sum(1 for g in d.get("games", []) if g.get("status") == "scheduled"),
                "built_at": d.get("built_at"),
            })
        except Exception:
            continue
    (DATA_DIR / "index.json").write_text(json.dumps({
        "matchweeks": entries,
        "default": entries[0]["matchweek"] if entries else None,
    }, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="ISO matchweek Saturday (default: per rule)")
    ap.add_argument("--season", type=int, help="season override")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit-per-div", type=int, default=5)
    args = ap.parse_args()

    target = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
              else matchweek_for_today())
    season = args.season or target.year
    print(f"matchweek: {target} (season {season})", flush=True)

    cohort = ESSEX_TOP5_BY_SEASON.get(season)
    if not cohort:
        raise SystemExit(f"no cohort cids for season {season}; "
                         f"add to ESSEX_TOP5_BY_SEASON")

    print(f"fetching season {season} matches summary ...", flush=True)
    season_matches = fetch_season_matches(ESSEX_SITE_ID, season)
    by_div: dict[int, list[dict]] = {cid: [] for cid in cohort}
    for m in season_matches:
        try:
            cid = int(m.get("competition_id"))
        except (TypeError, ValueError):
            continue
        if cid not in cohort:
            continue
        try:
            md = parse_match_date(m.get("match_date") or "")
        except Exception:
            continue
        if md != target:
            continue
        by_div[cid].append(m)

    selected: list[tuple[int, str, dict]] = []
    for cid, div_name in cohort.items():
        for m in sorted(by_div[cid], key=lambda x: x.get("match_time") or "")[:args.limit_per_div]:
            selected.append((cid, div_name, m))
    print(f"selected {len(selected)} matches across {len(cohort)} divisions", flush=True)

    if not selected:
        out_path = DATA_DIR / f"{target.isoformat()}.json"
        out_path.write_text(json.dumps({
            "matchweek": target.isoformat(), "season": season,
            "built_at": datetime.utcnow().isoformat() + "Z",
            "games": [], "note": "no matches scheduled in cohort for this date",
        }, indent=2))
        print(f"  no matches; wrote stub {out_path}")
        build_index()
        return 0

    games: list[dict] = []
    print("fetching match_detail for each ...", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(build_match_card, m, div): (cid, div, m)
                for cid, div, m in selected}
        for fut in as_completed(futs):
            cid, div, m = futs[fut]
            try:
                games.append(fut.result())
            except Exception as e:
                print(f"  failed {m['id']}: {e}", flush=True)

    games.sort(key=lambda g: (g["division"], g.get("match_time") or ""))

    out = {
        "matchweek": target.isoformat(),
        "season": season,
        "site_id": ESSEX_SITE_ID,
        "built_at": datetime.utcnow().isoformat() + "Z",
        "n_games": len(games),
        "n_completed": sum(1 for g in games if g["status"] == "completed"),
        "n_in_progress": sum(1 for g in games if g["status"] == "in_progress"),
        "n_scheduled": sum(1 for g in games if g["status"] == "scheduled"),
        "games": games,
    }
    out_path = DATA_DIR / f"{target.isoformat()}.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {out_path}  "
          f"(completed={out['n_completed']}, "
          f"in_progress={out['n_in_progress']}, "
          f"scheduled={out['n_scheduled']})")
    build_index()
    return 0


if __name__ == "__main__":
    sys.exit(main())
