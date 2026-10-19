// The webapp before there is a copy of it (W-946): its header and its cards
// as blocks on its field, no text. It asks the front door whether the page is
// ready and reloads with its own query and hash once it is. It takes the
// webapp's theme, the footer's Light / Dark / System kept as `ff-theme` in
// this browser, and its colours (`--bg`, `--surface` in index.html), so
// nothing changes at the swap (W-1075).

const HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark"><title>Featherframe</title>
<script>(function(){try{var t=localStorage.getItem('ff-theme');if(t==='dark'||t==='light')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>
<style>
  :root { --bg:#ececea; --block:#fcfcfb; color-scheme:light; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#1a1916; --block:#232220; color-scheme:dark; } }
  :root[data-theme="dark"] { --bg:#1a1916; --block:#232220; color-scheme:dark; }
  body { margin:0; background:var(--bg); }
  header { height:64px; max-width:1180px; margin:0 auto; padding:0 16px; display:flex; align-items:center; }
  .mark { width:150px; height:30px; border-radius:6px; background:var(--block); }
  main { max-width:1180px; margin:0 auto; padding:8px 16px 32px; display:grid; gap:20px; grid-template-columns:minmax(0,380px) minmax(0,1fr); }
  @media (max-width:760px) { main { grid-template-columns:1fr; } }
  .b { background:var(--block); border-radius:12px; animation:p 1.6s ease-in-out infinite; }
  .col { display:grid; gap:20px; align-content:start; }
  @keyframes p { 50% { opacity:.55; } }
  @media (prefers-reduced-motion:reduce) { .b { animation:none; } }
</style></head><body>
<header><div class="mark b"></div></header>
<main><div class="col"><div class="b" style="aspect-ratio:3/4"></div><div class="b" style="height:120px"></div></div>
<div class="col"><div class="b" style="height:220px"></div><div class="b" style="height:320px"></div></div></main>
<script>(function(){function look(){fetch("/api/page/ready",{credentials:"same-origin"}).then(function(r){return r.json();})
.then(function(s){if(s.ready)location.reload();else setTimeout(look,2000);}).catch(function(){setTimeout(look,2000);});}
setTimeout(look,2000);})();</script></body></html>`;

export function loadingPage(): Response {
  return new Response(HTML, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}
