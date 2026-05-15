// Cloudflare Worker: Play-Cricket API proxy with CORS + token injection.
//
// The static page (app/matchweek.html etc.) calls this worker. The worker:
//   1. Validates the requested endpoint (allow-list)
//   2. Adds the API token (held in a worker secret, never in the page)
//   3. Forwards to play-cricket.com
//   4. Adds the CORS headers the browser needs
//   5. Caches responses at the edge for 60s — keeps load off Play-Cricket
//
// Deploy: see cf_worker/README.md

const ALLOWED_PATHS = new Set([
  "/matches.json",
  "/match_detail.json",
  "/league_table.json",
]);

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
};

const CACHE_TTL_SECONDS = 60;

function cors(resp) {
  const headers = new Headers(resp.headers);
  for (const [k, v] of Object.entries(CORS_HEADERS)) headers.set(k, v);
  return new Response(resp.body, { status: resp.status, headers });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }
    if (request.method !== "GET") {
      return cors(new Response("Method Not Allowed", { status: 405 }));
    }

    const url = new URL(request.url);
    const path = url.pathname;
    if (!ALLOWED_PATHS.has(path)) {
      return cors(new Response(
        JSON.stringify({ error: "endpoint not allowed", allowed: [...ALLOWED_PATHS] }),
        { status: 404, headers: { "Content-Type": "application/json" } }
      ));
    }

    if (!env.PC_API_TOKEN) {
      return cors(new Response(
        JSON.stringify({ error: "PC_API_TOKEN not configured on the worker" }),
        { status: 500, headers: { "Content-Type": "application/json" } }
      ));
    }

    // Build upstream URL — pass through query params, ALWAYS overwrite token
    const upstream = new URL(`https://play-cricket.com/api/v2${path}`);
    for (const [k, v] of url.searchParams) {
      if (k.toLowerCase() === "api_token") continue;
      upstream.searchParams.set(k, v);
    }
    upstream.searchParams.set("api_token", env.PC_API_TOKEN);

    // Edge cache
    const cache = caches.default;
    const cacheKey = new Request(upstream.toString(), { method: "GET" });
    let resp = await cache.match(cacheKey);
    if (!resp) {
      resp = await fetch(upstream.toString(), {
        headers: {
          "User-Agent": "rcc-pc-proxy/1.0 (+https://github.com/gingerjono/play-cricket)",
          "Accept": "application/json",
        },
      });
      // Re-clone so we can set Cache-Control. Only cache successful JSON.
      if (resp.status === 200) {
        const ct = resp.headers.get("Content-Type") || "";
        if (ct.includes("application/json")) {
          const body = await resp.arrayBuffer();
          const headers = new Headers(resp.headers);
          headers.set("Cache-Control", `public, max-age=${CACHE_TTL_SECONDS}`);
          resp = new Response(body, { status: 200, headers });
          ctx.waitUntil(cache.put(cacheKey, resp.clone()));
        }
      }
    }

    return cors(resp);
  },
};
