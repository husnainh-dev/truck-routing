// Cloudflare Pages Function: forwards /api/* to the Django backend, so the
// browser only ever talks to the Pages domain (no CORS needed).
// BACKEND_URL is set in wrangler.toml, e.g. https://truck-routing-api.onrender.com
export async function onRequest({ request, env }) {
  if (!env.BACKEND_URL) {
    return Response.json({ error: "BACKEND_URL is not configured." }, { status: 500 });
  }
  const url = new URL(request.url);
  const target = env.BACKEND_URL.replace(/\/$/, "") + url.pathname + url.search;
  try {
    return await fetch(new Request(target, request));
  } catch {
    return Response.json({ error: "The planning server is starting up. Try again in a moment." }, { status: 502 });
  }
}
