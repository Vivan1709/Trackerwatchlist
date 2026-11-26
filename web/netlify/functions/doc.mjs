const PATHS = p => {
  if (p === "portfolio/main") return "portfolio.json";
  if (p === "meta/index") return "data/index.json";
  if (p === "meta/prices") return "data/prices.json";
  const m = p.match(/^(days|holders|txns|corp)\/([A-Za-z0-9&_\-]+)$/);
  return m ? `data/${m[1]}/${m[2]}.json` : null;
};
const gh = (env, path, init = {}) => fetch(`https://api.github.com/repos/${E.REPO}/${path}`, {...init,
  headers: {"Authorization": `Bearer ${E.GH_TOKEN}`, "User-Agent": "big-money-tracker", "Accept": "application/vnd.github+json", ...(init.headers || {})}});
const json = (o, status = 200) => new Response(JSON.stringify(o), {status, headers: {"content-type": "application/json", "cache-control": "no-store"}});

async function readFile(env, file){
  const r = await gh(env, `contents/${file}?ref=main`, {headers: {"Accept": "application/vnd.github.raw+json"}});
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`GitHub ${r.status}`);
  return JSON.parse(await r.text());
}


const E = {get REPO(){ return process.env.REPO || "Vivan1709/Trackerwatchlist"; }, get GH_TOKEN(){ return process.env.GH_TOKEN; }, get KEY(){ return process.env.SITE_KEY; }};
export default async (req) => {
  const url = new URL(req.url);
  const path = url.searchParams.get("path") || "", file = PATHS(path);
  if (!file) return json({error: "unknown path"}, 400);
  if (req.method === "GET") {
    try {
      let data = await readFile(E, file);
      if (data && path === "meta/prices") data = {p: data};
      if (data && path === "portfolio/main" && !data.items) data = {items: (data.symbols || []).map(s => ({s, q: s})), follows: []};
      return json({exists: data != null, data});
    } catch (e) { return json({error: String(e.message || e)}, 502); }
  }
  if (req.method === "POST" && path === "portfolio/main") {
    if (!E.GH_TOKEN) return json({error: "Saving isn't switched on yet: add GH_TOKEN in Netlify"}, 503);
    if (E.KEY && req.headers.get("x-key") !== E.KEY) return json({error: "Open the site from your personal link to make changes"}, 403);
    const body = await req.json();
    const items = (body.items || []).filter(i => /^[A-Z0-9&_\-]{1,20}$/.test(i.s)).slice(0, 100);
    const meta = await gh(E, "contents/portfolio.json?ref=main").then(r => r.ok ? r.json() : null);
    const old = meta ? JSON.parse(Buffer.from(meta.content, "base64").toString("utf8")) : {symbols: []};
    const doc = {symbols: items.map(i => i.s), items, follows: (body.follows || []).slice(0, 50), updated: new Date().toISOString()};
    const put = await gh(E, "contents/portfolio.json", {method: "PUT", body: JSON.stringify({
      message: "Portfolio updated from the website", sha: meta?.sha, branch: "main",
      content: Buffer.from(JSON.stringify(doc, null, 1)).toString("base64")})});
    if (!put.ok) return json({error: `Couldn't save (GitHub ${put.status})`}, 502);
    const added = doc.symbols.filter(s => !(old.symbols || []).includes(s));
    if (added.length) await gh(E, "actions/workflows/collect.yml/dispatches", {method: "POST", body: JSON.stringify({ref: "main", inputs: {symbols: added.join(",")}})});
    return json({ok: true, added});
  }
  return json({error: "not allowed"}, 405);
};
export const config = {path: "/api/doc"};
