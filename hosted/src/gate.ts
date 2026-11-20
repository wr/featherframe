// The gate for the owner's AI keys (W-937). On Featherframe Cloud a
// household's server never holds its owner's key: a model sends its request
// to the vendor's own path on `<vendor>.vendor.internal`, a name that is
// nothing on the internet. Cloudflare's outbound interception hands it to
// this entrypoint, registered by HouseholdServer for its own household, so
// the household comes from the props that trusted code set, never from the
// request. The gate adds the key the front door keeps for that household,
// only for the paths Featherframe calls, and for a paid call only while the
// spend gate holds that purchase open; then the request goes on to the vendor
// and its answer streams back.
//
// A Worker, not the front door: an image takes up to two minutes, and a
// Durable Object is billed by the second while it waits on a fetch, a Worker
// only for its own CPU. The rules are here; the entrypoint is vendorgate.ts.

export const GATE_DOMAIN = "vendor.internal";
export type Vendor = "openai" | "gemini" | "anthropic" | "replicate";
export type Slot = "image" | "text";
export const SLOTS: readonly Slot[] = ["image", "text"];

const ORIGINS: Record<Vendor, string> = {
  openai: "https://api.openai.com",
  gemini: "https://generativelanguage.googleapis.com",
  anthropic: "https://api.anthropic.com",
  replicate: "https://api.replicate.com",
};

// What Featherframe asks each vendor (server/featherframe/render/genart.py
// and keys.PROBES), by method; anything else is refused. A POST is paid.
const ASKS: Record<Vendor, Record<"GET" | "POST", RegExp[]>> = {
  openai: {
    GET: [/^\/v1\/models$/],
    POST: [/^\/v1\/images\/(edits|generations)$/, /^\/v1\/chat\/completions$/, /^\/v1\/responses$/],
  },
  gemini: {
    GET: [/^\/v1beta\/models$/],
    POST: [/^\/v1beta\/models\/[\w.-]+:generateContent$/],
  },
  anthropic: {
    GET: [/^\/v1\/models$/],
    POST: [/^\/v1\/messages$/],
  },
  replicate: {
    GET: [/^\/v1\/account$/, /^\/v1\/predictions\/[\w-]+$/],
    POST: [/^\/v1\/models\/[\w.-]+\/[\w.-]+\/predictions$/, /^\/v1\/predictions$/],
  },
};

// Every header that could carry a key or name ours: none reaches a vendor
// but the one the gate sets.
const DROPPED = ["authorization", "x-goog-api-key", "x-api-key", "x-ff-key", "x-ff-purchase", "cookie", "host"];

/** The vendor a request is for, or why it is refused. */
export function route(host: string, method: string, path: string): { vendor: Vendor } | { status: number; reason: string } {
  const m = /^([a-z]+)\.vendor\.internal$/.exec(host);
  const vendor = m?.[1] as Vendor | undefined;
  if (!vendor || !(vendor in ORIGINS)) return { status: 404, reason: "unknown vendor" };
  if (method !== "GET" && method !== "POST") return { status: 405, reason: "method" };
  if (!ASKS[vendor][method].some((re) => re.test(path))) return { status: 404, reason: "not a path Featherframe asks" };
  return { vendor };
}

export function authHeader(vendor: Vendor, key: string): [string, string] {
  switch (vendor) {
    case "openai": return ["Authorization", `Bearer ${key}`];
    case "gemini": return ["x-goog-api-key", key];
    case "anthropic": return ["x-api-key", key];
    case "replicate": return ["Authorization", `Token ${key}`];
  }
}

/** The request as the vendor gets it: its own host, the key, nothing of ours. */
export function toVendor(request: Request, vendor: Vendor, key: string): Request {
  const url = new URL(request.url);
  const headers = new Headers(request.headers);
  for (const h of DROPPED) headers.delete(h);
  headers.set(...authHeader(vendor, key));
  return new Request(ORIGINS[vendor] + url.pathname + url.search, {
    method: request.method, headers, body: request.method === "POST" ? request.body : null, redirect: "manual",
  });
}

/** One of the gate's own answers: marked, so the server never takes it for the vendor's. */
export function refuse(status: number, reason: string): Response {
  return new Response(reason, { status, headers: { "X-FF-Gate": reason, "Content-Type": "text/plain" } });
}

export type KeyAnswer = { ok: true; sealed: string } | { ok: false; status: number; reason: string };
