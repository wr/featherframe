// An exact rate limit for what signs in, sends mail or makes an account
// (W-890): one Durable Object per IP, counting in memory over a sliding
// minute. Cloudflare's rate-limit binding (RL_*) is permissive by design and
// let bursts of 40 through in testing; this one does not. An evicted object
// forgets its count, which errs toward letting a request through.

import { DurableObject } from "cloudflare:workers";

export class Limiter extends DurableObject {
  private hits: number[] = [];

  /** Count one more; false (not counted) once `limit` fall within `windowMs`. */
  async hit(limit: number, windowMs: number): Promise<boolean> {
    const now = Date.now();
    this.hits = this.hits.filter((t) => now - t < windowMs);
    if (this.hits.length >= limit) return false;
    this.hits.push(now);
    return true;
  }
}
