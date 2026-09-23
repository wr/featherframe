// The two Containers, both the one image (hosted/Dockerfile): each household's
// own server (W-844), and the lobby that draws a pairing code for a frame no
// household has claimed yet (W-845).

import { Container } from "@cloudflare/containers";
import type { Env } from "./index";

export class HouseholdServer extends Container<Env> {
  defaultPort = 8080;
  // A wake is one request (POST /api/hosted/run) answered when its tick is
  // done; after it the front door stops this at once (W-847). The page keeps
  // it up while it is open.
  sleepAfter = "30s";

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      const vars = await ctx.storage.get<Record<string, string>>("vars");
      if (vars) this.envVars = vars;
    });
  }

  /** The household it serves: its front door's address and key, its zone. */
  async configure(vars: Record<string, string>): Promise<void> {
    this.envVars = vars;
    await this.ctx.storage.put("vars", vars);
  }
}

export class Lobby extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = "2m";
  entrypoint = ["python", "-m", "featherframe.lobby", "--port", "8080"];
}
