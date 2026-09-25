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
