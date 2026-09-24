// Pairing codes (W-845, W-849): what a device no household has claimed shows,
// kits and viewers alike.

import type { Env } from "./index";

// A pairing code: letters only (the engraved face has old-style figures that
// rise and fall), and none of I/L/O/U/V to confuse on the glass.
const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZ";
const CODE_TTL_S = 24 * 60 * 60;

/** The code a device no one has claimed shows, made on its first ask and
 * kept for a day. `report` is what it said about itself, for the household
 * that claims it: a kit's own headers, or a viewer's (W-849). */
// Bumped when the Lobby draws a pairing screen differently: a new ETag (and
// R2 key), so a screen showing the old drawing is sent the new one. Bump it
// again once the Lobby's rollout has finished: a code asked for mid-rollout
// is drawn by the old image and cached under the new key.
export const LOBBY_DRAWING = "boot-art-2";

export async function pairingCode(env: Env, id: string, keyHash: string,
                                  report: Record<string, unknown>): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const row = await env.DB.prepare("SELECT code FROM pairing WHERE device_id = ? AND key_hash = ? AND expires_at > ?")
    .bind(id, keyHash, now).first<{ code: string }>();
  if (row) return row.code;
  const code = [...crypto.getRandomValues(new Uint8Array(6))]
    .map((b) => CODE_ALPHABET[b % CODE_ALPHABET.length]).join("");
  await env.DB.batch([
    env.DB.prepare("DELETE FROM pairing WHERE device_id = ? AND key_hash = ?").bind(id, keyHash),
    env.DB.prepare("INSERT INTO pairing (code, device_id, key_hash, report, expires_at) VALUES (?, ?, ?, ?, ?)")
      .bind(code, id, keyHash, JSON.stringify(report), now + CODE_TTL_S),
  ]);
  return code;
}

