// The two Containers, both the one image (hosted/Dockerfile): each household's
// own server (W-844), and the lobby that draws a pairing code for a frame no
// household has claimed yet (W-845).

import { Container } from "@cloudflare/containers";
import type { Env } from "./index";

// How long a SIGTERM is given before the process is killed: uvicorn closes
// its port at once, waits out open connections, then the server's shutdown
// pushes the last changes to the front door (app.lifespan).
const STOP_GRACE_MS = 60 * 1000;

const after = (ms: number) => new Promise<void>(res => setTimeout(res, ms));

/** A Container whose stop() returns once the process has gone, and whose
 * fetch() waits that out rather than proxying into it.
 *
 * The library's stop() signals the process and returns at once, and the
 * Durable Object goes on holding the container "healthy" until the process
 * has exited (0.0.30 and 0.3.7 alike). A request in that window skips the
 * readiness check and is proxied to a port uvicorn closed on the signal:
 * 500 "Error proxying request to container: The container is not listening
 * in the TCP address …:8080". Seen on the page right after a wake stopped
 * the server (W-847) and when its activity timeout ran out under a page. */
class SleepingContainer extends Container<Env> {
  private stopping?: Promise<void>;

  async stop(signal?: Parameters<Container<Env>["stop"]>[0]): Promise<void> {
    const c = this.ctx.container!;
    if (!c.running) return;
    if (!this.stopping) {
      this.stopping = (async () => {
        const exited = c.monitor().catch(() => undefined);
        await super.stop(signal);
        await Promise.race([exited, after(STOP_GRACE_MS)]);
        if (c.running) {
          console.warn("container did not exit in time; destroying it");
          await c.destroy();
          await exited;
        }
        // The runtime clears `running` on exit; give it a beat, never a wait.
        for (let i = 0; c.running && i < 20; i++) await after(100);
      })().finally(() => { this.stopping = undefined; });
    }
    await this.stopping;
  }

  async fetch(request: Request): Promise<Response> {
    if (this.stopping) await this.stopping;
    return super.fetch(request);
  }
}

export class HouseholdServer extends SleepingContainer {
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

export class Lobby extends SleepingContainer {
  defaultPort = 8080;
  sleepAfter = "2m";
  entrypoint = ["python", "-m", "featherframe.lobby", "--port", "8080"];
}
