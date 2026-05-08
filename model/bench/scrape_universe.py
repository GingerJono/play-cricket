#!/usr/bin/env python3
"""
Scrape each league's homepage to extract its admin-set division ordering.

The Play-Cricket public API doesn't expose `display_order` / `tier`. The
homepage HTML does — every league site renders an accordion list:

    <ul class="list-comp">
      <li><a href="/website/division/<cid>">Division name</a></li>
      ...
    </ul>

This is the canonical source-of-truth ordering. We take the first N entries
that survive an EXCL filter (top senior men's tiers).

Output:
  model/bench/universe.json
    {
      "<slug>": {
        "site_id": ...,
        "scraped_at": ...,
        "all_divisions": [{"cid": ..., "name": ...}, ...],
        "top_n": [{"cid": ..., "name": ...}, ...]   <- the N selected
      },
      ...
    }
"""

from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OUT = Path(__file__).resolve().parent

LEAGUES: list[tuple[str, int]] = [
    ("bdpcl", 252),
    ("bradfordcl", 259),
    ("cheshirecountycl", 7246),
    ("ccl", 285),
    ("derbyscountylge", 296),
    ("devoncl", 298),
    ("dorsetcl", 302),
    ("eapcl", 305),
    ("essexcl", 7300),
    ("gtrmcrcricket", 11685),
    ("hertspremiercl", 572),
    ("hcpcl", 342),
    ("huddersfieldcl", 346),
    ("kcl", 362),
    ("lancashireleague", 10954),
    ("leicestershirescl", 370),
    ("lincspremiercl", 30316),
    ("ldcc", 378),
    ("middlesexccl", 393),
    ("nepremierleague", 409),
    ("nssc", 447),
    ("nwcl", 4655),
    ("nysdl", 441),
    ("ncl", 426),
    ("npcl", 7301),
    ("nottinghamshirecbpl", 443),
    ("swpcl", 10196),
    ("spcl", 496),
    ("surreycricketchampionship", 29012),
    ("sussexcricketleague", 16378),
    ("westofengland", 545),
    ("ycspl", 22888),
    ("ypln", 8240),
]

# strip these — non-senior-men's-Saturday-league tiers
EXCL = re.compile(
    r"\b("
    r"2nd XI|3rd XI|4th XI|5th XI|Reserve|"
    r"Sunday|Midweek|Evening|"
    r"T20|Twenty20|20-over|20/20|Smash|Blast|Plate|"
    r"U1[1-9]|Under\s*1[1-9]|Junior|"
    r"Women|Ladies|Girls|Female|Softball|"
    r"Indoor|Winter|"
    r"Cup|Trophy|Plate|Pool"
    r")\b",
    re.I,
)

PATTERN = re.compile(
    r'<li>\s*<a[^>]*href="/website/division/(\d+)"[^>]*>\s*([^<]+?)\s*</a>\s*</li>',
    re.I,
)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def html_unescape(s: str) -> str:
    # minimal unescape — homepage uses &#39; for apostrophes etc.
    return (s.replace("&#39;", "'")
             .replace("&amp;", "&")
             .replace("&quot;", '"')
             .replace("&lt;", "<")
             .replace("&gt;", ">"))


def scrape_one(slug: str, site_id: int, attempts: int = 4, top_n: int = 5) -> dict:
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            req = Request(
                f"https://{slug}.play-cricket.com/",
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Cache-Control": "no-cache",
                },
            )
            with urlopen(req, timeout=45) as r:
                html = r.read().decode("utf-8", errors="ignore")
            break
        except (HTTPError, URLError, TimeoutError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    else:
        return {
            "slug": slug, "site_id": site_id, "error": f"fetch failed: {last_err}",
            "all_divisions": [], "top_n": [],
        }

    raw = PATTERN.findall(html)
    all_divs = [{"cid": int(cid), "name": html_unescape(name)} for cid, name in raw]
    senior = [d for d in all_divs if not EXCL.search(d["name"])]
    return {
        "slug": slug,
        "site_id": site_id,
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "all_divisions": all_divs,
        "top_n": senior[:top_n],
    }


def main() -> int:
    print(f"scraping {len(LEAGUES)} league homepages in parallel ...", flush=True)
    t0 = time.perf_counter()
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(scrape_one, slug, sid): (slug, sid) for slug, sid in LEAGUES}
        for fut in as_completed(futs):
            slug, sid = futs[fut]
            d = fut.result()
            results[slug] = d
    print(f"done in {time.perf_counter() - t0:.1f}s", flush=True)
    print()

    print(f"{'slug':<28} {'site_id':>8}  {'all':>4}  {'top5':>4}  status")
    print("-" * 70)
    for slug, sid in LEAGUES:
        d = results[slug]
        if d.get("error"):
            print(f"{slug:<28} {sid:>8}  {'-':>4}  {'-':>4}  ERROR  {d['error']}")
            continue
        all_n = len(d["all_divisions"])
        top_n = len(d["top_n"])
        first = d["top_n"][0]["name"] if d["top_n"] else "(none)"
        print(f"{slug:<28} {sid:>8}  {all_n:>4}  {top_n:>4}  {first[:60]}")

    out = OUT / "universe.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
