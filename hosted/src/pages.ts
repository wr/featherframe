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
  main.wide { max-width:1040px; }
  main.wide .wordmark { font-size:36px; margin-bottom:20px; }
  /* Outgrown, a card scrolls sideways rather than clip a row's actions (W-951). */
  main.wide .card { padding:0; margin-bottom:16px; overflow-x:auto; }
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
  /* A pressed action (W-955): its button spins until the next page loads. */
  button.busy { cursor:progress; }
  button.busy::after { content:""; display:inline-block; width:.85em; height:.85em; margin-left:7px; vertical-align:-.12em;
    border:2px solid currentColor; border-right-color:transparent; border-radius:50%; animation:ff-spin .7s linear infinite; }
  .btn.busy:disabled { opacity:.75; }
  .more-btn.busy svg { display:none; }
  .more-btn.busy::after { margin:0; vertical-align:0; }
  @keyframes ff-spin { to { transform:rotate(360deg); } }
  @media (prefers-reduced-motion:reduce) { button.busy::after { animation-duration:2.4s; } }
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
  return page("You're signed up · Featherframe", `<h1>You're signed up</h1>
    <p><a href="https://featherframe.app">Back to Featherframe</a></p>`);
}

export function waitlistExpiredPage(): Response {
  return page("Link expired · Featherframe", `<h1>That link has expired</h1>
    <p>Sign up again at <a href="https://featherframe.app">featherframe.app</a>.</p>`);
}

export function waitlistConfirmEmail(link: string): { subject: string; text: string; html: string } {
  const l = escapeHtml(link);
  return {
    subject: "Confirm your email for Featherframe updates",
    text: `Someone signed up for Featherframe updates with this address. To confirm it was you, open this link:

${link}

If it wasn't you, ignore this email and you won't hear from us again. The link works for 7 days.

Featherframe`,
    html: `<p>Someone signed up for Featherframe updates with this address. To confirm it was you:</p>
<p><a href="${l}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:#6b4a2c;color:#f7efe2;font-weight:600;text-decoration:none">Confirm</a></p>
<p style="color:#827e76">If it wasn't you, ignore this email and you won't hear from us again. The link works for 7 days.</p>
<p>Featherframe</p>`,
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
    // The switches the server later chose otherwise, a running total (W-1025).
    mismatches: number;
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
 * its button off until the name matches. A pressed action's button (and a
 * menu item's ⋯) spins, and nothing else is sent until the next page loads
 * (W-955). */
const ADMIN_SCRIPT = `<script>(function(){
  var sent=false;
  function busy(b,on){if(!b)return;b.classList.toggle("busy",on);b.disabled=on;
    if(on)b.setAttribute("aria-busy","true");else b.removeAttribute("aria-busy")}
  document.addEventListener("submit",function(e){
    if(e.defaultPrevented)return;
    if(sent){e.preventDefault();return}
    sent=true;
    var b=e.submitter||e.target.querySelector("button[type=submit]"),menu=b&&b.closest(".menu");
    busy(b,true);
    if(menu)busy(document.querySelector('[aria-controls="'+menu.id+'"]'),true);
  });
  window.addEventListener("pageshow",function(e){
    if(!e.persisted)return;
    sent=false;document.querySelectorAll("button.busy").forEach(function(b){busy(b,false)});
    document.querySelectorAll("[data-match]").forEach(function(i){i.dispatchEvent(new Event("input"))});
  });
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
      const days7 = h.usage.filter((u) => u.day >= week);
      const t = serverTime(days7);
      // The pictures the front door switched to without a wake, over the same
      // days as the server time beside it (W-1025).
      const switches = days7.reduce((a, u) => a + (u.switches || 0), 0);
      const note = [
        t.wakes ? `${Math.round(t.wake_ms / t.wakes / 1000)} s a wake` : "",
        switches ? `${switches} switch${switches === 1 ? "" : "es"} without a wake` : "",
        h.mismatches ? `${h.mismatches} mismatch${h.mismatches === 1 ? "" : "es"}` : "",
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
const SETUP_STYLE = `
  /* Setting up from the phone (W-1073): full screens, as a phone app's setup
     is drawn. A welcome on the scene the marketing page tells it with, then
     one question a screen: a big title, a line, its field, and the button
     at the bottom. A column on a computer. */
  body { display:block; min-height:0; padding:0; }
  main.flow { max-width:440px; margin:0 auto; }
  .screen { display:flex; flex-direction:column; min-height:100vh; min-height:100svh; padding:0 24px; }
  .screen h1 { font-size:28px; font-weight:650; line-height:1.15; letter-spacing:-.01em; margin:0 0 10px; outline:none; text-wrap:balance; }
  .lede { font-size:15.5px; line-height:1.5; color:var(--ink-2); margin:0 0 22px; }
  .top { display:flex; align-items:center; justify-content:space-between; height:56px; margin:0 -8px 6px; }
  .count { font-size:13px; color:var(--muted); margin-right:8px; font-variant-numeric:tabular-nums; }
  button.backarrow { width:40px; height:40px; margin:0; padding:0; display:grid; place-items:center; background:none; color:var(--ink); border-radius:50%; }
  button.backarrow svg { width:22px; height:22px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
  .bottom { position:sticky; bottom:0; margin-top:auto; display:flex; flex-direction:column; align-items:center; gap:6px;
    padding:18px 0 calc(18px + env(safe-area-inset-bottom, 0px)); background:linear-gradient(to bottom, transparent, var(--bg) 22px); }
  /* The main button stands in the same place on every screen, link or not. */
  .bottom:not(:has(.link))::after { content:""; height:37px; }
  .bottom button[type=submit], .bottom [data-next] { margin:0; width:100%; padding:14px 12px; font-size:16px; border-radius:12px; }
  button.link { width:auto; margin:0; padding:8px 4px; background:none; border:0; color:var(--accent); font-weight:500; font-size:15px; }
  button:disabled { opacity:.5; cursor:default; }
  [hidden] { display:none !important; }
  .sr { position:absolute; width:1px; height:1px; margin:-1px; padding:0; overflow:hidden; clip:rect(0 0 0 0); border:0; }
  label { font-size:13px; color:var(--muted); margin:0 0 6px; }
  input[type=text], input[type=email] { padding:13px 14px; font-size:16px; border-radius:10px; }
  .hint { font-size:13.5px; color:var(--muted); margin:8px 0 0; }
  .bad { margin:16px 0 0; }

  /* The welcome: the scene, the wordmark, one line, Get started. */
  .welcome { padding:0; }
  .welcome .body { padding:0 24px; }
  .welcome .wordmark { font-size:30px; text-align:left; margin:0 0 10px; }
  .welcome .bottom { padding-left:24px; padding-right:24px; }
  .hero { position:relative; margin-bottom:30px; }
  .hero .media { aspect-ratio:4 / 3; overflow:hidden; background:#cfcdc8 url("/_ff/setup-photo.webp") center / cover no-repeat; }
  .hero video { display:block; width:100%; height:100%; object-fit:cover; }
  .hero .ping { position:absolute; left:14px; top:calc(14px + env(safe-area-inset-top, 0px)); display:flex; align-items:center; gap:10px;
    padding:8px 14px 8px 8px; max-width:calc(100% - 28px); background:#fff; border-radius:14px; box-shadow:0 8px 22px rgba(20,16,10,.25); color:#121212;
    transition:opacity .4s ease, transform .5s cubic-bezier(.2, .8, .2, 1); }
  .hero.live .ping { opacity:0; transform:translateY(-8px); }
  .hero.live .ping.on { opacity:1; transform:none; }
  .hero .ping .ic { flex:none; display:grid; place-items:center; width:34px; height:34px; border-radius:50%; background:#121212; }
  .hero .ping .ic svg { width:18px; height:18px; fill:none; stroke:#fff; stroke-width:2; stroke-linecap:round; }
  .hero .ping .tx { display:flex; flex-direction:column; min-width:0; }
  .hero .ping .k { font-size:12px; line-height:1.3; color:#6b6b69; }
  .hero .ping .nm { display:block; font-size:15px; font-weight:600; line-height:1.3; white-space:nowrap; }
  .hero .ping .nm.swap { animation:swapname .5s cubic-bezier(.2, .8, .2, 1); }
  @keyframes swapname { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }
  .hero .ping .when { align-self:flex-start; margin-left:10px; font-size:12px; line-height:1.3; color:#6b6b69; white-space:nowrap; }
  .hero .credit { position:absolute; left:12px; bottom:8px; max-width:calc(68% - 24px); font-size:10px; line-height:1.3; color:rgba(255,255,255,.88);
    text-shadow:0 1px 3px rgba(0,0,0,.7); }
  .hero .wall { position:absolute; right:20px; bottom:-46px; width:32%; max-width:138px; aspect-ratio:300 / 377; filter:drop-shadow(0 12px 18px rgba(20,16,10,.38)); }
  .hero .wall.gray { aspect-ratio:300 / 383; }
  .hero .wall > img { position:absolute; inset:0; width:100%; height:100%; display:block; }
  /* Only the screen repaints, as e-paper does: dark, then light, then the
     illustration. The walnut and the mat stay put. */
  .hero .wall .pane { position:absolute; left:17%; top:16.98%; width:66%; height:66.05%; overflow:hidden; background:#e8e7e3; }
  .hero .wall.gray .pane { left:18.67%; top:16.71%; width:62.67%; height:66.06%; }
  .hero .wall .pane img { position:absolute; inset:0; width:100%; height:100%; display:block; opacity:0; }
  .hero .wall .pane img.on { opacity:1; }
  .hero .wall .pane img.paint { animation:repaint .9s ease; }
  @keyframes repaint { 0% { opacity:0; } 22% { opacity:1; filter:brightness(.12); } 48% { filter:brightness(2.2) contrast(.35); } 100% { opacity:1; filter:none; } }
  @media (min-width:480px) { main.flow { padding-top:24px; } .hero { margin:0 24px 30px; } .hero .media { border-radius:16px; } }

  /* A station near you, or their own detector. */
  .find { display:flex; gap:8px; }
  .searchbox { position:relative; flex:1 1 auto; min-width:0; }
  .searchbox input { padding-right:44px; }
  button.loc { position:absolute; top:0; right:0; bottom:0; width:44px; margin:0; padding:0; display:flex; align-items:center; justify-content:center;
    background:none; color:var(--accent); border-radius:0 10px 10px 0; }
  button.loc svg { width:19px; height:19px; fill:currentColor; }
  .find > button { margin:0; width:auto; flex:none; padding:0 18px; border-radius:10px; }
  .near { font-size:13px; color:var(--muted); margin:16px 0 8px; }
  .oops { display:flex; gap:10px; margin:16px 0 10px; padding:12px 14px; border-radius:12px; font-size:14.5px; line-height:1.4; color:var(--ink);
    background:rgba(182,71,46,.08); background:color-mix(in srgb, var(--bad) 9%, var(--surface));
    border:1px solid rgba(182,71,46,.3); border-color:color-mix(in srgb, var(--bad) 32%, transparent); }
  .oops svg { flex:none; width:20px; height:20px; margin-top:1px; fill:none; stroke:var(--bad); stroke-width:2; stroke-linecap:round; }
  .oops b { display:block; font-weight:600; color:var(--bad); }
  .choices, .stations { list-style:none; margin:0; padding:0; border:1px solid var(--border); border-radius:12px; overflow:hidden; background:var(--surface); }
  .stations:empty { display:none; }
  .choices li + li, .stations li + li { border-top:1px solid var(--border); }
  .choices label, .stations label { display:flex; gap:12px; align-items:flex-start; margin:0; padding:14px; font-size:15.5px; color:var(--ink); cursor:pointer; }
  .choices input, .stations input { margin:3px 0 0; width:18px; height:18px; accent-color:var(--accent); flex:none; }
  .meta { display:block; margin-top:2px; font-size:13px; color:var(--muted); }
  .choices label:has(input:checked), .stations label:has(input:checked) { background:rgba(107,74,44,.06); background:color-mix(in srgb, var(--accent) 7%, var(--surface)); }
  .stations label { align-items:center; }
  .stations input { margin:0; }
  .stations .nm { flex:1 1 auto; min-width:0; }
  .stations .far { flex:none; margin-left:4px; text-align:right; font-size:17px; font-weight:600; line-height:1.15; font-variant-numeric:tabular-nums; }
  .stations .far small { display:block; font-size:12px; font-weight:400; color:var(--muted); }
  .closest { display:inline-block; margin-left:6px; padding:2px 8px; border-radius:999px; background:var(--accent); color:var(--on-accent);
    font-size:11.5px; font-weight:600; line-height:1.4; vertical-align:2px; white-space:nowrap; }
  .then { margin:18px 0 0; padding:14px 16px; border-radius:12px; background:var(--surface); border:1px solid var(--border);
    font-size:14.5px; line-height:1.45; color:var(--ink-2); animation:rise .35s ease; }
  .then p { margin:0 0 6px; font-weight:600; color:var(--ink); }
  .then ol { margin:0; padding-left:20px; }
  .then li + li { margin-top:4px; }
  @keyframes rise { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }
`;

export interface SetupView {
  code: string; token: string; error?: string;
  email?: string; miles: boolean; place: string; country: string; source?: string;
  /** Which step a re-shown page opens on: the one its error is about. */
  step?: "source" | "station" | "email";
  /** The kit asking is the colour one (EE02): the page shows that frame. */
  color?: boolean;
}

// Setting up from the phone (W-1073), for someone who has never heard of
// BirdNET: a welcome, a station near them, their email. Their own detector
// is a link off the station; "I already have an account" goes straight to the
// email. One form and one POST; without its script every screen shows.
export function setupPage(v: SetupView): Response {
  const e = escapeHtml;
  const action = `/setup/${e(v.code.toLowerCase())}/${e(v.token)}`;
  const src = v.source || "birdweather";
  const own = src !== "birdweather";
  const detector = (value: string, name: string) => `<li><label>
            <input type="radio" name="source" value="${value}" data-name="${name}"${src === value ? " checked" : ""}><span>${name}</span></label></li>`;
  const chosen = src === "apprise" ? "BirdNET-Pi" : src === "birdnet_go" ? "BirdNET-Go" : "";
  // The kit's own frame: the 13" colour one, or the 10" gray one.
  const kit = v.color ? "13" : "10";
  const us = v.country === "US";
  const start = v.step === "email" ? "email" : v.step ? "source" : "welcome";
  const back = `<button type="button" class="backarrow" data-back aria-label="Back"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg></button>`;
  const error = v.error ? `<p class="bad">${e(v.error)}</p>` : "";
  // The scene the marketing page tells it with, and its four birds: each one's
  // video, then the phone's "New detection" for it, then the frame, hanging
  // off the video's foot, repainting to its illustration. The page follows the
  // video's clock (six seconds a bird). With reduced motion, or no script, it
  // is the cardinal, still.
  // Each clip's credit as the marketing page gives it; three are CC BY-SA, so
  // the one playing is always named.
  const BIRDS: [string, string, string][] = [
    ["cardinal", "Northern Cardinal", "Video by Courtney Celley, U.S. Fish and Wildlife Service"],
    ["bluebird", "Eastern Bluebird", "Video by Paul Danese, Wikimedia Commons, CC BY-SA 4.0 (excerpt)"],
    ["titmouse", "Tufted Titmouse", "Video by Víctor Martínez, Wikimedia Commons, CC BY-SA 4.0 (excerpt)"],
    ["chickadee", "Black-capped Chickadee", "Video by Cephas, Wikimedia Commons, CC BY-SA 3.0 (excerpt)"]];
  const scene = `<div class="hero" id="hero" data-birds="${BIRDS.map((b) => b[1]).join("|")}" data-credits="${e(BIRDS.map((b) => b[2]).join("|"))}">
          <div class="media" aria-hidden="true"><video id="scene" muted playsinline loop preload="auto" poster="/_ff/setup-photo.webp" width="720" height="540"><source src="/_ff/setup-birds.mp4" type="video/mp4"></video></div>
          <span class="credit" id="credit">${BIRDS[0][2]}</span>
          <span class="ping" aria-hidden="true"><span class="ic"><svg viewBox="0 0 24 24"><path d="M3.5 10.5v3M7 7.5v9M10.5 4.5v15M14 8v8M17.5 9.5v5M21 11v2"/></svg></span><span class="tx"><span class="k">New detection</span><span class="nm" id="ping-nm">Northern Cardinal</span></span><span class="when">Just now</span></span>
          <span class="wall${v.color ? "" : " gray"}" aria-hidden="true"><span class="pane">${BIRDS.map(([slug], i) => `<img${i === 0 ? ' class="on"' : ""} src="/_ff/setup-screen-${kit}-${slug}.webp" alt="">`).join("")}</span><img src="/_ff/setup-frame-${kit}.webp" alt="" width="300" height="${v.color ? 377 : 383}"></span>
        </div>`;
  return shell("Set up your frame · Featherframe", `<main class="flow">
    <form method="post" action="${action}" id="setup" data-start="${start}">
      <section class="screen welcome" data-step="welcome">
        ${scene}
        <div class="body">
          <p class="wordmark">Featherframe</p>
          <h1 tabindex="-1">Hear a bird outside. See it on your frame.</h1>
          <p class="lede">Your frame will show the latest bird heard near you, as a 19th-century illustration. Setting it up takes about two minutes.</p>
        </div>
        <div class="bottom">
          <button type="button" data-next>Get started</button>
          <button type="button" class="link" id="have-account">I already have an account</button>
        </div>
      </section>
      <section class="screen" data-step="source">
        <div class="top">${back}<span class="count">1 of 2</span></div>
        ${v.step === "station" ? error : ""}
        <div id="bw"${own ? " hidden" : ""}>
          <input type="radio" name="source" value="birdweather" id="src-bw" class="sr"${own ? "" : " checked"} tabindex="-1" aria-hidden="true">
          <h1 tabindex="-1">Choose a station near you</h1>
          <p class="lede">Your frame will show the birds heard by a station near you. Stations are microphones that people set up at home and share on BirdWeather.</p>
          <div class="find">
            <div class="searchbox">
              <input type="text" id="place" autocomplete="postal-code" placeholder="${us ? "ZIP code or town" : "Postcode or town"}"
                aria-label="${us ? "ZIP code or town" : "Postcode or town"}" value="${e(v.place)}" enterkeyhint="search">
              <button type="button" class="loc" id="locate" aria-label="Use current location" title="Use current location"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 3.5 3.8 10.4c-.6.3-.6 1.2.1 1.4l6.4 1.9 1.9 6.4c.2.7 1.1.7 1.4.1z"/></svg></button>
            </div>
            <button type="button" id="findbtn">Find</button>
          </div>
          <p class="near" id="status" hidden></p>
          <div class="oops" id="oops" role="alert" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.6v.01"/></svg><div><b id="oops-t"></b><span id="oops-x"></span></div></div>
          <ul class="stations" id="stations"></ul>
        </div>
        <div id="own"${own ? "" : " hidden"}>
          <h1 tabindex="-1" id="own-q">Your BirdNET station</h1>
          <p class="lede">Your frame will show the birds your station hears. Which one do you have?</p>
          <ul class="choices" role="radiogroup" aria-labelledby="own-q">
            ${detector("apprise", "BirdNET-Pi")}
            ${detector("birdnet_go", "BirdNET-Go")}
          </ul>
          <div class="then" id="then" aria-live="polite"${chosen ? "" : " hidden"}>
            <p>What happens next</p>
            <ol>
              <li>You'll create your account.</li>
              <li>The Featherframe webapp will show you how to connect your <span id="then-nm">${chosen}</span>. It takes a few minutes.</li>
              <li>Your frame will show the next bird your station hears.</li>
            </ol>
          </div>
        </div>
        <div class="bottom">
          <button type="button" data-next>Continue</button>
          <button type="button" class="link" id="swap">${own ? "Use a station near me instead" : "I have my own BirdNET station"}</button>
        </div>
      </section>
      <section class="screen" data-step="email">
        <div class="top">${back}<span class="count" id="count2">2 of 2</span></div>
        ${v.step === "email" ? error : ""}
        <h1 tabindex="-1" id="acct-h">Create your account</h1>
        <label for="email">Email</label>
        <input type="email" id="email" name="email" autocomplete="email" required value="${e(v.email || "")}">
        <p class="hint" id="acct-hint">We'll send you a link to confirm your account. Your email address will never be shared or sold to anyone.</p>
        <input type="hidden" name="tz" id="tz">
        <input type="hidden" name="km" id="km">
        <div class="bottom"><button type="submit" id="go">Set up frame</button></div>
      </section>
    </form>
    </main>
    <script>
    (function () {
      var base = ${JSON.stringify(`/api/setup/stations?c=${v.code}&t=${v.token}`)}, miles = ${v.miles ? "true" : "false"};
      var $ = function (id) { return document.getElementById(id); };
      var form = $("setup"), list = $("stations"), status = $("status"), km = $("km"), place = $("place");
      try { $("tz").value = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) {}
      // A line of status, or a callout when something needs them to act.
      function say(text) { status.textContent = text; status.hidden = !text; $("oops").hidden = true; }
      function oops(title, text) { say(""); $("oops-t").textContent = title; $("oops-x").textContent = text; $("oops").hidden = false; }
      function dist(k) { return miles ? Math.round(k * 0.621371) + " mi" : k + " km"; }
      function source() { var r = document.querySelector("input[name=source]:checked"); return r ? r.value : ""; }
      function station() { return document.querySelector("input[name=station]:checked"); }
      function mine() { return !$("own").hidden; }

      // The screens, and where Back goes: "I already have an account" skips
      // the station, so its Back is the welcome.
      var screens = Array.prototype.slice.call(form.querySelectorAll("section.screen")), at = 0, signin = false;
      var vid = $("scene"), still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      // The scene: a bird every six seconds of the video; its card a moment
      // after it lands, its illustration on the frame a moment after that.
      var hero = $("hero"), names = hero.getAttribute("data-birds").split("|"), ping = hero.querySelector(".ping"), nm = $("ping-nm");
      var credits = hero.getAttribute("data-credits").split("|"), credit = $("credit"), playing = 0;
      var frames = hero.querySelectorAll(".pane img"), carded = -1, painted = 0;
      function paint(i) {
        if (i === painted) return;
        frames[painted].classList.remove("on", "paint"); frames[i].classList.add("on", "paint"); painted = i;
      }
      function follow() {
        var t = vid.currentTime || 0, bird = Math.floor(t / 6) % names.length, into = t % 6;
        if (bird !== playing) { playing = bird; credit.textContent = credits[bird]; }
        if (into >= 0.9 && carded !== bird) {
          carded = bird; nm.textContent = names[bird];
          nm.classList.remove("swap"); void nm.offsetWidth; nm.classList.add("swap"); ping.classList.add("on");
        }
        if (into >= 2 && carded === bird) paint(bird);
      }
      if (vid && !still) {
        hero.classList.add("live"); paint(names.length - 1); frames[0].classList.remove("on");
        frames[names.length - 1].classList.remove("paint");
        vid.addEventListener("timeupdate", follow);
      }
      function show(i, focus) {
        at = Math.max(0, Math.min(i, screens.length - 1));
        screens.forEach(function (s, k) { s.hidden = k !== at; });
        if (vid && !still) { if (at === 0) { var go = vid.play(); if (go && go.catch) go.catch(function () {}); } else vid.pause(); }
        ready();
        if (focus) { var h = screens[at].querySelector("h1:not([hidden])"), p = screens[at].querySelector("#bw:not([hidden]) h1, #own:not([hidden]) h1");
          (p || h).focus(); window.scrollTo(0, 0); }
      }
      function account(asSignin) {
        signin = asSignin;
        $("acct-h").textContent = signin ? "Sign in" : "Create your account";
        $("acct-hint").textContent = signin ? "We'll email you a code to add this frame." : "We'll send you a link to confirm your account. Your email address will never be shared or sold to anyone.";
        $("go").textContent = signin ? "Continue" : "Set up frame";
        $("count2").hidden = signin;
      }
      form.classList.add("stepped");
      form.addEventListener("click", function (ev) {
        var t = ev.target.closest ? ev.target.closest("button") : ev.target;
        if (!t) return;
        if (t.hasAttribute("data-next")) { if (!t.disabled) { if (at === 0) account(false); show(at + 1, true); } }
        else if (t.hasAttribute("data-back")) show(at === 2 && signin ? 0 : at - 1, true);
      });
      $("have-account").onclick = function () { account(true); show(2, true); };
      // Their own detector, or back to a station: the same screen, the other half.
      $("swap").onclick = function () {
        var to = !mine();
        $("bw").hidden = to; $("own").hidden = !to; $("src-bw").checked = !to;
        $("swap").textContent = to ? "Use a station near me instead" : "I have my own BirdNET station";
        ready(); (to ? $("own") : $("bw")).querySelector("h1").focus();
      };

      function rows(b) {
        var found = b.stations || [];
        list.innerHTML = ""; km.value = "";
        if (found.length) say(b.place ? "Near " + b.place : "Near you");
        else if (b.found === false) oops("Couldn't find that place", "Check the spelling, or try a ZIP code.");
        else oops("No stations nearby", "There are none within about " + (miles ? "35 miles" : "55 km") + ". Try a larger town nearby.");
        // Nearest first: the first is tagged, and each says how far it is.
        found.forEach(function (s, i) {
          var li = document.createElement("li"), lab = document.createElement("label"), r = document.createElement("input");
          r.type = "radio"; r.name = "station"; r.value = s.id; r.checked = i === 0;
          r.onchange = function () { km.value = s.km; ready(); };
          var t = document.createElement("span"); t.className = "nm"; t.textContent = s.name;
          if (i === 0 && found.length > 1) { var tag = document.createElement("span"); tag.className = "closest"; tag.textContent = "Closest"; t.appendChild(tag); }
          var m = document.createElement("span"); m.className = "meta";
          m.textContent = s.species + (s.species === 1 ? " kind of bird" : " kinds of birds") + " this week";
          var f = document.createElement("span"); f.className = "far"; f.textContent = dist(s.km);
          var a = document.createElement("small"); a.textContent = "away"; f.appendChild(a);
          t.appendChild(m); lab.appendChild(r); lab.appendChild(t); lab.appendChild(f); li.appendChild(lab); list.appendChild(li);
          if (i === 0) km.value = s.km;
        });
        ready();
      }
      // The station screen's Continue waits for its choice: a station, or a detector.
      function ready() {
        var next = form.querySelector("[data-step=source] [data-next]");
        next.disabled = mine() ? !(source() === "apprise" || source() === "birdnet_go") : !station();
      }
      function load(q) {
        say("Finding stations…"); list.innerHTML = ""; ready();
        var x = new XMLHttpRequest();
        x.open("GET", base + q);
        x.onload = function () { try { rows(JSON.parse(x.responseText)); } catch (e) { rows({}); } };
        x.onerror = function () { oops("Couldn't find stations just now", "Check your connection, then try again."); };
        x.send();
      }
      function find() { var p = place.value.trim(); if (p.length > 1) load("&place=" + encodeURIComponent(p)); }
      $("findbtn").onclick = find;
      place.onkeydown = function (ev) { if (ev.key === "Enter") { ev.preventDefault(); find(); } };
      $("locate").onclick = function () {
        if (!navigator.geolocation) { oops("This browser can't share your location", "Enter your ZIP code or town instead."); return; }
        say("Finding your location…");
        navigator.geolocation.getCurrentPosition(function (p) {
          place.value = "";
          load("&lat=" + p.coords.latitude.toFixed(3) + "&lon=" + p.coords.longitude.toFixed(3));
        }, function (err) {
          if (err && err.code === 1) oops("Location is off", "Turn on location for this browser in your phone's settings, or enter your ZIP code or town.");
          else oops("Couldn't find your location", "Enter your ZIP code or town instead.");
        }, { enableHighAccuracy: false, timeout: 20000, maximumAge: 600000 });
      };
      var radios = document.querySelectorAll("input[name=source]");
      for (var i = 0; i < radios.length; i++) radios[i].onchange = function () {
        var n = this.getAttribute("data-name");
        if (n) { $("then-nm").textContent = n; $("then").hidden = false; }
        ready();
      };
      // Enter before the last screen goes on; it never sends a half-filled form.
      form.addEventListener("submit", function (ev) {
        if (at < 2) { ev.preventDefault(); var n = screens[at].querySelector("[data-next]"); if (n && !n.disabled) show(at + 1, true); return; }
        $("go").disabled = true; $("go").textContent = signin ? "Sending…" : "Setting up…";
      });
      var first = form.getAttribute("data-start");
      show(first === "email" ? 2 : first === "source" ? 1 : 0, first !== "welcome");
      if (place.value) find();
    })();
    </script>`, SETUP_STYLE);
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
  const first = station ? "Its first picture will appear within a minute." : "";
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
