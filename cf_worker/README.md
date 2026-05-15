# Cloudflare Worker — Play-Cricket API proxy

The static `app/matchweek.html` page can't call play-cricket.com directly:

1. Their edge sends `403 Forbidden` for unknown `Origin` headers.
2. Their response carries no `Access-Control-Allow-Origin`, so even if it
   responded the browser would refuse the body.

This worker fixes both. It also keeps the `PC_API_TOKEN` server-side (never
in client JS) and adds a 60-second edge cache so the upstream API isn't
hammered.

Free tier: **100,000 requests / day**, well above what one hobbyist app uses.

## Deploy

You need a Cloudflare account. Free is fine.

```bash
# one-off
npm install -g wrangler
wrangler login

cd cf_worker
wrangler deploy

# you'll be asked for a name on first deploy — accept "rcc-pc-proxy".
# wrangler prints a URL like:
#   https://rcc-pc-proxy.<your-subdomain>.workers.dev

# then store the API token as a secret (NEVER commit it):
wrangler secret put PC_API_TOKEN
# paste the token when prompted
```

## Test it

```bash
WORKER=https://rcc-pc-proxy.<your-subdomain>.workers.dev

# allowed endpoint
curl -s "$WORKER/matches.json?site_id=7300&season=2026" | head -c 300

# disallowed endpoint
curl -s "$WORKER/teams.json?site_id=7300&season=2026"
# → {"error":"endpoint not allowed", ...}
```

## Wire into the static page

`app/matchweek.html` checks for a `?worker=<URL>` query parameter. When
present, it calls `<URL>/match_detail.json?...` directly instead of
loading pre-built JSON snapshots — TRULY live, every page-load.

```
https://gingerjono.github.io/play-cricket/app/matchweek.html?worker=https://rcc-pc-proxy.<sub>.workers.dev
```

You can also pin the worker URL in `app/static/config.js` so the
querystring isn't required (committed defaults).

## What's allowed

The worker hard-codes an allow-list of endpoints (in `src/index.js`):

- `/matches.json`
- `/match_detail.json`
- `/league_table.json`

Any other path returns 404. This stops a leaked URL from exposing the
whole API — only the bits the public page actually needs.

## Caching

Each unique URL (after token injection) is cached at the Cloudflare edge
for 60s. So a fresh page-load fetches; reloads within the next minute
return the cached payload. Tweak `CACHE_TTL_SECONDS` in `src/index.js`
if you want fresher / staler.

## Costs

- Workers: **free** up to 100k requests/day, then $5/mo for 10M.
- Cache: free.
- Subrequests to play-cricket.com: free (just network).

## Pitfalls

- **The token isn't truly secret** — it's in php/globals.php in the
  parent project, used by every PHP page on the legacy site. The worker
  just adds CORS + the allow-list, not bulletproof token security.
- **Browser private mode / strict tracking protection** sometimes blocks
  worker subdomains. The page falls back to the static JSON snapshot if
  the worker call fails.
- **Cloudflare account needed.** No way around this — running it on
  GitHub Pages alone doesn't work because Pages can't run server code.
