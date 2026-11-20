// The owner's AI keys at rest (W-937): sealed with AES-GCM under the Worker
// secret AI_KEY_SECRET (32 random bytes, base64) and bound to the household
// and the key's slot, so a sealed key copied to another household or slot
// does not open. "v1:" names the scheme, so the secret can be changed later.

const enc = new TextEncoder();
const VERSION = "v1:";

function bytes(b64: string): Uint8Array {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}
function b64(b: Uint8Array): string {
  let s = "";
  for (const c of b) s += String.fromCharCode(c);
  return btoa(s);
}

async function aesKey(secret: string): Promise<CryptoKey> {
  const raw = bytes(secret || "");
  if (raw.length !== 32) throw new Error("AI_KEY_SECRET is not 32 bytes of base64");
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function seal(secret: string, plain: string, bound: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: enc.encode(bound) }, await aesKey(secret), enc.encode(plain)));
  const out = new Uint8Array(iv.length + ct.length);
  out.set(iv);
  out.set(ct, iv.length);
  return VERSION + b64(out);
}

/** The key, or a throw when the secret, the household or the slot is not the one it was sealed with. */
export async function unseal(secret: string, sealed: string, bound: string): Promise<string> {
  if (!sealed.startsWith(VERSION)) throw new Error("not a sealed key");
  const all = bytes(sealed.slice(VERSION.length));
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: all.slice(0, 12), additionalData: enc.encode(bound) }, await aesKey(secret), all.slice(12));
  return new TextDecoder().decode(plain);
}
