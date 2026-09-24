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
  }
  return { Container };
});

const { Lobby } = await import("../src/containers");

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

const after = (ms: number) => new Promise<void>(res => setTimeout(res, ms));
