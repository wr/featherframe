// Pairing codes (W-845, W-849): what a device no household has claimed shows,
// kits and viewers alike.

import type { Env } from "./index";

// A pairing code: letters only (the engraved face has old-style figures that
// rise and fall), and none of I/L/O/U/V to confuse on the glass.
const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZ";
const CODE_TTL_S = 24 * 60 * 60;

// Bumped when the Lobby draws a pairing screen differently: a new ETag (and
// R2 key), so a screen showing the old drawing is sent the new one. Bump it
// again once the Lobby's rollout has finished: a code asked for mid-rollout
// is drawn by the old image and cached under the new key.
export const LOBBY_DRAWING = "boot-art-3";

/** The code a device no one has claimed shows, made on its first ask and
 * kept for a day. `report` is what it said about itself, for the household
 * that claims it: a kit's own headers, or a viewer's (W-849). */
export async function pairingCode(env: Env, id: string, keyHash: string,
                                  report: Record<string, unknown>): Promise<{ code: string; expiresAt: number }> {
  const now = Math.floor(Date.now() / 1000);
  const row = await env.DB.prepare(
    "SELECT code, expires_at FROM pairing WHERE device_id = ? AND key_hash = ? AND expires_at > ?")
    .bind(id, keyHash, now).first<{ code: string; expires_at: number }>();
  if (row) return { code: row.code, expiresAt: row.expires_at };
  const code = [...crypto.getRandomValues(new Uint8Array(6))]
    .map((b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
  await env.DB.batch([
    env.DB.prepare("DELETE FROM pairing WHERE device_id = ? AND key_hash = ?").bind(id, keyHash),
    env.DB.prepare("INSERT INTO pairing (code, device_id, key_hash, report, expires_at) VALUES (?, ?, ?, ?, ?)")
      .bind(code, id, keyHash, JSON.stringify(report), now + CODE_TTL_S),
  ]);
  return { code, expiresAt: now + CODE_TTL_S };
}

/** When a code stops working, in the asking device's own time zone (its IP's,
 * as Cloudflare places it): "25 September, 10:32 am". The glass keeps its
 * picture with the power off, so a frame found in a drawer still shows its
 * code; the date says whether it is worth typing. */
export function expiryText(expiresAt: number, request: Request): string {
  const tz = (request as { cf?: { timezone?: string } }).cf?.timezone;
  const fmt = (timeZone: string) => new Intl.DateTimeFormat("en-GB", {
    timeZone, day: "numeric", month: "long", hour: "numeric", minute: "2-digit", hour12: true,
  }).formatToParts(new Date(expiresAt * 1000));
  let parts: Intl.DateTimeFormatPart[];
  let zone = "";
  try { parts = fmt(tz || "UTC"); if (!tz) zone = " UTC"; }
  catch { parts = fmt("UTC"); zone = " UTC"; }
  const p = (t: string) => parts.find((x) => x.type === t)?.value ?? "";
  return `${p("day")} ${p("month")}, ${p("hour")}:${p("minute")} ${p("dayPeriod").toLowerCase()}${zone}`;
}

