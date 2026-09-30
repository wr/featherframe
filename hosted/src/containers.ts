// The two Containers, both the one image (hosted/Dockerfile): each household's
// own server (W-844), and the lobby that draws a pairing code for a frame no
// household has claimed yet (W-845).

import { Container } from "@cloudflare/containers";
import type { Env } from "./index";

// How long a SIGTERM is given before the process is killed: uvicorn closes
// its port at once, waits out open connections, then the server's shutdown
// pushes the last changes to the front door (app.lifespan).
const STOP_GRACE_MS = 60 * 1000;
// A household server's own work (a tick and the push after it, a repaint)
// holds its Container up for at most this long: a tick stuck on a dead socket
// must not keep one running all night (W-917).
const MAX_WORK_S = 10 * 60;

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
  protected stopping?: Promise<void>;

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

  /** A request that finds the container on its way out waits for it to go
   * and starts it again. The library reads `running` once: while a process
   * is exiting it is still true, so the library skips the start, waits on
   * the port, sees the process gone and answers 500 "Failed to start
   * container: The container is not running, consider calling start()".
   * Nothing was sent to the server then, so asking again is safe for any
   * method; the body is still unread. */
  async fetch(request: Request): Promise<Response> {
    for (let attempt = 0; ; attempt++) {
      if (this.stopping) await this.stopping;
      const res = await super.fetch(request);
      if (attempt >= 2 || res.status !== 500 || request.bodyUsed) return res;
      const text = await res.clone().text();
      if (!text.startsWith("Failed to start container") || !text.includes("not running")) return res;
      console.warn(`container gone under a start; asking again (${attempt + 1})`);
      const c = this.ctx.container!;
      if (c.running) await Promise.race([c.monitor().catch(() => undefined), after(5000)]);
      for (let i = 0; c.running && i < 20; i++) await after(100);
    }
  }
}

export class HouseholdServer extends SleepingContainer {
  defaultPort = 8080;
  // A wake is one request (POST /api/hosted/run) answered when its tick is
  // done; after it the front door stops this once it is idle (W-847). The
  // page keeps it up while it is open.
  sleepAfter = "30s";

  /** The library's activity timeout runs from the last request proxied, not
   * from its end, and knows nothing of the server's own work: a wake's tick,
   * or the first tick after a page load, that outlived it was stopped part
   * way and lost, and the nightly collage with it (W-917). */
  override async onActivityExpired(): Promise<void> {
    await this.sleepWhenIdle();
  }

  /** Stop, unless the server is still working: then the activity timeout,
   * renewed by asking, comes back to it. */
  async sleepWhenIdle(): Promise<void> {
    if (!this.ctx.container?.running || this.stopping) return;
    if (await this.working()) return;
    await this.stop();
  }

  private async working(): Promise<boolean> {
    try {
      const res = await this.containerFetch(new Request("http://server/api/hosted/busy"), this.defaultPort);
      if (!res.ok) return false;
      const body = await res.json<{ busy?: boolean; for_s?: number }>();
      return body.busy === true && Number(body.for_s || 0) < MAX_WORK_S;
    } catch {
      return false;      // a server that cannot answer is not doing anything for us
    }
  }

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

  /** Its household was deleted (W-860): stop, and keep nothing. */
  async forget(): Promise<void> {
    await this.stop();
    await this.ctx.storage.deleteAlarm();
    await this.ctx.storage.deleteAll();
  }
}

export class Lobby extends SleepingContainer {
  defaultPort = 8080;
  sleepAfter = "2m";
  entrypoint = ["python", "-m", "featherframe.lobby", "--port", "8080"];
}
