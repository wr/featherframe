// The admin page's charts (W-923): spend, server time and growth, one column
// per UTC day. Drawn on the server as HTML (columns, gridlines, labels) with an
// SVG layer for lines, so they fit any width with no library; the page's one
// script adds the hover readout. Every chart has a table under it.

import { escapeHtml } from "./util";
import { lastDays, type UsageDay } from "./usage";

export type Unit = "usd" | "ms" | "count";
export type Series = { label: string; color: string; values: number[] };
export type ChartSpec = {
  label: string;               // what is plotted, for a screen reader
  kind: "stack" | "line";
  days: string[];              // YYYY-MM-DD, oldest first
  series: Series[];            // a stack's first series sits on the baseline
  unit: Unit;
  latest?: boolean;            // say each series' last value in the legend
};

const DAY = 86400e3;

/** Every household's server time by day: wakes, the time a page kept a server
 * up, and a day from before the two were counted apart (W-907) as its total. */
export function serverByDay(households: { usage: UsageDay[] }[], days: string[]):
    { wake: number[]; page: number[]; total: number[] } {
  const at = new Map(days.map((d, i) => [d, i]));
  const wake = days.map(() => 0), page = days.map(() => 0), total = days.map(() => 0);
  for (const h of households) {
    for (const u of h.usage) {
      const i = at.get(u.day);
      if (i === undefined) continue;
      wake[i] += u.wake_ms;
      page[i] += u.page_ms;
      total[i] += Math.max(0, u.server_ms - u.wake_ms - u.page_ms);
    }
  }
  return { wake, page, total };
}

/** Running counts by day, from the first day anything began to today: how
 * many of each list had begun by the end of that day. Times are Unix seconds. */
export function growth(lists: number[][], now = new Date()): { days: string[]; counts: number[][] } {
  const first = Math.min(...lists.flat());
  if (!Number.isFinite(first)) return { days: [], counts: lists.map(() => []) };
  const start = Math.floor((first * 1000) / DAY) * DAY;
  const n = Math.max(1, Math.floor((now.getTime() - start) / DAY) + 1);
  const days = lastDays(n, now);
  const counts = lists.map((times) => {
    const per = days.map(() => 0);
    for (const t of times) {
      const i = Math.floor((t * 1000 - start) / DAY);
      if (i >= 0 && i < n) per[i]++;
    }
    let run = 0;
    return per.map((c) => (run += c));
  });
  return { days, counts };
}

/** Round gridlines over `max`: about four steps of 1, 2, 2.5 or 5 × 10^k —
 * a cent at least for money, a whole number for a count or a time (in
 * minutes, or hours once the axis passes two). */
export function ticks(max: number, unit: Unit): { top: number; step: number; scale: number } {
  const scale = unit === "ms" ? (max >= 2 * 3600e3 ? 3600e3 : 60e3) : 1;
  const m = max / scale;
  const least = unit === "usd" ? 0.01 : 1;
  let step = least;
  if (m > 0) {
    const raw = m / 4;
    const p = 10 ** Math.floor(Math.log10(raw));
    const nice = unit === "usd" ? [1, 2, 2.5, 5, 10] : [1, 2, 5, 10];
    step = Math.max(least, nice.map((f) => f * p).find((s) => s >= raw - 1e-12) ?? 10 * p);
  }
  const top = +Math.max(step, Math.ceil(m / step - 1e-9) * step).toPrecision(12);
  return { top: top * scale, step: step * scale, scale };
}

/** A value as its axis and readout say it: $0.42, 34 min, 1.5 h, 12. */
export function fmt(v: number, unit: Unit): string {
  if (unit === "usd") return v > 0 && v < 0.005 ? "<$0.01" : `$${v.toFixed(2)}`;
  if (unit === "count") return String(Math.round(v));
  const m = v / 60000;
  return !v ? "0" : m < 1 ? "<1 min" : m < 90 ? `${Math.round(m)} min` : `${(m / 60).toFixed(1)} h`;
}

function tick(v: number, unit: Unit, scale: number): string {
  if (unit === "usd") return v === 0 ? "$0" : v >= 1 ? `$${+v.toFixed(2)}` : `$${v.toFixed(2)}`;
  if (unit === "count") return String(v);
  return v === 0 ? "0" : `${+(v / scale).toFixed(1)} ${scale === 60e3 ? "min" : "h"}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** 24 Sep (spelled out: a runtime's en-GB may say "Sept"). */
export function dayLabel(day: string): string {
  return `${Number(day.slice(8, 10))} ${MONTHS[Number(day.slice(5, 7)) - 1]}`;
}

const pct = (v: number) => `${+v.toFixed(3)}%`;

export function chart(c: ChartSpec): string {
  const e = escapeHtml;
  const n = c.days.length;
  const max = c.kind === "stack"
    ? Math.max(0, ...c.days.map((_, i) => c.series.reduce((a, s) => a + s.values[i], 0)))
    : Math.max(0, ...c.series.flatMap((s) => s.values));
  const { top, step, scale } = ticks(max, c.unit);
  const y = (v: number) => (v / top) * 100;

  const legend = `<div class="chart-legend">${c.series.map((s) => `<span><i class="key ${c.kind === "line" ? "line" : ""}" style="--c:var(${s.color})"></i>${e(s.label)}${
    c.latest && n ? ` <b>${fmt(s.values[n - 1], c.unit)}</b>` : ""}</span>`).join("")}</div>`;

  const grid: string[] = [];
  for (let k = 0; k <= Math.round(top / step); k++) {
    const v = k * step;
    grid.push(`<div class="grid${k ? "" : " base"}" style="bottom:${pct(y(v))}"><span>${tick(v, c.unit, scale)}</span></div>`);
  }

  let marks = "";
  if (c.kind === "stack") {
    marks = `<div class="cols">${c.days.map((_, i) => {
      const segs = c.series.map((s) => ({ v: s.values[i], color: s.color })).filter((s) => s.v > 0);
      return `<div class="col">${segs.map((s, k) => `<div class="seg${k === segs.length - 1 ? " cap" : ""}${k ? " gap" : ""}" style="height:${pct(y(s.v))};--c:var(${s.color})"></div>`).join("")}</div>`;
    }).join("")}</div>`;
  } else {
    // Steps: a running count holds until the day it changes. The layer is
    // stretched to the plot, so its strokes are kept 2px by non-scaling-stroke.
    const paths = c.series.map((s) => {
      const d = s.values.map((v, i) => (i ? `H${i + 0.5}V${+(100 - y(v)).toFixed(3)}` : `M0.5 ${+(100 - y(v)).toFixed(3)}`)).join("");
      return `<path d="${d}H${n - 0.5}" style="stroke:var(${s.color})"/>`;
    }).join("");
    const ends = c.series.map((s) => `<i class="end" style="left:${pct(((n - 0.5) / n) * 100)};bottom:${pct(y(s.values[n - 1]))};--c:var(${s.color})"></i>`).join("");
    marks = `<svg class="lines" viewBox="0 0 ${n} 100" preserveAspectRatio="none" aria-hidden="true">${paths}</svg>${ends}`;
  }

  // A date under today and back from it in even steps, four or five in all,
  // which still fit a phone.
  const every = Math.max(1, Math.round(n / 4));
  const xs = c.days.map((d, i) => ((n - 1 - i) % every === 0
    ? `<span style="left:${pct(((i + 0.5) / n) * 100)}">${dayLabel(d)}</span>` : "")).join("");

  const data = JSON.stringify({
    kind: c.kind, unit: c.unit,
    x: c.days.map(dayLabel),
    s: c.series.map((s) => ({ l: s.label, c: s.color, v: s.values })),
  });

  const rows = c.days.map((d, i) => ({ d, vals: c.series.map((s) => s.values[i]) }))
    // A running count's table keeps only the days it changed.
    .filter((r, i, all) => c.kind === "stack" || i === 0 || r.vals.some((v, k) => v !== all[i - 1].vals[k]))
    .reverse();
  const table = `<details class="chart-table"><summary>Table</summary><table><thead><tr><th>Day</th>${
    c.series.map((s) => `<th class="num">${e(s.label)}</th>`).join("")}</tr></thead><tbody>${
    rows.map((r) => `<tr><td>${dayLabel(r.d)}</td>${r.vals.map((v) => `<td class="num">${fmt(v, c.unit)}</td>`).join("")}</tr>`).join("")}
    </tbody></table></details>`;

  return `<figure class="chart" data-chart="${e(data)}">${legend}
    <div class="plot" tabindex="0" role="img" aria-label="${e(c.label)}. Arrow keys step through the days.">
      ${grid.join("")}${marks}<div class="xhair" hidden></div><div class="tip" hidden></div></div>
    <div class="xaxis">${xs}</div>${table}</figure>`;
}

export const CHART_STYLE = `
  :root { --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s-rest:#bdb9ae; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s-rest:#5e5a52; } }
  .chart { margin:0; padding:4px 20px 14px; }
  .chart-title { font-size:13px; color:var(--ink-2); margin:0 0 6px; padding:0 20px; }
  .chart-note { font-size:12px; color:var(--muted); margin:0; padding:0 20px 16px; }
  .usage .chart-title { padding:0; }
  .usage .chart { padding:4px 0 18px; }
  .chart-legend { display:flex; flex-wrap:wrap; gap:4px 16px; font-size:12px; color:var(--ink-2); margin:0 0 10px; }
  .chart-legend span { display:inline-flex; align-items:center; gap:6px; }
  .chart-legend b { font-weight:600; color:var(--ink); }
  .key { display:inline-block; width:10px; height:10px; border-radius:2px; background:var(--c); }
  .key.line { height:2px; width:12px; border-radius:1px; }
  .plot { position:relative; height:140px; margin-left:44px; outline:none; }
  .plot:focus-visible { box-shadow:0 0 0 2px var(--surface), 0 0 0 4px var(--ring); border-radius:2px; }
  .grid { position:absolute; left:0; right:0; height:0; border-top:1px solid var(--border); }
  .grid.base { border-top-color:var(--border-strong); }
  .grid span { position:absolute; right:calc(100% + 8px); top:-7px; font-size:11px; line-height:14px;
    color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }
  .cols { position:absolute; inset:0; display:flex; }
  .col { flex:1 1 0; min-width:0; display:flex; flex-direction:column-reverse; align-items:center; border-radius:3px; }
  .col.on { background:color-mix(in srgb, var(--ink) 6%, transparent); }
  .seg { width:min(24px, 62%); background:var(--c); box-sizing:border-box; flex:none; }
  .seg.gap { border-bottom:2px solid var(--surface); }
  .seg.cap { border-radius:4px 4px 0 0; }
  .lines { position:absolute; inset:0; width:100%; height:100%; overflow:visible; }
  .lines path { fill:none; stroke-width:2; stroke-linejoin:round; stroke-linecap:round; vector-effect:non-scaling-stroke; }
  .end { position:absolute; width:8px; height:8px; margin:0 0 -6px -6px; border-radius:50%;
    background:var(--c); border:2px solid var(--surface); box-sizing:content-box; }
  .xhair { position:absolute; top:0; bottom:0; width:0; border-left:1px solid var(--border-strong); pointer-events:none; }
  .xaxis { position:relative; height:18px; margin-left:44px; font-size:11px; color:var(--muted); }
  .xaxis span { position:absolute; top:5px; transform:translateX(-50%); white-space:nowrap; }
  .tip { position:absolute; top:6px; z-index:5; min-width:120px; padding:7px 10px; border-radius:7px; pointer-events:none;
    background:var(--surface); border:1px solid var(--border); box-shadow:var(--sh-card); font-size:12px; line-height:1.5; }
  .tip .d { color:var(--muted); margin-bottom:2px; }
  .tip .r { display:grid; grid-template-columns:12px auto 1fr; align-items:center; gap:7px; white-space:nowrap; }
  .tip .r i { height:2px; border-radius:1px; background:var(--c); }
  .tip .r strong { font-weight:600; font-variant-numeric:tabular-nums; }
  .tip .r span { color:var(--muted); }
  .tip .r.sum { border-top:1px solid var(--border); margin-top:3px; padding-top:3px; }
  .chart-table { margin-top:6px; font-size:12px; }
  .chart-table summary { cursor:pointer; color:var(--muted); width:max-content; }
  .chart-table table { margin-top:6px; font-size:12px; }
  .chart-table th, .chart-table td { padding:4px 0; }
  .chart-table th.num, .chart-table td.num { text-align:right; padding-left:16px; font-variant-numeric:tabular-nums; }
  @media (max-width:600px) { .chart { padding-left:14px; padding-right:14px; }
    .chart-table th:nth-child(n+3), .chart-table td:nth-child(n+3) { display:table-cell; } }
`;

/** The hover readout: the day under the pointer (or chosen with the arrow
 * keys) lights its column, or the crosshair, and lists every series there. */
export const CHART_SCRIPT = `<script>(function(){
  function fmt(v,u){if(u==="usd")return v>0&&v<0.005?"<$0.01":"$"+v.toFixed(2);if(u==="count")return String(Math.round(v));
    var m=v/60000;return !v?"0":m<1?"<1 min":m<90?Math.round(m)+" min":(m/60).toFixed(1)+" h"}
  function row(c,v,l,sum){var r=document.createElement("div");r.className="r"+(sum?" sum":"");
    var k=document.createElement("i");if(c)k.style.setProperty("--c","var("+c+")");
    var s=document.createElement("strong");s.textContent=v;var t=document.createElement("span");t.textContent=l;
    r.appendChild(k);r.appendChild(s);r.appendChild(t);return r}
  document.querySelectorAll(".chart").forEach(function(fig){
    var d=JSON.parse(fig.getAttribute("data-chart")),n=d.x.length,plot=fig.querySelector(".plot"),
      tip=fig.querySelector(".tip"),xh=fig.querySelector(".xhair"),cols=fig.querySelectorAll(".col"),at=-1;
    if(!n)return;
    function show(i){
      i=Math.max(0,Math.min(n-1,i));at=i;
      cols.forEach(function(c,k){c.classList.toggle("on",k===i)});
      var w=plot.clientWidth,cx=(i+.5)/n*w;
      if(d.kind==="line"){xh.hidden=false;xh.style.left=cx+"px"}
      tip.textContent="";var h=document.createElement("div");h.className="d";h.textContent=d.x[i];tip.appendChild(h);
      var list=d.kind==="stack"?d.s.slice().reverse():d.s,total=0;
      list.forEach(function(s){total+=s.v[i];tip.appendChild(row(s.c,fmt(s.v[i],d.unit),s.l))});
      if(d.kind==="stack"&&d.s.length>1)tip.appendChild(row("",fmt(total,d.unit),"Total",true));
      tip.hidden=false;var tw=tip.offsetWidth,x=cx+14;if(x+tw>w)x=cx-14-tw;tip.style.left=Math.max(0,x)+"px";
    }
    function hide(){at=-1;tip.hidden=true;xh.hidden=true;cols.forEach(function(c){c.classList.remove("on")})}
    function point(e){var r=plot.getBoundingClientRect();show(Math.floor((e.clientX-r.left)/r.width*n))}
    plot.addEventListener("pointermove",point);
    plot.addEventListener("pointerdown",point);
    // A tap keeps its readout until a tap elsewhere (the plot loses focus).
    plot.addEventListener("pointerleave",function(e){if(e.pointerType==="mouse"&&document.activeElement!==plot)hide()});
    plot.addEventListener("focus",function(){show(at<0?n-1:at)});
    plot.addEventListener("blur",hide);
    plot.addEventListener("keydown",function(e){
      if(e.key==="ArrowLeft"||e.key==="ArrowRight"){e.preventDefault();show((at<0?n-1:at)+(e.key==="ArrowLeft"?-1:1))}
      else if(e.key==="Home"){e.preventDefault();show(0)}else if(e.key==="End"){e.preventDefault();show(n-1)}
      else if(e.key==="Escape")plot.blur();
    });
  });
})()</script>`;
