// Which limiter a request counts against (W-890); see rateLimited in index.ts.

import { deviceId } from "./util";

// -- rate limits (W-890) -------------------------------------------------------
// Anything that signs in, sends an email, makes an account or claims a
// frame is RL_AUTH; a frame and a detector are keyed by who they are, so a
// household behind one IP is not one bucket; the rest is RL_PAGE.
const AUTH_PATHS = /^\/(login|auth|logout|account\/|admin\/|_admin\/|api\/pair|api\/waitlist)/i;

export function limiterFor(request: Request, url: URL, isFramePath: (path: string) => boolean): { name: "RL_AUTH" | "RL_PAGE" | "RL_FRAME"; key: string } {
  const path = url.pathname;
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (AUTH_PATHS.test(path) && !(path.toLowerCase() === "/login" && request.method === "GET")
      && !(path === "/admin" && request.method === "GET")) return { name: "RL_AUTH", key: `ip:${ip}` };
  if (/^\/setup(\/|$)/i.test(path) && request.method === "POST") return { name: "RL_AUTH", key: `ip:${ip}` };
  if (isFramePath(path)) {
    const who = deviceId(request) || url.searchParams.get("id") || request.headers.get("ID") || ip;
    return { name: "RL_FRAME", key: `frame:${who}` };
  }
  const push = path.match(/^\/api\/ingest\/[^/]+\/([^/]+)$/);
  if (push) return { name: "RL_FRAME", key: `push:${push[1]}` };
  return { name: "RL_PAGE", key: `ip:${ip}` };
}

