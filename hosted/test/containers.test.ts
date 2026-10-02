// A stop() that returns only once the process is gone, and a fetch() that
// waits that out (src/containers.ts). The library is stood in for: its own
// stop() signals and returns at once, and its fetch() proxies blindly while
// the container is up — the gap this covers.
import { describe, expect, it, vi } from "vitest";

type Fake = {
  running: boolean; exit: () => void;
  monitor: () => Promise<void>; signal: (n: number) => void; destroy: () => Promise<void>;
  signals: number[]; destroyed: number;
};

function fakeContainer(): Fake {
  let resolve: () => void = () => {};
  const exited = new Promise<void>(res => { resolve = res; });
  const c: Fake = {
    running: true, signals: [], destroyed: 0,
    exit() { c.running = false; resolve(); },
    monitor: () => exited,
    signal(n) { c.signals.push(n); },
    async destroy() { c.destroyed++; c.exit(); },
  };
  return c;
}

vi.mock("@cloudflare/containers", () => {
  class Container {
    ctx: { container: Fake };
    envVars = {};
    fetched: string[] = [];
    constructor(ctx: { container: Fake }) { this.ctx = ctx; }
    async stop(signal?: string | number) {
      if (this.ctx.container.running) this.ctx.container.signal(typeof signal === "number" ? signal : 15);
    }
    async fetch(request: Request) {
      // The library proxies to a running container without a readiness check.
      this.fetched.push(this.ctx.container.running ? "running" : "started");
      return new Response("ok");
    }
    // What the server says when it is asked whether it is working (W-917).
    busy: (() => Response) = () => Response.json({ busy: false, for_s: 0 });
    asked = 0;
    async containerFetch(_request: Request) {
      this.asked++;
      return this.busy();
    }
  }
  return { Container };
});

const { HouseholdServer, Lobby } = await import("../src/containers");

function household(c: Fake) {
  const ctx = {
    container: c,
    blockConcurrencyWhile: (fn: () => Promise<void>) => fn(),
    storage: { get: async () => undefined },
  };
  return new HouseholdServer(ctx as never, {} as never) as unknown as InstanceType<typeof HouseholdServer> & {
    busy: () => Response; asked: number;
  };
}

describe("SleepingContainer", () => {
  it("stop() returns once the process has exited", async () => {
    const c = fakeContainer();
    const box = new Lobby({ container: c } as never, {} as never);
    let done = false;
    const stopping = box.stop().then(() => { done = true; });
    await after(10);
    expect(c.signals).toEqual([15]);
    expect(done).toBe(false);
    c.exit();
    await stopping;
    expect(done).toBe(true);
  });

  it("a request during the shutdown waits for it, then is served afresh", async () => {
    const c = fakeContainer();
    const box = new Lobby({ container: c } as never, {} as never);
    const stopping = box.stop();
    let answered = false;
    const res = box.fetch(new Request("http://server/api/status")).then(r => { answered = true; return r; });
    await after(10);
    expect(answered).toBe(false);          // not proxied into a closing port
    c.exit();
    await stopping;
    expect((await res).status).toBe(200);
    expect((box as unknown as { fetched: string[] }).fetched).toEqual(["started"]);
  });

  it("a second stop() joins the first", async () => {
    const c = fakeContainer();
    const box = new Lobby({ container: c } as never, {} as never);
    const a = box.stop();
    const b = box.stop();
    await after(10);
    expect(c.signals).toEqual([15]);
    c.exit();
    await Promise.all([a, b]);
  });

  it("stop() on a container already gone returns at once", async () => {
    const c = fakeContainer();
    c.running = false;
    const box = new Lobby({ container: c } as never, {} as never);
    await box.stop();
    expect(c.signals).toEqual([]);
  });
});

// The activity timeout runs from a request's start, so a tick longer than it
// was stopped part way (W-917): the server is stopped only once it is idle.
describe("HouseholdServer", () => {
  it("says it is running only while up and not on its way out", async () => {
    const c = fakeContainer();
    const h = household(c);
    expect(await h.running()).toBe(true);
    const stopping = h.stop();
    expect(await h.running()).toBe(false);
    c.exit();
    await stopping;
    expect(await h.running()).toBe(false);
  });

  it("is not stopped while the server is working", async () => {
    const c = fakeContainer();
    const box = household(c);
    box.busy = () => Response.json({ busy: true, for_s: 40 });
    await box.onActivityExpired();
    expect(box.asked).toBe(1);
    expect(c.signals).toEqual([]);
  });

  it("is stopped once the server is idle", async () => {
    const c = fakeContainer();
    const box = household(c);
    const stopping = box.onActivityExpired();
    await after(10);
    expect(c.signals).toEqual([15]);
    c.exit();
    await stopping;
  });

  it("is stopped when the work has run past its cap", async () => {
    const c = fakeContainer();
    const box = household(c);
    box.busy = () => Response.json({ busy: true, for_s: 11 * 60 });
    const stopping = box.sleepWhenIdle();
    await after(10);
    expect(c.signals).toEqual([15]);
    c.exit();
    await stopping;
  });

  it("is stopped when the server cannot say", async () => {
    const c = fakeContainer();
    const box = household(c);
    box.busy = () => new Response("not listening", { status: 500 });
    const stopping = box.sleepWhenIdle();
    await after(10);
    expect(c.signals).toEqual([15]);
    c.exit();
    await stopping;
  });

  it("never starts a server that has gone just to ask it", async () => {
    const c = fakeContainer();
    c.running = false;
    const box = household(c);
    await box.sleepWhenIdle();
    expect(box.asked).toBe(0);
    expect(c.signals).toEqual([]);
  });
});

const after = (ms: number) => new Promise<void>(res => setTimeout(res, ms));
