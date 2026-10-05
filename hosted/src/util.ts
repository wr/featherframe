// Small shared helpers for hosted Featherframe.

export function randomHex(bytes = 32): string {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export async function sha256(text: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

/** Now as the household's server reads a clock: naive ISO in its own zone. */
export function localIso(tz: string, d = new Date()): string {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}

export function validTz(tz: string | null | undefined): string {
  try {
    if (tz) {
      new Intl.DateTimeFormat("en", { timeZone: tz });
      return tz;
    }
  } catch { /* not a zone */ }
  return "UTC";
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
}

export function cookie(request: Request, name: string): string | null {
  for (const part of (request.headers.get("Cookie") || "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return null;
}

/** A frame's key, as it sends it: at most 64 hex characters. */
export function frameKey(request: Request): string {
  const k = (request.headers.get("X-FF-Key") || "").trim().toLowerCase();
  return /^[0-9a-f]{16,64}$/.test(k) ? k : "";
}

export function deviceId(request: Request): string {
  return (request.headers.get("X-Device-Id") || "").trim().slice(0, 40);
}

/** Whether a BirdNET-Go webhook body is a detection (its default payload). */
export function isDetection(body: string): boolean {
  try {
    const o = JSON.parse(body) as { type?: unknown };
    return o !== null && typeof o === "object" && o.type === "detection";
  } catch {
    return false;
  }
}

/** The species a push names, lowercased, common and scientific (W-984):
 * BirdNET-Go's webhook (`metadata`), or BirdNET-Pi's Apprise (our JSON in
 * `message`, or the fields at the top). Read only to match the server's
 * `unchanged` list; empty when the body names none. */
export function pushedNames(kind: string, body: string): string[] {
  let o: any;
  try { o = JSON.parse(body); } catch { return []; }
  if (o === null || typeof o !== "object") return [];
  let fields: any = o;
  if (kind === "birdnet_go") {
    fields = o.metadata && typeof o.metadata === "object" ? { common: o.metadata.species, scientific: o.metadata.scientific_name } : {};
  } else if (typeof o.message === "string" && o.message.includes("{") && o.message.includes("}")) {
    try {
      const inner = JSON.parse(o.message.slice(o.message.indexOf("{"), o.message.lastIndexOf("}") + 1));
      if (inner && typeof inner === "object") fields = inner;
    } catch { /* the fields at the top, if any */ }
  }
  const pick = (...vs: unknown[]) => vs.find((v) => typeof v === "string" && v.trim()) as string | undefined;
  return [pick(fields.comname, fields.common, fields.commonName),
          pick(fields.sciname, fields.scientific, fields.scientificName)]
    .filter((v): v is string => !!v).map((v) => v.trim().toLowerCase());
}

/** A detection that changes no picture (W-984): the server's last report
 * named it, by either name, or named every species ("*"). One that names no
 * species is always news. `unchanged` is that report as stored (JSON). */
export function changesNothing(unchanged: string | null, names: string[]): boolean {
  if (!unchanged) return false;
  let list: unknown;
  try { list = JSON.parse(unchanged); } catch { return false; }
  if (list === "*") return true;
  return Array.isArray(list) && names.some((n) => list.includes(n));
}

/** A BirdWeather look (newest first): whether its detections since `lastId`
 * hold news, and the newest id. The first look (no `lastId`) only learns
 * where the station is; a page with nothing older than `lastId` on it may
 * have missed some, so it is news. */
export function birdweatherNews(rows: { id?: number | string; species?: { commonName?: string; scientificName?: string } }[],
                                lastId: string | null, unchanged: string | null): { news: boolean; last: string | null } {
  const ids = rows.map((r) => Number(r.id)).filter((n) => Number.isFinite(n));
  if (!ids.length) return { news: false, last: lastId };
  const newest = String(Math.max(...ids));
  if (lastId === null) return { news: false, last: newest };
  const fresh = rows.filter((r) => Number(r.id) > Number(lastId));
  if (!fresh.length) return { news: false, last: lastId };
  const all = fresh.length === rows.length;
  const news = all || fresh.some((r) => !changesNothing(unchanged,
    [r.species?.commonName, r.species?.scientificName]
      .filter((v): v is string => typeof v === "string" && !!v.trim()).map((v) => v.trim().toLowerCase())));
  return { news, last: newest };
}

/** Plain http is sent to https, before anything else (wrangler dev on
 * localhost stays http). */
export function httpsRedirect(url: URL): Response | null {
  if (url.protocol !== "http:" || url.hostname === "localhost" || url.hostname === "127.0.0.1") return null;
  const to = new URL(url);
  to.protocol = "https:";
  return Response.redirect(to.toString(), 301);
}

/** Whether a frame's update check needs the household's server (W-915): a
 * release is waiting for it (its push message's `ota`, from the server), or a
 * dev image sits at the top of the data dir (`firmware.bin`,
 * `firmware-*.bin`), which the server matches to the frame's board. Otherwise
 * the server would only answer "no firmware hosted", and the front door says
 * so itself: a kit asks every 15 min, and each ask would start the server. */
export function firmwareWaiting(push: string | null, files: string[]): boolean {
  try {
    if (push && JSON.parse(push)?.ota) return true;
  } catch {
    return true;            // not a message we read: let the server answer
  }
  return files.some((f) => f === "firmware.bin" || /^firmware-[^/]*\.bin$/.test(f));
}
