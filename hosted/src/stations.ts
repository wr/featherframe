// BirdWeather stations near a new owner (W-888): the setup page's list. One
// call to BirdWeather's public GraphQL API (`stations`, documented at
// app.birdweather.com/api), made here rather than in the phone's browser so
// it is cached and a failure is simply an empty list. The `id` it answers is
// what the BirdWeather detection source takes as its station.

const GRAPHQL = "https://app.birdweather.com/graphql";
// About 55 km either way of the point: a station further off hears other species.
const BOX_DEG = 0.5;
const ACTIVE_S = 7 * 24 * 60 * 60;
const SHOWN = 5;
const CACHE_S = 60 * 60;

export interface Station {
  id: string; name: string; km: number; species: number; continent: string;
  lat: number; lon: number;
}

interface Node {
  id: string; name?: string | null; continent?: string | null; latestDetectionAt?: string | null;
  coords?: { lat: number; lon: number } | null; counts?: { species?: number | null } | null;
}

const FIELDS = "id name continent latestDetectionAt coords { lat lon } counts { species }";

export function kmBetween(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = (d: number) => (d * Math.PI) / 180;
  const a = Math.sin(r(lat2 - lat1) / 2) ** 2
    + Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lon2 - lon1) / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** The answer's stations heard from in the past week, nearest first when
 * there is a point to measure from (else the most species first). */
export function rankStations(nodes: Node[], at: { lat: number; lon: number } | null, now = Date.now()): Station[] {
  const out: Station[] = [];
  for (const n of nodes) {
    if (!n || !n.id || !n.coords) continue;
    const last = n.latestDetectionAt ? Date.parse(n.latestDetectionAt) : NaN;
    if (!(now - last < ACTIVE_S * 1000)) continue;
    out.push({
      id: String(n.id), name: (n.name || "").trim() || `Station ${n.id}`,
      km: at ? Math.round(kmBetween(at.lat, at.lon, n.coords.lat, n.coords.lon)) : 0,
      species: Number(n.counts?.species || 0), continent: n.continent || "",
      lat: n.coords.lat, lon: n.coords.lon,
    });
  }
  out.sort(at ? (a, b) => a.km - b.km || b.species - a.species : (a, b) => b.species - a.species);
  return out.slice(0, SHOWN);
}

async function ask(query: string, variables: Record<string, unknown>, key: string): Promise<Node[]> {
  const cache = (globalThis as { caches?: CacheStorage }).caches?.default;
  const cacheReq = new Request(`https://stations.cache/${encodeURIComponent(key)}`);
  const hit = cache ? await cache.match(cacheReq) : undefined;
  if (hit) return hit.json<Node[]>();
  try {
    const r = await fetch(GRAPHQL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });
    if (!r.ok) return [];
    const body = await r.json<{ data?: { stations?: { nodes?: Node[] } } }>();
    const nodes = body.data?.stations?.nodes || [];
    if (cache) {
      await cache.put(cacheReq, new Response(JSON.stringify(nodes), {
        headers: { "Content-Type": "application/json", "Cache-Control": `max-age=${CACHE_S}` },
      }));
    }
    return nodes;
  } catch (err) {
    console.error("birdweather stations", err);
    return [];
  }
}

const PAGE = 100;
// About 15 km either way first, 55 km if that finds fewer than a list.
const BOXES = [0.15, BOX_DEG];

/** Stations near a point, cached per 0.1° cell. The API filters by
 * activity (`period`) but answers a box by name, not distance: a small box
 * first keeps the nearest from falling off a full page. */
export async function stationsNear(lat: number, lon: number): Promise<Station[]> {
  const cLat = Math.round(lat * 10) / 10;
  const cLon = Math.round(lon * 10) / 10;
  const q = `query($ne: InputLocation!, $sw: InputLocation!) {
    stations(ne: $ne, sw: $sw, period: {count: 7, unit: "day"}, first: ${PAGE}) { nodes { ${FIELDS} } } }`;
  let found: Station[] = [];
  for (const box of BOXES) {
    const dLon = Math.min(box / Math.max(Math.cos((cLat * Math.PI) / 180), 0.1), 179);
    const nodes = await ask(q, {
      ne: { lat: Math.min(cLat + box, 90), lon: Math.min(cLon + dLon, 180) },
      sw: { lat: Math.max(cLat - box, -90), lon: Math.max(cLon - dLon, -180) },
    }, `near:${cLat},${cLon}:${box}`);
    found = rankStations(nodes, { lat, lon });
    if (found.length >= SHOWN) break;
  }
  return found;
}

/** Stations whose name matches `text`, measured from `at` when known. */
export async function stationsNamed(text: string, at: { lat: number; lon: number } | null): Promise<Station[]> {
  const t = text.trim().slice(0, 60);
  if (t.length < 2) return [];
  const q = `query($q: String) { stations(query: $q, first: 30) { nodes { ${FIELDS} } } }`;
  return rankStations(await ask(q, { q: t }, `name:${t.toLowerCase()}`), at);
}

/** One station by its id, for the setup's seed: its name and continent. */
export async function stationById(id: string): Promise<Node | null> {
  if (!/^\d{1,12}$/.test(id)) return null;
  const q = `query($id: ID!) { station(id: $id) { ${FIELDS} } }`;
  try {
    const r = await fetch(GRAPHQL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, variables: { id } }),
    });
    if (!r.ok) return null;
    const body = await r.json<{ data?: { station?: Node | null } }>();
    return body.data?.station || null;
  } catch {
    return null;
  }
}

/** The Region a station's continent reads first. */
export function regionFor(continent: string): string | null {
  const c = continent.toLowerCase();
  if (c.includes("north america")) return "north-america";
  if (c.includes("europe")) return "europe";
  if (c.includes("oceania") || c.includes("australia")) return "australia";
  if (c.includes("asia")) return "asia";
  return null;
}
