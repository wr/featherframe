// The few pages the Worker draws itself (W-845): signing in. Everything past
// sign-in is the household's own page, drawn by its server.

import { CHART_SCRIPT, CHART_STYLE, chart, growth, serverByDay } from "./charts";
import { lastDays, serverTime, type Meter, type Usage, type UsageDay } from "./usage";
import { escapeHtml } from "./util";

const STYLE = `
  :root { --bg:#ececea; --surface:#fcfcfb; --ink:#201e1a; --ink-2:#474540; --muted:#827e76;
    --border:#e6e4dd; --border-strong:#d5d2ca; --field:#ffffff; --accent:#6b4a2c; --on-accent:#f7efe2; --ring:rgba(107,74,44,.24); --bad:#b6472e; --good:#5c8a46;
    --sh-card:0 1px 2px rgba(74,54,28,.045), 0 4px 12px rgba(74,54,28,.05); --sh-sm:0 1px 1px rgba(74,54,28,.05);
    color-scheme:light; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#171614; --surface:#211f1c; --ink:#ecebe7; --ink-2:#c9c6bf; --muted:#8f8b83;
    --border:#34312c; --border-strong:#4a463f; --field:#2c2b27; --accent:#b08a63; --on-accent:#1b140d; --ring:rgba(176,138,99,.3); --bad:#d9705a; --good:#7fae66;
    --sh-card:0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.28); --sh-sm:0 1px 1px rgba(0,0,0,.25); color-scheme:dark; } }
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
  input[type=text], input[type=email] { width:100%; font:inherit; padding:10px 12px; border:1px solid var(--border-strong);
    border-radius:8px; background:var(--field); color:var(--ink); box-shadow:var(--sh-sm); }
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
  /* An email or id breaks anywhere rather than push a row's actions out of
     the card, which clips them (W-914). */
  td { padding:10px 20px; border-top:1px solid var(--border); vertical-align:top; overflow-wrap:anywhere; }
  td.num { font-variant-numeric:tabular-nums; white-space:nowrap; }
  /* A household's server note is the longest line on the page: it wraps, so
     the row's actions stay inside the card (W-914). */
  td.num .srv-note { white-space:normal; }
  /* A frame's line and a household's id stay whole when the page has room. */
  @media (min-width:900px) { .frames li, .hid { white-space:nowrap; } }
  .muted { color:var(--muted); }
  button.linkish { display:inline; width:auto; margin:0; padding:0; border:0; border-radius:0; background:none;
    color:var(--accent); font-weight:400; text-decoration:underline; cursor:pointer; }
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
  /* Every row's actions sit in its last column (W-914); a household's rarer
     ones in a ⋯ menu, as the Frames card's in the webapp. */
  td.actions { width:1%; white-space:nowrap; }
  .row-actions { align-items:center; }
  .more-btn { width:30px; height:30px; margin:0; padding:0; display:inline-flex; align-items:center; justify-content:center;
    border:0; border-radius:6px; background:transparent; color:var(--muted); cursor:pointer; }
  .more-btn:hover, .more-btn[aria-expanded="true"] { background:color-mix(in srgb, var(--ink) 9%, transparent); color:var(--ink); }
  .more-btn:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
  .more-btn svg { width:16px; height:16px; fill:currentColor; }
  /* Fixed, placed under its button by script: the card clips to its radius. */
  .menu { position:fixed; z-index:20; min-width:180px; padding:4px; background:var(--surface);
    border:1px solid var(--border); border-radius:8px; box-shadow:var(--sh-card); }
  .menu[hidden] { display:none; }
  .menu form { margin:0; }
  .menu hr { border:0; border-top:1px solid var(--border); margin:4px 2px; }
  .menu button { display:block; width:100%; margin:0; text-align:left; font:inherit; font-size:13px; font-weight:400;
    color:var(--ink-2); background:none; border:0; border-radius:5px; padding:7px 10px; cursor:pointer; white-space:nowrap; }
  .menu button:hover, .menu button:focus-visible { background:var(--bg); color:var(--ink); outline:none; }
  .menu button.danger { color:var(--bad); }
  .dlg { width:min(440px, calc(100vw - 32px)); border:1px solid var(--border); border-radius:10px;
    background:var(--surface); color:var(--ink); box-shadow:var(--sh-card); padding:20px; }
  .dlg::backdrop { background:rgba(32,30,26,.45); }
  .dlg form { margin:0; }
  .dlg h2 { font-size:16px; font-weight:600; margin:0 0 8px; overflow-wrap:anywhere; }
  .dlg p { font-size:14px; margin:0 0 14px; }
  .dlg label { font-size:13px; color:var(--ink-2); overflow-wrap:anywhere; }
  .dlg input[type=text], .dlg input[type=email] { padding:9px 12px; }
  /* Cancel on the left, the action on the right, as the webapp's dialogs. */
  .dlg-foot { display:flex; justify-content:space-between; gap:8px; margin-top:18px; }
  .dlg-foot .btn { padding:9px 16px; font-size:14px; }
  .btn:disabled { opacity:.45; cursor:default; }
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
  @media (max-width:600px) { th:nth-child(n+3):not(.actions), td:nth-child(n+3):not(.actions) { display:none; }
    td, th { padding-left:14px; padding-right:14px; } }
  @media (max-width:820px) { .row-actions { flex-direction:column; align-items:flex-end; } }
`;

function page(title: string, body: string): Response {
  return shell(title, `<main><p class="wordmark">Featherframe</p><div class="card">${body}</div></main>`);
}

function shell(title: string, main: string, style = ""): Response {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(title)}</title>
<style>${STYLE}${style}</style></head><body>${main}</body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

/** The marketing page's form, answered without its script: the same page for
 * every address, new, pending or confirmed. */
export function waitlistThanksPage(error = "", status = 400): Response {
  if (error) {
    const res = page("Featherframe updates", `<h1>Featherframe updates</h1><p class="bad">${escapeHtml(error)}</p>
        <p><a href="https://featherframe.app">Back</a></p>`);
    return new Response(res.body, { status, headers: res.headers });
  }
  return page("Check your email · Featherframe", `<h1>Almost there</h1>
        <p>Check your email for a link to confirm.</p><p><a href="https://featherframe.app">Back</a></p>`);
}

export function waitlistConfirmedPage(): Response {
  return page("You're on the list · Featherframe", `<h1>You're on the list</h1>
    <p>I'll write once, when the frames have shipped.</p><p><a href="https://featherframe.app">Back to Featherframe</a></p>`);
}

export function waitlistExpiredPage(): Response {
  return page("Link expired · Featherframe", `<h1>That link has expired</h1>
    <p>Sign up again at <a href="https://featherframe.app">featherframe.app</a>.</p>`);
}

export function waitlistConfirmEmail(link: string): { subject: string; text: string; html: string } {
  const l = escapeHtml(link);
  return {
    subject: "Confirm your Featherframe updates",
    text: `Someone asked for Featherframe updates at this address. To confirm it was you, open this link:

${link}

I'll write once, when the frames have shipped.

If it wasn't you, ignore this email and you won't hear from me again. The link works for 7 days.

Wells`,
    html: `<p>Someone asked for Featherframe updates at this address. To confirm it was you:</p>
<p><a href="${l}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:#6b4a2c;color:#f7efe2;font-weight:600;text-decoration:none">Confirm</a></p>
<p>I'll write once, when the frames have shipped.</p>
<p style="color:#827e76">If it wasn't you, ignore this email and you won't hear from me again. The link works for 7 days.</p>
<p>Wells</p>`,
  };
}

// -- the admin page (W-850) -------------------------------------------------------

/** A household's AI spend this month, as the webapp's AI row reads (W-938). */
export function aiCell(ai: { usd: number; limit: number | null; paused: boolean; count: number }): string {
  if (!ai.count && !ai.paused) return "—";
  const money = `$${ai.usd.toFixed(2)}${ai.limit === null ? "" : ` of $${ai.limit.toFixed(2)}`}`;
  return ai.paused ? `Paused · ${money}` : money;
}

export type AdminData = {
  waitlist: { email: string; source: string | null; created_at: number; invited_at: number | null; confirmed_at: number | null }[];
  invites: { email: string; created_at: number; used_at: number | null }[];
  households: {
    id: string; created_at: number; email: string | null; paired: number;
    suspended_at: number | null;
    frames: { id: string; status: string; seen: number | null }[];
    usage: UsageDay[];
    last_wake: number | null; source: string | null;
    ai: { usd: number; limit: number | null; paused: boolean; count: number };
  }[];
  usage: Usage;
  log: { at: number; admin: string; action: string; target: string | null; ok: number; result: string }[];
  kits: { device_id: string; kit: string | null; note: string | null; registered_at: number; used_at: number | null; email: string | null }[];
  // When each began, in Unix seconds (W-923).
  growth: { signups: number[]; invites: number[]; households: number[] };
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
    ${u.days.length ? chart({
      label: "Cloudflare use by day, last 30 days", title: "Last 30 days", kind: "stack", unit: "usd",
      days: u.days.map((d) => d.day),
      series: [{ label: "Containers", color: "--r3", values: u.days.map((d) => d.containers) },
               { label: "Everything else", color: "--r1", values: u.days.map((d) => d.other) }] }) : ""}
    <div class="meters">${groups.map((g) => `<div><p class="meter-group">${e(g)}${g === "Containers" && !u.measured ? " · estimated" : ""}</p>
      ${u.meters.filter((m) => m.group === g).map(meter).join("")}</div>`).join("")}</div>
    <p class="muted" style="font-size:12px;margin:12px 0 0">Account-wide, against the Workers Paid allowances.
      ${u.days.length ? "The chart prices each day's use past them. " : ""}
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
  return !ms ? "0 min" : m < 1 ? "<1 min" : m < 90 ? `${Math.round(m)} min` : `${(m / 60).toFixed(1)} h`;
}

const DOTS = `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="8" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="13" cy="8" r="1.5"/></svg>`;

/** The page's ⋯ menus and dialogs: a menu is placed under its button and
 * closes on a click elsewhere, Escape, scroll or resize; a dialog closes on
 * Cancel, Escape or a click on its backdrop, and a typed confirmation keeps
 * its button off until the name matches. */
const ADMIN_SCRIPT = `<script>(function(){
  var open=null;
  function close(){if(!open)return;open.menu.hidden=true;open.btn.setAttribute("aria-expanded","false");open=null}
  document.querySelectorAll(".more-btn").forEach(function(btn){
    var menu=document.getElementById(btn.getAttribute("aria-controls"));
    btn.addEventListener("click",function(e){
      e.stopPropagation();var was=open&&open.menu===menu;close();if(was)return;
      var r=btn.getBoundingClientRect();
      menu.style.top=(r.bottom+4)+"px";
      menu.style.right=Math.max(8,document.documentElement.clientWidth-r.right)+"px";
      menu.hidden=false;btn.setAttribute("aria-expanded","true");open={btn:btn,menu:menu};
      var first=menu.querySelector("[role=menuitem]");if(first)first.focus();
    });
  });
  document.addEventListener("click",function(e){if(open&&!open.menu.contains(e.target))close()});
  window.addEventListener("scroll",close,{passive:true});
  window.addEventListener("resize",close);
  document.addEventListener("keydown",function(e){if(e.key==="Escape"&&open){var b=open.btn;close();b.focus()}});
  document.querySelectorAll("[data-dialog]").forEach(function(item){
    item.addEventListener("click",function(){
      close();var d=document.getElementById(item.getAttribute("data-dialog"));d.showModal();
      var i=d.querySelector("input:not([type=hidden])");if(i)i.focus();
    });
  });
  document.querySelectorAll("dialog.dlg").forEach(function(d){
    var form=d.querySelector("form"),go=d.querySelector("button[type=submit]"),match=d.querySelector("[data-match]");
    function check(){if(match)go.disabled=match.value.trim().toLowerCase()!==match.getAttribute("data-match").toLowerCase()}
    if(match)match.addEventListener("input",check);
    d.querySelectorAll("[data-close]").forEach(function(b){b.addEventListener("click",function(){d.close()})});
    d.addEventListener("close",function(){form.reset();check()});
    var outside=false;
    function out(e){var r=d.getBoundingClientRect();return e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom}
    d.addEventListener("mousedown",function(e){outside=e.target===d&&out(e)});
    d.addEventListener("click",function(e){if(outside&&e.target===d&&out(e))d.close();outside=false});
  });
})()</script>`;

export function adminPage(d: AdminData, toast: Toast | null, actingAs = false, ownHid: string | null = null): Response {
  const e = escapeHtml;
  // Confirmed addresses are the list; pending ones have not followed their
  // link yet (or tried to sign in uninvited) and are counted apart.
  const confirmed = d.waitlist.filter((w) => w.confirmed_at);
  const unconfirmed = d.waitlist.filter((w) => !w.confirmed_at);
  const hidden = (name: string, value: string) => `<input type="hidden" name="${name}" value="${e(value)}">`;
  const waitRows = (rows: AdminData["waitlist"]) => rows.map((w) => `<tr><td>${e(w.email)}</td>
      <td class="muted">${w.source === "login" ? "Sign-in" : w.source === "setup" ? "Frame setup" : "Website"}</td>
      <td class="num muted">${ago(w.created_at * 1000)}</td>
      <td class="actions"><div class="row-actions">
        <form method="post" action="/admin/waitlist/remove">${hidden("email", w.email)}<button class="btn plain" type="submit">Remove</button></form>
        <form method="post" action="/admin/invite">${hidden("email", w.email)}${hidden("send", "1")}<button class="btn" type="submit">Invite</button></form>
      </div></td></tr>`).join("");
  const waiting = (confirmed.length ? `<table><thead><tr><th>Confirmed</th><th>From</th><th>Asked</th><th class="actions"></th></tr></thead><tbody>
    ${waitRows(confirmed)}</tbody></table>` : `<p class="empty">Nobody is waiting.</p>`)
    + (unconfirmed.length ? `<table><thead><tr><th>Pending · ${unconfirmed.length}</th><th>From</th><th>Asked</th><th class="actions"></th></tr></thead><tbody>
    ${waitRows(unconfirmed)}</tbody></table>` : "");

  const pending = d.invites.filter((i) => !i.used_at);
  const invites = `<form class="invite" method="post" action="/admin/invite">
      <input type="email" name="email" placeholder="name@example.com" required aria-label="Email">
      <label><input type="checkbox" name="send" value="1" checked> Send email</label>
      <button class="btn" type="submit">Invite</button>
    </form>
    ${pending.length ? `<table><thead><tr><th>Invited, not signed up</th><th>Sent</th><th class="actions"></th></tr></thead><tbody>
      ${pending.map((i) => `<tr><td>${e(i.email)}</td><td class="num muted">${ago(i.created_at * 1000)}</td>
        <td class="actions"><div class="row-actions">
          <form method="post" action="/admin/invite/revoke">${hidden("email", i.email)}<button class="btn plain" type="submit">Revoke</button></form>
          <form method="post" action="/admin/invite/resend">${hidden("email", i.email)}<button class="btn plain" type="submit">Resend</button></form>
        </div></td></tr>`).join("")}
    </tbody></table>` : ""}`;

  const week = lastDays(7)[0];
  const days = lastDays(30);
  const srv = serverByDay(d.households, days);
  const serverChart = d.households.length ? chart({
    label: "Server time by day, last 30 days", title: "Server time, last 30 days", kind: "stack", unit: "ms", days,
    series: [{ label: "Wakes", color: "--r3", values: srv.wake },
             { label: "Page open", color: "--r1", values: srv.page },
             ...(srv.total.some((v) => v > 0) ? [{ label: "Total only", color: "--r-rest", values: srv.total }] : [])] }) : "";
  const households = d.households.length ? `<table><thead><tr><th>Household</th><th>Frames</th><th>Server, 7 days</th><th>AI this month</th><th>Last wake</th><th class="actions"></th></tr></thead><tbody>
    ${d.households.map((h, n) => {
      const t = serverTime(h.usage.filter((u) => u.day >= week));
      const note = [
        t.wakes ? `${Math.round(t.wake_ms / t.wakes / 1000)} s a wake` : "",
        t.page_ms ? `${minutes(t.page_ms)} on the page` : "page not opened",
        t.since ? `since ${new Date(`${t.since}T00:00:00Z`).toLocaleDateString("en-GB",
          { day: "numeric", month: "short", timeZone: "UTC" })}` : "",
      ].filter(Boolean).join(" · ");
      const frames = h.frames.length
        ? `<ul class="frames">${h.frames.map((f) => `<li>${e(f.id.slice(-6))} <span class="muted">· ${e(f.status)} · ${ago(f.seen)}</span></li>`).join("")}</ul>`
        : `<span class="muted">${h.paired ? `${h.paired} paired` : "none"}</span>`;
      const id = hidden("id", h.id);
      const who = h.email || h.id;
      const own = h.id === ownHid;
      const paired = h.paired === 1 ? ", and its frame shows a pairing code" : h.paired ? `, and its ${h.paired} frames show a pairing code` : "";
      // The admin's own household can't be deleted: the admin page goes with its login.
      const menu = `<div class="menu" id="hm-${n}" role="menu" hidden>
          <form method="post" action="/admin/household/${h.suspended_at ? "resume" : "suspend"}">${id}<button type="submit" role="menuitem">${h.suspended_at ? "Resume" : "Suspend"}</button></form>
          ${h.email ? `<button type="button" role="menuitem" data-dialog="he-${n}">Change email…</button>` : ""}
          ${own ? "" : `<hr><button type="button" role="menuitem" class="danger" data-dialog="hd-${n}">Delete…</button>`}
        </div>`;
      const dialogs = (h.email ? `<dialog class="dlg" id="he-${n}" aria-labelledby="he-${n}-t"><form method="post" action="/admin/household/email">${id}
          <h2 id="he-${n}-t">Change email</h2>
          <p>The login moves to the new address at once, with no link to confirm it.</p>
          <input type="email" name="email" required placeholder="New email" aria-label="New email">
          <div class="dlg-foot"><button class="btn plain" type="button" data-close>Cancel</button><button class="btn" type="submit">Change email</button></div>
        </form></dialog>` : "")
        + (own ? "" : `<dialog class="dlg" id="hd-${n}" aria-labelledby="hd-${n}-t"><form method="post" action="/admin/household/delete">${id}
          <h2 id="hd-${n}-t">Delete ${e(who)}?</h2>
          <p>Its login, server and data are deleted${paired}. This can't be undone.</p>
          <label for="hd-${n}-c">Type ${e(who)} to confirm</label>
          <input type="text" id="hd-${n}-c" name="confirm" required autocomplete="off" spellcheck="false" data-match="${e(who)}">
          <div class="dlg-foot"><button class="btn plain" type="button" data-close>Cancel</button><button class="btn danger" type="submit" disabled>Delete household</button></div>
        </form></dialog>`);
      const actions = `<div class="row-actions">
        ${h.email ? `<form method="post" action="/admin/household/as">${id}<button class="btn plain" type="submit">Log in as</button></form>` : ""}
        <button class="more-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="hm-${n}" aria-label="More for ${e(who)}">${DOTS}</button>
        ${menu}${dialogs}</div>`;
      return `<tr><td>${e(h.email || "(no login)")}${own ? ` <span class="muted">· you</span>` : ""}${h.suspended_at ? ` <span class="badge">Suspended</span>` : ""}<br><span class="muted hid">${e(h.id)}${h.source ? ` · ${e(h.source)}` : ""}</span></td>
        <td>${frames}</td>
        <td class="num">${minutes(t.wake_ms)} in ${t.wakes} wakes<br><span class="muted srv-note">${note}</span></td>
        <td>${e(aiCell(h.ai))}</td>
        <td class="num muted">${ago(h.last_wake)}</td>
        <td class="actions">${actions}</td></tr>`;
    }).join("")}
    </tbody></table>` : `<p class="empty">No households yet.</p>`;

  const kits = d.kits.length ? `<table><thead><tr><th>Kit</th><th>Note</th><th>Set up by</th><th>Registered</th></tr></thead><tbody>
    ${d.kits.map((k) => `<tr><td>${e((k.kit || "").toUpperCase())} <span class="muted">${e(k.device_id.slice(-6))}</span></td>
      <td class="muted">${e(k.note || "")}</td>
      <td>${k.used_at ? `${e(k.email || "(gone)")} <span class="muted">· ${ago(k.used_at * 1000)}</span>` : `<span class="muted">Not yet</span>`}</td>
      <td class="num muted">${ago(k.registered_at * 1000)}</td></tr>`).join("")}
    </tbody></table>` : `<p class="empty">No kits registered. Run firmware/tools/register_kit.py after flashing one.</p>`;

  const g = growth([d.growth.signups, d.growth.invites, d.growth.households]);
  const growthChart = g.days.length ? chart({
    label: "Sign-ups, invitations and households over time", kind: "line", unit: "count", days: g.days, latest: true,
    series: [{ label: "Sign-ups", color: "--r1", values: g.counts[0] },
             { label: "Invitations", color: "--r2", values: g.counts[1] },
             { label: "Households", color: "--r3", values: g.counts[2] }] })
    : `<p class="empty">Nothing yet.</p>`;

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
    <div class="card"><h2 class="sec-head">Growth</h2>${growthChart}</div>
    <div class="card"><h2 class="sec-head">Waitlist · ${confirmed.length}</h2>${waiting}</div>
    <div class="card"><h2 class="sec-head">Invite</h2>${invites}</div>
    <div class="card"><h2 class="sec-head">Households · ${d.households.length}</h2>${serverChart}${households}</div>
    <div class="card"><h2 class="sec-head">Kits · ${d.kits.length}</h2>${kits}</div>
    <div class="card"><h2 class="sec-head">Audit log</h2>${log}</div>
  </main>${ADMIN_SCRIPT}${CHART_SCRIPT}`, CHART_STYLE);
}

export function suspendedPage(): Response {
  return page("Suspended · Featherframe", `
    <h1>This account is suspended</h1>
    <form method="post" action="/logout"><button type="submit">Sign out</button></form>`);
}

export function loginPage(error = ""): Response {
  const e = escapeHtml;
  return page("Sign in · Featherframe", `
    <h1>Sign in</h1>
    ${error ? `<p class="bad">${e(error)}</p>` : ""}
    <form method="post" action="/login">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" autocomplete="email" required autofocus>
      <input type="hidden" name="tz" id="tz">
      <button type="submit">Email me a code</button>
    </form>
    <p class="muted" style="margin:16px 0 0"><a href="/setup">Set up a new frame</a></p>
    <script>try{document.getElementById("tz").value=Intl.DateTimeFormat().resolvedOptions().timeZone}catch(e){}</script>`);
}

/** A frame's six letters, typed (W-891): its own page since W-947. */
export function setupCodePage(error = "", code = ""): Response {
  const e = escapeHtml;
  return page("Set up a new frame · Featherframe", `
    <h1>Set up a new frame</h1>
    <p>Enter the code on your frame's screen.</p>
    ${error ? `<p class="bad">${e(error)}</p>` : ""}
    <form method="post" action="/setup">
      <label for="code">Code</label>
      <input type="text" id="code" name="code" required autocomplete="off" autocapitalize="characters" spellcheck="false"
        maxlength="9" placeholder="ABC-DEF" value="${e(code)}" autofocus style="text-transform:uppercase;letter-spacing:.08em">
      <button type="submit">Continue</button>
    </form>
    <p class="muted" style="margin:16px 0 0"><a href="/login">Sign in</a></p>`);
}

/** Where the code is typed (W-947). The same page for every address. */
export function codeEntryPage(v: { email: string; kind: "login" | "setup"; back: string; error: string; sent: boolean }): Response {
  const e = escapeHtml;
  const setup = v.kind === "setup";
  return page("Check your email · Featherframe", `
    <h1>Check your email</h1>
    <p>${setup ? `If ${e(v.email)} has a Featherframe Cloud account, we sent it a code that adds this frame.`
               : `If ${e(v.email)} has an invitation or an account, we sent it a code.`}</p>
    ${v.error ? `<p class="bad">${e(v.error)}</p>` : v.sent ? `<p>Sent a new code.</p>` : ""}
    <form method="post" action="/login/code" id="code-form">
      <label for="code">Code</label>
      <input type="text" id="code" name="code" inputmode="numeric" autocomplete="one-time-code"
        maxlength="12" required autofocus style="letter-spacing:.2em">
      <button type="submit">${setup ? "Add this frame" : "Sign in"}</button>
    </form>
    <p class="muted" style="margin:16px 0">The code and the link in the email work once, for 15 minutes.</p>
    ${setup ? `<p>Built this frame yourself? Featherframe Cloud is invite-only for now. <a href="https://featherframe.app">Join the waitlist</a>, and we'll email you an invitation.</p>` : ""}
    <form method="post" action="/login/resend" style="margin:0"><p style="margin:0"><button type="submit" class="linkish">Send a new code</button>
      · <a href="${e(v.back)}">Use a different email</a> · <a href="https://featherframe.app/help/account">Help</a></p></form>
    <script>(function(){var f=document.getElementById("code-form"),i=document.getElementById("code");
      i.addEventListener("input",function(){if(i.value.replace(/\\D/g,"").length===6){f.requestSubmit?f.requestSubmit():f.submit();}});
      function look(){if(document.hidden)return;fetch("/login/state",{credentials:"same-origin"})
        .then(function(r){return r.json();}).then(function(s){if(s.signedIn)location.replace("/");}).catch(function(){});}
      document.addEventListener("visibilitychange",look);window.addEventListener("focus",look);})();</script>`);
}

export function linkExpiredPage(): Response {
  return page("Link expired · Featherframe", `
    <h1>That link has expired</h1>
    <p>Sign-in links work once, for 15 minutes.</p>
    <p><a href="/login">Get a new link</a></p>`);
}

export function signInEmail(link: string, code: string): { subject: string; text: string; html: string } {
  const e = escapeHtml;
  return {
    subject: `Your Featherframe sign-in code: ${code}`,
    text: `Your sign-in code is ${code}.\n\nOr sign in with this link:\n${link}\n\nThe code and the link work once, for 15 minutes. If you didn't ask to sign in, ignore this email.`,
    html: `<p>Your sign-in code is <strong style="font-size:20px;letter-spacing:2px">${e(code)}</strong>.</p>
<p>Or <a href="${e(link)}">sign in with this link</a>.</p>
<p style="color:#827e76">The code and the link work once, for 15 minutes. If you didn't ask to sign in, ignore this email.</p>`,
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

// -- setting up a frame from the phone (W-888, W-889) ---------------------------------
const SETUP_STYLE = `<style>
  .hint { font-size:13px; color:var(--muted); margin:6px 0 0; }
  .field { margin-bottom:18px; }
  .code-in { text-transform:uppercase; letter-spacing:.08em; }
  fieldset { border:0; margin:0 0 6px; padding:0; }
  legend { font-size:15px; font-weight:600; margin:4px 0 8px; padding:0; }
  .choices, .stations { list-style:none; margin:0; padding:0; border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  .stations { margin-top:10px; }
  .stations:empty { display:none; }
  .choices li + li, .stations li + li { border-top:1px solid var(--border); }
  .choices label, .stations label { display:flex; gap:10px; align-items:flex-start; margin:0; padding:10px 12px;
    font-size:14px; color:var(--ink); cursor:pointer; }
  .choices input, .stations input { margin:3px 0 0; accent-color:var(--accent); flex:none; }
  .meta { display:block; font-size:12.5px; color:var(--muted); }
  .find { display:flex; gap:8px; margin-top:14px; }
  .find input { flex:1 1 auto; min-width:0; }
  .find button { margin:0; width:auto; flex:none; }
  .btn2 { margin-top:8px; width:100%; background:transparent; color:var(--ink-2); border:1px solid var(--border); font-weight:500; }
  .near { font-size:13px; color:var(--muted); margin:12px 0 0; }
  button:disabled { opacity:.6; cursor:default; }
  [hidden] { display:none !important; }
</style>`;

export interface SetupView {
  code: string; token: string; error?: string;
  email?: string; miles: boolean; place: string; country: string; source?: string;
}

export function setupPage(v: SetupView): Response {
  const e = escapeHtml;
  const action = `/setup/${e(v.code.toLowerCase())}/${e(v.token)}`;
  const src = v.source || "birdweather";
  const choice = (value: string, name: string, meta: string) => `<li><label>
    <input type="radio" name="source" value="${value}"${src === value ? " checked" : ""}>
    <span>${name}<span class="meta">${meta}</span></span></label></li>`;
  const us = v.country === "US";
  return page("Set up your frame · Featherframe", `${SETUP_STYLE}
    <h1>Set up your frame</h1>
    ${v.error ? `<p class="bad">${e(v.error)}</p>` : ""}
    <form method="post" action="${action}" id="setup">
      <div class="field">
        <label for="email">Email address</label>
        <input type="email" id="email" name="email" autocomplete="email" required value="${e(v.email || "")}">
        <p class="hint">This is the email you'll use to sign in to Featherframe to manage your frame and change settings.</p>
      </div>

      <fieldset>
        <legend>Detection source</legend>
        <ul class="choices">
          ${choice("birdweather", "A BirdWeather station near me", "No equipment needed")}
          ${choice("apprise", "My BirdNET-Pi", "You'll connect it in Settings after setup")}
          ${choice("birdnet_go", "My BirdNET-Go", "You'll connect it in Settings after setup")}
        </ul>
      </fieldset>
      <div id="bw"${src === "birdweather" ? "" : " hidden"}>
        <p class="hint">Choose a station near where the frame will hang.</p>
        <div class="find">
          <input type="text" id="place" autocomplete="postal-code" placeholder="${us ? "ZIP code or town" : "Postcode or town"}"
            aria-label="${us ? "ZIP code or town" : "Postcode or town"}" value="${e(v.place)}" enterkeyhint="search">
          <button type="button" id="findbtn">Find</button>
        </div>
        <button type="button" class="btn2" id="locate">Use my location</button>
        <p class="near" id="status" hidden></p>
        <ul class="stations" id="stations"></ul>
      </div>
      <input type="hidden" name="tz" id="tz">
      <input type="hidden" name="km" id="km">
      <button type="submit" id="go">Set up frame</button>
    </form>
    <script>
    (function () {
      var base = ${JSON.stringify(`/api/setup/stations?c=${v.code}&t=${v.token}`)}, miles = ${v.miles ? "true" : "false"};
      var $ = function (id) { return document.getElementById(id); };
      var list = $("stations"), status = $("status"), km = $("km"), place = $("place");
      try { $("tz").value = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) {}
      function say(text) { status.textContent = text; status.hidden = !text; }
      function dist(k) { return miles ? Math.round(k * 0.621371) + " mi" : k + " km"; }
      function show(b) {
        var rows = b.stations || [];
        list.innerHTML = ""; km.value = "";
        say(rows.length ? (b.place ? "Near " + b.place : "Near you")
          : (b.found === false ? "Couldn't find that place. Check the spelling, or try a ZIP code."
             : "No BirdWeather stations within about " + (miles ? "35 miles" : "55 km") + ". Try a town further away, or choose another detection source."));
        rows.forEach(function (s, i) {
          var li = document.createElement("li"), lab = document.createElement("label"), r = document.createElement("input");
          r.type = "radio"; r.name = "station"; r.value = s.id; r.checked = i === 0;
          r.onchange = function () { km.value = s.km; };
          var t = document.createElement("span"); t.textContent = s.name;
          var m = document.createElement("span"); m.className = "meta";
          m.textContent = [s.state, dist(s.km), s.species + " species in the last 7 days"].filter(Boolean).join(" · ");
          t.appendChild(m); lab.appendChild(r); lab.appendChild(t); li.appendChild(lab); list.appendChild(li);
          if (i === 0) km.value = s.km;
        });
        ready();
      }
      // With BirdWeather chosen, setting up waits for a station: without one
      // the frame would have no source at all.
      function ready() {
        var bw = document.querySelector("input[name=source]:checked");
        $("go").disabled = !!(bw && bw.value === "birdweather" && !document.querySelector("input[name=station]:checked"));
      }
      function load(q) {
        say("Finding stations…"); list.innerHTML = "";
        var x = new XMLHttpRequest();
        x.open("GET", base + q);
        x.onload = function () { try { show(JSON.parse(x.responseText)); } catch (e) { show({}); } };
        x.onerror = function () { say("Couldn't reach BirdWeather. Try again in a moment."); };
        x.send();
      }
      function find() { var p = place.value.trim(); if (p.length > 1) load("&place=" + encodeURIComponent(p)); }
      $("findbtn").onclick = find;
      place.onkeydown = function (ev) { if (ev.key === "Enter") { ev.preventDefault(); find(); } };
      $("locate").onclick = function () {
        if (!navigator.geolocation) { say("This browser can't share its location. Enter your ZIP code or town instead."); return; }
        say("Finding your location…");
        navigator.geolocation.getCurrentPosition(function (p) {
          place.value = "";
          load("&lat=" + p.coords.latitude.toFixed(3) + "&lon=" + p.coords.longitude.toFixed(3));
        }, function (err) {
          say(err && err.code === 1
            ? "Location is off for this browser. Turn it on in your phone's settings, or enter your ZIP code or town."
            : "Couldn't find your location. Enter your ZIP code or town instead.");
        }, { enableHighAccuracy: false, timeout: 20000, maximumAge: 600000 });
      };
      var radios = document.querySelectorAll("input[name=source]");
      for (var i = 0; i < radios.length; i++) radios[i].onchange = function () {
        $("bw").hidden = this.value !== "birdweather";
        ready();
      };
      ready();
      if (place.value) find();
      $("setup").onsubmit = function () { $("go").disabled = true; $("go").textContent = "Setting up…"; };
    })();
    </script>`);
}

export function setupAddPage(code: string, token: string, email: string): Response {
  const e = escapeHtml;
  return page("Add this frame · Featherframe", `
    <h1>Add this frame</h1>
    <p>You're signed in as ${e(email)}.</p>
    <form method="post" action="/setup/${e(code)}/${e(token)}">
      <button type="submit">Add this frame to your account</button>
    </form>`);
}

export function setupExpiredPage(): Response {
  return page("Code expired · Featherframe", `
    <h1>This code has expired</h1>
    <p>Your frame will show a new one within a minute. Scan that one.</p>`);
}

export function setupLimitedPage(): Response {
  return page("Try again later · Featherframe", `
    <h1>Too many tries</h1>
    <p>Try again in an hour.</p>`);
}

type Mail = { subject: string; text: string; html: string };

/** The first email (W-889, written to docs/STYLE.md). `station`: the
 * BirdWeather station chosen; null for the owner's own detector. */
export function welcomeEmail(station: { name: string; distance: string } | null): Mail {
  const e = escapeHtml;
  const opening = station
    ? `Your frame is set up. It shows the latest species heard by ${station.name}, a BirdWeather station ${station.distance ? `${station.distance} from you` : "near you"}, as a 19th-century illustration.`
    : "Your frame is set up. One step is left: connect your detector. Open the Featherframe webapp, go to Settings → Detection source and follow the steps there. Once it is connected, the frame shows the latest species your detector hears as a 19th-century illustration.";
  const first = station ? "Its first picture appears within a minute." : "";
  const more = "There you can name the frame and set its rotation and update interval, change the detection source, switch Content to Collage (every species heard today on one sheet), set quiet hours (overnight, every frame shows the day's collage), add an OpenAI API key under Settings → AI image generation for species with no historical illustration, and add more frames.";
  const signIn = "To sign in, enter your email. We send you a link. There is no password.";
  const help = "Help: featherframe.app/help";
  const text = [opening, first, "Everything else is in the Featherframe webapp: cloud.featherframe.app", more, signIn, help, "Featherframe"]
    .filter(Boolean).join("\n\n");
  const link = `<a href="https://cloud.featherframe.app">Featherframe webapp</a>`;
  const bold = (t: string) => e(t).replace(/Settings → (Detection source|AI image generation)/g, "<strong>$&</strong>");
  const openingHtml = station ? e(opening)
    : `Your frame is set up. <strong>One step is left: connect your detector.</strong> Open the ${link}, go to <strong>Settings → Detection source</strong> and follow the steps there. Once it is connected, the frame shows the latest species your detector hears as a 19th-century illustration.`;
  return {
    subject: "Welcome to Featherframe!",
    text,
    html: [`<p>${openingHtml}</p>`, first ? `<p>${e(first)}</p>` : "", `<p>Everything else is in the ${link}.</p>`,
           `<p>${bold(more)}</p>`, `<p>${e(signIn)}</p>`,
           `<p>Help: <a href="https://featherframe.app/help">featherframe.app/help</a></p>`, `<p>Featherframe</p>`].join("\n"),
  };
}

export function verifyEmail(link: string): Mail {
  const l = escapeHtml(link);
  return {
    subject: "Confirm your email for Featherframe",
    text: `Confirm this is your email address for Featherframe:\n\n${link}\n\nThe link works for 7 days. Confirming lets us know the address is yours.\n\nIf you did not set up a Featherframe, ignore this email.\n\nFeatherframe`,
    html: `<p>Confirm this is your email address for Featherframe:</p>
<p><a href="${l}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:#6b4a2c;color:#f7efe2;font-weight:600;text-decoration:none">Confirm email</a></p>
<p style="color:#827e76;word-break:break-all">${l}</p>
<p>The link works for 7 days. Confirming lets us know the address is yours.</p>
<p style="color:#827e76">If you did not set up a Featherframe, ignore this email.</p><p>Featherframe</p>`,
  };
}

export function verifiedPage(): Response {
  return page("Email confirmed · Featherframe", `
    <h1>Email confirmed</h1>
    <p>You can sign in on any device with this address.</p>
    <p><a href="/">Open the Featherframe webapp</a></p>`);
}

export function verifyExpiredPage(): Response {
  return page("Link expired · Featherframe", `
    <h1>That link has expired</h1>
    <p>Sign in, and send a new one from the notice at the top of the Featherframe webapp.</p>
    <p><a href="/">Open the Featherframe webapp</a></p>`);
}

export function addFrameEmail(link: string, frame: string, code: string): Mail {
  const e = escapeHtml;
  return {
    subject: `Your code to add a frame to Featherframe: ${code}`,
    text: `Someone scanned the code on a frame (${frame}) and asked to add it to your account. To add it and sign in, enter the code ${code}, or use this link:\n${link}\n\nThe code and the link work once, for 15 minutes. If this wasn't you, ignore this email.`,
    html: `<p>Someone scanned the code on a frame (${e(frame)}) and asked to add it to your account. To add it and sign in, enter the code <strong style="font-size:20px;letter-spacing:2px">${e(code)}</strong>, or <a href="${e(link)}">use this link</a>.</p>
<p style="color:#827e76">The code and the link work once, for 15 minutes. If this wasn't you, ignore this email.</p>`,
  };
}
