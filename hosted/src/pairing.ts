// Pairing codes (W-845, W-849): what a device no household has claimed shows,
// kits and viewers alike.

import type { Env } from "./index";

// A pairing code: letters only (the engraved face has old-style figures that
// rise and fall), and none of I/L/O/Q/U/V to confuse on the glass.
const CODE_ALPHABET = "ABCDEFGHJKMNPRSTWXYZ";
const CODE_TTL_S = 24 * 60 * 60;
// The setup page's secret for a code (W-888), lower case like the URL it
// ends (W-889: an upper-case URL looked odd in the phone's camera).
const TOKEN_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz";
export const SETUP_TOKEN_LEN = 12;

export function setupToken(): string {
  // 252 is the largest multiple of 36 under 256: no letter is likelier.
  const out: string[] = [];
  while (out.length < SETUP_TOKEN_LEN) {
    for (const b of crypto.getRandomValues(new Uint8Array(16))) {
      if (b < 252 && out.length < SETUP_TOKEN_LEN) out.push(TOKEN_ALPHABET[b % 36]);
    }
  }
  return out.join("");
}

/** The setup page for a code, as the QR spells it. */
export function setupUrl(host: string, code: string, token: string): string {
  return `https://${host}/setup/${code.toLowerCase()}/${token.toLowerCase()}`;
}

// Bumped when the Lobby draws a pairing screen differently: a new ETag (and
// R2 key), so a screen showing the old drawing is sent the new one. Bump it
// again once the Lobby's rollout has finished: a code asked for mid-rollout
// is drawn by the old image and cached under the new key.
export const LOBBY_DRAWING = "setup-row-4";

/** The code a device no one has claimed shows, made on its first ask and
 * kept for a day. `report` is what it said about itself, for the household
 * that claims it: a kit's own headers, or a viewer's (W-849). */
export async function pairingCode(env: Env, id: string, keyHash: string,
                                  report: Record<string, unknown>): Promise<{ code: string; expiresAt: number; token: string }> {
  const now = Math.floor(Date.now() / 1000);
  const row = await env.DB.prepare(
    "SELECT code, expires_at, setup_token FROM pairing WHERE device_id = ? AND key_hash = ? AND expires_at > ?")
    .bind(id, keyHash, now).first<{ code: string; expires_at: number; setup_token: string | null }>();
  if (row) {
    let token = row.setup_token;
    if (!token) {
      // A code made before the setup page: it gets its secret now.
      token = setupToken();
      await env.DB.prepare("UPDATE pairing SET setup_token = ? WHERE code = ?").bind(token, row.code).run();
    }
    return { code: row.code, expiresAt: row.expires_at, token };
  }
  const code = [...crypto.getRandomValues(new Uint8Array(6))]
    .map((b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
  const token = setupToken();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM pairing WHERE device_id = ? AND key_hash = ?").bind(id, keyHash),
    env.DB.prepare("INSERT INTO pairing (code, device_id, key_hash, report, expires_at, setup_token) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(code, id, keyHash, JSON.stringify(report), now + CODE_TTL_S, token),
  ]);
  return { code, expiresAt: now + CODE_TTL_S, token };
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

