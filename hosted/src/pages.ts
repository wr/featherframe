// The few pages the Worker draws itself (W-845): signing in. Everything past
// sign-in is the household's own page, drawn by its server.

import type { Meter, Usage } from "./usage";
import { escapeHtml } from "./util";

const STYLE = `
  :root { --bg:#ececea; --surface:#fcfcfb; --ink:#201e1a; --ink-2:#474540; --muted:#827e76;
    --border:#e6e4dd; --accent:#6b4a2c; --on-accent:#f7efe2; --ring:rgba(107,74,44,.24); --bad:#b6472e; --good:#5c8a46;
    --sh-card:0 1px 2px rgba(74,54,28,.045), 0 4px 12px rgba(74,54,28,.05);
    color-scheme:light; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#171614; --surface:#211f1c; --ink:#ecebe7; --ink-2:#c9c6bf; --muted:#8f8b83;
    --border:#34312c; --accent:#b08a63; --on-accent:#1b140d; --ring:rgba(176,138,99,.3); --bad:#d9705a; --good:#7fae66;
    --sh-card:0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.28); color-scheme:dark; } }
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
  .badge { display:inline-block; font-size:11px; font-weight:600; padding:1px 6px; border-radius:4px;
    background:var(--bad); color:#fff; vertical-align:1px; margin-left:4px; }
  .h-actions { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-top:8px; }
  .h-actions form { margin:0; }
  .h-actions details { font-size:13px; }
  .h-actions summary { cursor:pointer; color:var(--ink-2); list-style:none; padding:6px 4px; }
  .h-actions summary::-webkit-details-marker { display:none; }
  .h-actions details[open] { flex-basis:100%; }
  .h-more { display:grid; gap:10px; padding:8px 0 4px; }
  .h-more form { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  .h-more input { flex:1 1 180px; font:inherit; font-size:13px; padding:6px 10px; border:1px solid var(--border);
    border-radius:8px; background:var(--bg); color:var(--ink); }
  .btn.danger { background:var(--bad); color:#fff; }
  /* The page's toast (W-863): the Featherframe page's own flash, pinned to the
     top of the viewport and gone after five seconds. */
  .toast { position:fixed; top:16px; left:50%; z-index:40; display:flex; align-items:flex-start; gap:9px;
    width:max-content; max-width:min(560px, calc(100vw - 32px)); padding:9px 12px; border-radius:8px;
    font-size:13px; line-height:1.45; border:1px solid var(--border); color:var(--ink); box-shadow:var(--sh-card);
    transform:translateX(-50%); animation:toast-in .3s cubic-bezier(.2,.7,.2,1);
    transition:opacity .3s, transform .3s; }
  .toast.ok { background:color-mix(in srgb, var(--good) 12%, var(--surface)); border-color:color-mix(in srgb, var(--good) 32%, var(--border)); }
  .toast.bad { background:color-mix(in srgb, var(--bad) 11%, var(--surface)); border-color:color-mix(in srgb, var(--bad) 30%, var(--border)); }
  .toast svg { flex:none; width:18px; height:18px; margin-top:1px; }
  .toast.ok svg { color:var(--good); } .toast.bad svg { color:var(--bad); }
  .toast.gone { opacity:0; transform:translate(-50%, -120%); }
  @keyframes toast-in { from { transform:translate(-50%, -120%); opacity:0; } to { transform:translate(-50%, 0); opacity:1; } }
  @media (prefers-reduced-motion:reduce) { .toast { animation:none; transition:none; } }
  .log-bad { color:var(--bad); }
  .usage { padding:0 20px 18px; }
  .usage-total { display:flex; gap:24px; flex-wrap:wrap; margin:0 0 14px; }
  .usage-total div { font-size:13px; color:var(--muted); }
  .usage-total strong { display:block; font-size:22px; font-weight:600; color:var(--ink); font-variant-numeric:tabular-nums; }
  .meters { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:14px 24px; }
  .meter-group { font-size:12px; font-weight:600; color:var(--muted); margin:0 0 6px; }
  .meter { display:grid; grid-template-columns:1fr auto; gap:2px 8px; font-size:13px; margin-bottom:8px; }
  .meter .v { font-variant-numeric:tabular-nums; color:var(--ink-2); }
  .meter .track { grid-column:1 / -1; height:6px; border-radius:3px; background:var(--border); overflow:hidden; }
  .meter .fill { height:100%; background:var(--accent); }
  .meter.warn .fill { background:#c28a2c; } .meter.over .fill { background:var(--bad); }
  .meter .over-cost { grid-column:1 / -1; font-size:12px; color:var(--bad); }
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
        <p>We'll email ${escapeHtml(email)} when there's news.</p><p><a href="https://featherframe.app">Back</a></p>`);
}

// -- the admin page (W-850) -------------------------------------------------------
export type AdminData = {
  waitlist: { email: string; source: string | null; created_at: number; invited_at: number | null }[];
  invites: { email: string; created_at: number; used_at: number | null }[];
  households: {
    id: string; created_at: number; email: string | null; paired: number;
    suspended_at: number | null;
    frames: { id: string; status: string; seen: number | null }[];
    usage: { day: string; wakes: number; server_ms: number }[];
    last_wake: number | null; source: string | null;
  }[];
  usage: Usage;
  log: { at: number; admin: string; action: string; target: string | null; ok: number; result: string }[];
};

export type Toast = { ok: boolean; message: string };

const TOAST_ICONS = {
  ok: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>`,
  bad: `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.9 15 14.4H1L8 1.9Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.3v3.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="8" cy="11.75" r=".9" fill="currentColor"/></svg>`,
};

function toastHtml(t: Toast | null): string {
  if (!t) return "";
  return `<div class="toast ${t.ok ? "ok" : "bad"}" id="toast" role="status" aria-live="polite">
    ${t.ok ? TOAST_ICONS.ok : TOAST_ICONS.bad}<span>${escapeHtml(t.message)}</span></div>
    <script>setTimeout(function(){var t=document.getElementById("toast");if(t){t.classList.add("gone");
      setTimeout(function(){t.remove()},400)}},5000)</script>`;
}

/** 1.2M, 340k, 12.5, 0.03 */
function qty(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(a >= 1e10 ? 0 : 1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e4) return `${Math.round(n / 1e3)}k`;
  if (a >= 100) return String(Math.round(n));
  if (a >= 1) return n.toFixed(1).replace(/\.0$/, "");
  return a === 0 ? "0" : n.toFixed(a >= 0.01 ? 2 : 3);
}

const dollars = (n: number) => `$${n.toFixed(2)}`;

function usageCard(u: Usage): string {
  const e = escapeHtml;
  const groups = [...new Set(u.meters.map((m) => m.group))];
  const meter = (m: Meter) => {
    if (m.used === null) {
      return `<div class="meter"><span>${e(m.label)}</span><span class="v muted">unavailable</span></div>`;
    }
    const pct = m.included ? (m.used / m.included) * 100 : 0;
    const over = Math.max(0, m.used - m.included) * m.price;
    const cls = pct >= 100 ? "over" : pct >= 80 ? "warn" : "";
    const unit = m.unit ? ` ${e(m.unit)}` : "";
    return `<div class="meter ${cls}"><span>${e(m.label)}</span>
      <span class="v">${qty(m.used)} / ${qty(m.included)}${unit}</span>
      <div class="track"><div class="fill" style="width:${Math.min(100, pct).toFixed(1)}%"></div></div>
      ${over >= 0.005 ? `<span class="over-cost">${dollars(over)} over</span>` : ""}</div>`;
  };
  const month = new Date(`${u.month}-01T00:00:00Z`).toLocaleDateString("en-GB", { month: "long", timeZone: "UTC" });
  return `<div class="usage">
    <div class="usage-total">
      <div><strong>${dollars(u.bill)}</strong>${e(month)} so far</div>
      <div><strong>${dollars(u.projected)}</strong>at this pace</div>
    </div>
    <div class="meters">${groups.map((g) => `<div><p class="meter-group">${e(g)}${g === "Containers" ? " · our count" : ""}</p>
      ${u.meters.filter((m) => m.group === g).map(meter).join("")}</div>`).join("")}</div>
    <p class="muted" style="font-size:12px;margin:12px 0 0">Account-wide, against the Workers Paid allowances.
      ${u.live ? "" : "Only the containers are counted until the <code>CF_API_TOKEN</code> secret is set. "}
      The bill itself: <a href="https://dash.cloudflare.com/?to=/:account/billing/billable-usage">Billable usage</a>.</p>
  </div>`;
}

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

export function adminPage(d: AdminData, toast: Toast | null, actingAs = false): Response {
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
      ${pending.map((i) => `<tr><td>${e(i.email)}</td>
        <td><div class="row-actions">
          <form method="post" action="/admin/invite/revoke"><input type="hidden" name="email" value="${e(i.email)}"><button class="btn plain" type="submit">Revoke</button></form>
          <form method="post" action="/admin/invite/resend"><input type="hidden" name="email" value="${e(i.email)}"><button class="btn plain" type="submit">Resend</button></form>
        </div></td><td></td><td class="num muted">${ago(i.created_at * 1000)}</td></tr>`).join("")}
    </tbody></table>` : ""}`;

  const households = d.households.length ? `<table><thead><tr><th>Household</th><th>Frames</th><th>Server, today · 7 days</th><th>Last wake</th></tr></thead><tbody>
    ${d.households.map((h) => {
      const today = h.usage.find((u) => u.day === new Date().toISOString().slice(0, 10));
      const week = h.usage.reduce((a, u) => a + u.server_ms, 0);
      const wakes = h.usage.reduce((a, u) => a + u.wakes, 0);
      const frames = h.frames.length
        ? `<ul class="frames">${h.frames.map((f) => `<li>${e(f.id.slice(-6))} <span class="muted">· ${e(f.status)} · ${ago(f.seen)}</span></li>`).join("")}</ul>`
        : `<span class="muted">${h.paired ? `${h.paired} paired` : "none"}</span>`;
      const id = `<input type="hidden" name="id" value="${e(h.id)}">`;
      const who = h.email || h.id;
      const actions = `<div class="h-actions">
        ${h.email ? `<form method="post" action="/admin/household/as">${id}<button class="btn" type="submit">Log in as</button></form>` : ""}
        <form method="post" action="/admin/household/${h.suspended_at ? "resume" : "suspend"}">${id}<button class="btn plain" type="submit">${h.suspended_at ? "Resume" : "Suspend"}</button></form>
        <details><summary>More</summary><div class="h-more">
          ${h.email ? `<form method="post" action="/admin/household/email">${id}
            <input type="email" name="email" required placeholder="New email" aria-label="New email">
            <button class="btn plain" type="submit">Change email</button></form>` : ""}
          <form method="post" action="/admin/household/delete">${id}
            <input type="text" name="confirm" required autocomplete="off" placeholder="Type ${e(who)}" aria-label="Type ${e(who)} to delete">
            <button class="btn danger" type="submit">Delete</button></form>
        </div></details></div>`;
      return `<tr><td>${e(h.email || "(no login)")}${h.suspended_at ? ` <span class="badge">Suspended</span>` : ""}<br><span class="muted">${e(h.id)}${h.source ? ` · ${e(h.source)}` : ""}</span>${actions}</td>
        <td>${frames}</td>
        <td class="num">${minutes(today?.server_ms || 0)} · ${minutes(week)}<br><span class="muted">${wakes} wakes in 7 days</span></td>
        <td class="num muted">${ago(h.last_wake)}</td></tr>`;
    }).join("")}
    </tbody></table>` : `<p class="empty">No households yet.</p>`;

  const log = d.log.length ? `<table><thead><tr><th>Action</th><th>By</th><th>When</th></tr></thead><tbody>
    ${d.log.map((l) => `<tr><td><span class="${l.ok ? "" : "log-bad"}">${e(l.result)}</span><br>
        <span class="muted">${e(l.action)}${l.target ? ` · ${e(l.target)}` : ""}</span></td>
      <td class="muted">${e(l.admin)}</td>
      <td class="num muted" title="${new Date(l.at * 1000).toISOString()}">${ago(l.at * 1000)}</td></tr>`).join("")}
    </tbody></table>` : `<p class="empty">Nothing yet.</p>`;

  return shell("Admin · Featherframe", `<main class="wide"><p class="wordmark">Featherframe</p>
    ${toastHtml(toast)}
    ${actingAs ? `<form class="note" method="post" action="/admin/as/stop" style="display:flex;gap:12px;align-items:center;justify-content:space-between">
      <span>You are logged in as a household.</span><button class="btn" type="submit">Stop</button></form>` : ""}
    <div class="card"><h2 class="sec-head">Cloudflare usage</h2>${usageCard(d.usage)}</div>
    <div class="card"><h2 class="sec-head">Waitlist · ${d.waitlist.length}</h2>${waiting}</div>
    <div class="card"><h2 class="sec-head">Invite</h2>${invites}</div>
    <div class="card"><h2 class="sec-head">Households · ${d.households.length}</h2>${households}</div>
    <div class="card"><h2 class="sec-head">Audit log</h2>${log}</div>
  </main>`);
}

export function suspendedPage(): Response {
  return page("Suspended · Featherframe", `
    <h1>This account is suspended</h1>
    <form method="post" action="/logout"><button type="submit">Sign out</button></form>`);
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

export function confirmEmailEmail(link: string): { subject: string; text: string; html: string } {
  return {
    subject: "Confirm your Featherframe email",
    text: `Use this address to sign in to Featherframe:\n\n${link}\n\nThe link works once, for a day. If you didn't ask for it, ignore this email.`,
    html: `<p>Use this address to sign in to Featherframe:</p><p><a href="${escapeHtml(link)}">Confirm</a></p>
<p style="color:#827e76">The link works once, for a day. If you didn't ask for it, ignore this email.</p>`,
  };
}
