// The gate's entrypoint (W-937; the rules are gate.ts). Reached only through
// HouseholdServer's outbound interception, with the household in its props.

import { WorkerEntrypoint } from "cloudflare:workers";
import type { Env } from "./index";
import { type KeyAnswer, refuse, route, type Slot, SLOTS, toVendor } from "./gate";
import { unseal } from "./vault";

export class VendorGate extends WorkerEntrypoint<Env, { hid: string }> {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const r = route(url.hostname, request.method, url.pathname);
    if (!("vendor" in r)) return refuse(r.status, r.reason);
    const slot = request.headers.get("X-FF-Key") as Slot | null;
    if (!slot || !SLOTS.includes(slot)) return refuse(400, "no key named");
    const hid = this.ctx.props.hid;
    const paid = request.method === "POST";
    const got: KeyAnswer = await this.env.HOUSEHOLD.getByName(hid)
      .vendorKey(hid, slot, paid ? request.headers.get("X-FF-Purchase") || "" : null);
    if (!got.ok) return refuse(got.status, got.reason);
    let key: string;
    try {
      key = await unseal(this.env.AI_KEY_SECRET, got.sealed, `${hid}:${slot}`);
    } catch (e) {
      console.error("gate: a key would not open", hid, slot, String(e));
      return refuse(500, "key unreadable");
    }
    const res = await fetch(toVendor(request, r.vendor, key));
    return new Response(res.body, res);
  }
}
