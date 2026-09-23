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
`;

function page(title: string, body: string): Response {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(title)}</title>
<style>${STYLE}</style></head><body><main><p class="wordmark">Featherframe</p><div class="card">${body}</div></main></body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
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
