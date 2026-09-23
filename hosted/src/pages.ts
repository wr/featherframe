// The few pages the Worker draws itself (W-845): signing in. Everything past
// sign-in is the household's own page, drawn by its server.

import { escapeHtml } from "./util";

const STYLE = `
  :root { --bg:#ececea; --surface:#fcfcfb; --ink:#201e1a; --ink-2:#474540; --muted:#827e76;
    --border:#e6e4dd; --accent:#6b4a2c; --on-accent:#f7efe2; --ring:rgba(107,74,44,.24); --bad:#b6472e;
    color-scheme:light; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#171614; --surface:#211f1c; --ink:#ecebe7; --ink-2:#c9c6bf; --muted:#8f8b83;
    --border:#34312c; --accent:#b08a63; --on-accent:#1b140d; --ring:rgba(176,138,99,.3); color-scheme:dark; } }
  @font-face { font-family:"Featherframe Script"; src:url("/_ff/script.ttf") format("truetype"); font-display:swap; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:16px;
    background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  main { width:100%; max-width:380px; }
  .wordmark { font-family:"Featherframe Script",Georgia,serif; font-size:48px; line-height:1; text-align:center; margin:0 0 28px; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:24px; }
  h1 { font-size:17px; font-weight:600; margin:0 0 6px; }
  p { margin:0 0 16px; color:var(--ink-2); }
  label { display:block; font-size:13px; color:var(--muted); margin-bottom:6px; }
  input[type=email] { width:100%; font:inherit; padding:10px 12px; border:1px solid var(--border); border-radius:8px;
    background:var(--bg); color:var(--ink); }
  input:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px var(--ring); }
  button { margin-top:14px; width:100%; font:inherit; font-weight:600; padding:10px 12px; border:0; border-radius:8px;
    background:var(--accent); color:var(--on-accent); cursor:pointer; }
  a { color:var(--accent); }
  .bad { color:var(--bad); }
  main.wide { max-width:880px; }
  main.wide .wordmark { font-size:36px; margin-bottom:20px; }
  main.wide .card { padding:0; margin-bottom:16px; overflow:hidden; }
  .sec-head { font-size:12px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:var(--muted);
    margin:0; padding:16px 20px 8px; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th { text-align:left; font-weight:500; font-size:12px; color:var(--muted); padding:6px 20px; }
  td { padding:10px 20px; border-top:1px solid var(--border); vertical-align:top; }
  td.num { font-variant-numeric:tabular-nums; white-space:nowrap; }
  .muted { color:var(--muted); }
  .empty { padding:4px 20px 18px; color:var(--muted); margin:0; }
  .row-actions { display:flex; gap:8px; justify-content:flex-end; }
  .row-actions form { margin:0; }
  .btn { width:auto; margin:0; padding:6px 12px; font-size:13px; }
  .btn.plain { background:transparent; color:var(--ink-2); border:1px solid var(--border); }
  .note { margin:0 0 16px; padding:10px 14px; border-radius:8px; background:var(--surface); border:1px solid var(--border); }
  .invite { display:flex; gap:8px; padding:4px 20px 18px; align-items:center; flex-wrap:wrap; }
  .invite input[type=email] { flex:1 1 220px; }
  .invite label { display:flex; gap:6px; align-items:center; margin:0; font-size:13px; }
  .frames { margin:0; padding:0; list-style:none; }
  @media (max-width:600px) { th:nth-child(n+3), td:nth-child(n+3) { display:none; } td, th { padding-left:14px; padding-right:14px; } }
`;

function page(title: string, body: string): Response {
  return shell(title, `<main><p class="wordmark">Featherframe</p><div class="card">${body}</div></main>`);
}

function shell(title: string, main: string): Response {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(title)}</title>
<style>${STYLE}</style></head><body>${main}</body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

export function waitlistThanksPage(email: string, error = ""): Response {
  return error
    ? page("Waitlist · Featherframe", `<h1>Join the waitlist</h1><p class="bad">${escapeHtml(error)}</p>
        <p><a href="https://featherframe.app">Back</a></p>`)
    : page("You're on the list · Featherframe", `<h1>You're on the list</h1>
        <p>We'll email ${escapeHtml(email)} when there's room.</p><p><a href="https://featherframe.app">Back</a></p>`);
}

// -- the admin page (W-850) -------------------------------------------------------
export type AdminData = {
  waitlist: { email: string; source: string | null; created_at: number; invited_at: number | null }[];
  invites: { email: string; created_at: number; used_at: number | null }[];
  households: {
    id: string; created_at: number; email: string | null; paired: number;
    frames: { id: string; status: string; seen: number | null }[];
    usage: { day: string; wakes: number; server_ms: number }[];
    last_wake: number | null; source: string | null;
  }[];
};

/** "4 min ago", "3 h ago", "12 Sep". */
function ago(ms: number | null): string {
  if (!ms) return "never";
  const s = (Date.now() - ms) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ms).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

function minutes(ms: number): string {
  const m = ms / 60000;
  return m < 1 ? "<1 min" : m < 90 ? `${Math.round(m)} min` : `${(m / 60).toFixed(1)} h`;
}

export function adminPage(d: AdminData, message: string): Response {
  const e = escapeHtml;
  const waiting = d.waitlist.length ? `<table><thead><tr><th>Email</th><th></th><th>From</th><th>Asked</th></tr></thead><tbody>
    ${d.waitlist.map((w) => `<tr><td>${e(w.email)}</td>
      <td><div class="row-actions">
        <form method="post" action="/admin/waitlist/remove"><input type="hidden" name="email" value="${e(w.email)}"><button class="btn plain" type="submit">Remove</button></form>
        <form method="post" action="/admin/invite"><input type="hidden" name="email" value="${e(w.email)}"><input type="hidden" name="send" value="1"><button class="btn" type="submit">Invite</button></form>
      </div></td>
      <td class="muted">${w.source === "login" ? "Sign-in" : "Website"}</td><td class="num muted">${ago(w.created_at * 1000)}</td></tr>`).join("")}
    </tbody></table>` : `<p class="empty">Nobody is waiting.</p>`;

  const pending = d.invites.filter((i) => !i.used_at);
  const invites = `<form class="invite" method="post" action="/admin/invite">
      <input type="email" name="email" placeholder="name@example.com" required aria-label="Email">
      <label><input type="checkbox" name="send" value="1" checked> Send email</label>
      <button class="btn" type="submit">Invite</button>
    </form>
    ${pending.length ? `<table><thead><tr><th>Invited, not signed up</th><th></th><th></th><th>Sent</th></tr></thead><tbody>
      ${pending.map((i) => `<tr><td>${e(i.email)}</td><td></td><td></td><td class="num muted">${ago(i.created_at * 1000)}</td></tr>`).join("")}
    </tbody></table>` : ""}`;

  const households = d.households.length ? `<table><thead><tr><th>Household</th><th>Frames</th><th>Server, today · 7 days</th><th>Last wake</th></tr></thead><tbody>
    ${d.households.map((h) => {
      const today = h.usage.find((u) => u.day === new Date().toISOString().slice(0, 10));
      const week = h.usage.reduce((a, u) => a + u.server_ms, 0);
      const wakes = h.usage.reduce((a, u) => a + u.wakes, 0);
      const frames = h.frames.length
        ? `<ul class="frames">${h.frames.map((f) => `<li>${e(f.id.slice(-6))} <span class="muted">· ${e(f.status)} · ${ago(f.seen)}</span></li>`).join("")}</ul>`
        : `<span class="muted">${h.paired ? `${h.paired} paired` : "none"}</span>`;
      return `<tr><td>${e(h.email || "(no login)")}<br><span class="muted">${e(h.id)}${h.source ? ` · ${e(h.source)}` : ""}</span></td>
        <td>${frames}</td>
        <td class="num">${minutes(today?.server_ms || 0)} · ${minutes(week)}<br><span class="muted">${wakes} wakes in 7 days</span></td>
        <td class="num muted">${ago(h.last_wake)}</td></tr>`;
    }).join("")}
    </tbody></table>` : `<p class="empty">No households yet.</p>`;

  return shell("Admin · Featherframe", `<main class="wide"><p class="wordmark">Featherframe</p>
    ${message ? `<p class="note">${e(message)}</p>` : ""}
    <div class="card"><h2 class="sec-head">Waitlist · ${d.waitlist.length}</h2>${waiting}</div>
    <div class="card"><h2 class="sec-head">Invite</h2>${invites}</div>
    <div class="card"><h2 class="sec-head">Households · ${d.households.length}</h2>${households}</div>
  </main>`);
}

export function loginPage(error = ""): Response {
  return page("Sign in · Featherframe", `
    <h1>Sign in</h1>
    ${error ? `<p class="bad">${escapeHtml(error)}</p>` : ""}
    <form method="post" action="/login">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" autocomplete="email" required autofocus>
      <input type="hidden" name="tz" id="tz">
      <button type="submit">Email me a link</button>
    </form>
    <script>try{document.getElementById("tz").value=Intl.DateTimeFormat().resolvedOptions().timeZone}catch(e){}</script>`);
}

export function checkEmailPage(email: string): Response {
  return page("Check your email · Featherframe", `
    <h1>Check your email</h1>
    <p>If ${escapeHtml(email)} has an invitation or an account, a sign-in link is on its way. It works once, for 15 minutes.</p>
    <p><a href="/login">Use a different email</a></p>`);
}

export function linkExpiredPage(): Response {
  return page("Link expired · Featherframe", `
    <h1>That link has expired</h1>
    <p>Sign-in links work once, for 15 minutes.</p>
    <p><a href="/login">Get a new link</a></p>`);
}

export function signInEmail(link: string): { subject: string; text: string; html: string } {
  return {
    subject: "Sign in to Featherframe",
    text: `Sign in to Featherframe:\n\n${link}\n\nThe link works once, for 15 minutes. If you didn't ask for it, ignore this email.`,
    html: `<p>Sign in to Featherframe:</p><p><a href="${escapeHtml(link)}">Sign in</a></p>
<p style="color:#827e76">The link works once, for 15 minutes. If you didn't ask for it, ignore this email.</p>`,
  };
}

export function inviteEmail(link: string): { subject: string; text: string; html: string } {
  return {
    subject: "You're invited to Featherframe",
    text: `You're invited to Featherframe. Sign in with this email to start:\n\n${link}`,
    html: `<p>You're invited to Featherframe. Sign in with this email to start:</p><p><a href="${escapeHtml(link)}">${escapeHtml(link)}</a></p>`,
  };
}
