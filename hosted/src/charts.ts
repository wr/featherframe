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
  title?: string;              // a few words before the legend
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
  const { top, scale } = ticks(max, c.unit);
  const y = (v: number) => (v / top) * 100;

  const legend = c.series.map((s) => `<span><i class="key${c.kind === "line" ? " line" : ""}" style="--c:var(${s.color})"></i>${e(s.label)}${
    c.latest && n ? ` <b>${fmt(s.values[n - 1], c.unit)}</b>` : ""}</span>`).join("");
  const head = `<div class="chart-head">${c.title ? `<span class="chart-title">${e(c.title)}</span>` : ""}<span class="chart-legend">${legend}</span></div>`;

  // Two lines only: the baseline, and the top of the scale with its value.
  const grid = `<div class="grid base"></div><div class="grid top"><span>${tick(top, c.unit, scale)}</span></div>`;

  let marks = "";
  if (c.kind === "stack") {
    marks = `<div class="cols">${c.days.map((_, i) => {
      // A part under a pixel is left to the readout: drawn, it reads as a dashed baseline.
      const segs = c.series.map((s) => ({ v: s.values[i], color: s.color })).filter((s) => y(s.v) >= 2);
      return `<div class="col">${segs.map((s, k) => `<div class="seg${k === segs.length - 1 ? " cap" : ""}${k ? " gap" : ""}" style="height:${pct(y(s.v))};--c:var(${s.color})"></div>`).join("")}</div>`;
    }).join("")}</div>`;
  } else {
    // Steps: a running count holds until the day it changes. The layer is
    // stretched to the plot, so its strokes are kept 2px by non-scaling-stroke.
    const paths = c.series.map((s) => {
      const d = s.values.map((v, i) => (i ? `H${i + 0.5}V${+(100 - y(v)).toFixed(3)}` : `M0.5 ${+(100 - y(v)).toFixed(3)}`)).join("");
      return `<path d="${d}H${n - 0.5}" style="stroke:var(${s.color})"/>`;
    }).join("");
    marks = `<svg class="lines" viewBox="0 0 ${n} 100" preserveAspectRatio="none" aria-hidden="true">${paths}</svg>`;
  }

  // The first day and the last, under the plot's two ends.
  const xs = n ? `<span>${dayLabel(c.days[0])}</span>${n > 1 ? `<span>${dayLabel(c.days[n - 1])}</span>` : ""}` : "";

  const data = JSON.stringify({
    kind: c.kind, unit: c.unit,
    x: c.days.map(dayLabel),
    s: c.series.map((s) => ({ l: s.label, c: s.color, v: s.values })),
  });

  // The values as a table, for a screen reader: the plot is a picture to it.
  const rows = c.days.map((d, i) => ({ d, vals: c.series.map((s) => s.values[i]) }))
    // A running count's table keeps only the days it changed.
    .filter((r, i, all) => c.kind === "stack" || i === 0 || r.vals.some((v, k) => v !== all[i - 1].vals[k]))
    .reverse();
  const table = `<table class="chart-table"><caption>${e(c.label)}</caption><thead><tr><th>Day</th>${
    c.series.map((s) => `<th>${e(s.label)}</th>`).join("")}</tr></thead><tbody>${
    rows.map((r) => `<tr><td>${dayLabel(r.d)}</td>${r.vals.map((v) => `<td>${fmt(v, c.unit)}</td>`).join("")}</tr>`).join("")}
    </tbody></table>`;

  return `<figure class="chart" data-chart="${e(data)}">${head}
    <div class="plot" tabindex="0" role="img" aria-label="${e(c.label)}. Arrow keys step through the days.">
      ${grid}${marks}<div class="xhair" hidden></div><div class="tip" hidden></div></div>
    <div class="xaxis">${xs}</div>${table}</figure>`;
}

// One hue, the page's accent, in three steps (W-923): a stack's base and the
// last of a funnel are the strongest. Checked as ordinal ramps against the
// page's light and dark surfaces.
export const CHART_STYLE = `
  :root { --r1:#cdb08c; --r2:#9c7550; --r3:#6b4a2c; --r-rest:#cfcbc2; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --r1:#6a5642; --r2:#8d6f50; --r3:#c09a70; --r-rest:#4a463f; } }
  .chart { margin:0; padding:2px 20px 16px; }
  .usage .chart { padding:0 0 18px; }
  .chart-head { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:2px 16px;
    font-size:12px; color:var(--muted); margin:0 0 8px; }
  .chart-legend { display:flex; flex-wrap:wrap; gap:2px 12px; }
  .chart-legend span { display:inline-flex; align-items:center; gap:5px; }
  .chart-legend b { font-weight:600; color:var(--ink-2); font-variant-numeric:tabular-nums; }
  .key { display:inline-block; width:8px; height:8px; border-radius:2px; background:var(--c); }
  .key.line { height:2px; width:10px; border-radius:1px; }
  .plot { position:relative; height:56px; outline:none; }
  .plot:focus-visible { box-shadow:0 0 0 2px var(--surface), 0 0 0 4px var(--ring); border-radius:2px; }
  .grid { position:absolute; left:0; right:0; height:0; border-top:1px solid var(--border); }
  .grid.base { bottom:0; border-top-color:var(--border-strong); }
  .grid.top { top:0; }
  .grid span { position:absolute; left:0; top:1px; padding:0 4px 0 0; background:var(--surface); font-size:10px;
    line-height:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .cols { position:absolute; inset:0; display:flex; }
  .col { flex:1 1 0; min-width:0; display:flex; flex-direction:column-reverse; align-items:center; border-radius:2px; }
  .col.on { background:color-mix(in srgb, var(--ink) 5%, transparent); }
  .seg { width:min(10px, 56%); background:var(--c); box-sizing:border-box; flex:none; }
  .seg.gap { border-bottom:1px solid var(--surface); }
  .seg.cap { border-radius:2px 2px 0 0; }
  .lines { position:absolute; inset:0; width:100%; height:100%; overflow:visible; }
  .lines path { fill:none; stroke-width:1.5; stroke-linejoin:round; stroke-linecap:round; vector-effect:non-scaling-stroke; }
  .xhair { position:absolute; top:0; bottom:0; width:0; border-left:1px solid var(--border-strong); pointer-events:none; }
  .xaxis { display:flex; justify-content:space-between; margin-top:4px; font-size:10px; color:var(--muted); }
  /* Fixed, placed over the plot by script: the card clips to its radius. */
  .tip { position:fixed; z-index:30; padding:5px 8px; border-radius:6px; pointer-events:none;
    background:var(--surface); border:1px solid var(--border); box-shadow:var(--sh-card); font-size:12px; line-height:1.45; }
  .tip .d { color:var(--muted); }
  .tip .r { display:grid; grid-template-columns:10px auto 1fr; align-items:center; gap:6px; white-space:nowrap; }
  .tip .r i { height:2px; border-radius:1px; background:var(--c); }
  .tip .r strong { font-weight:600; font-variant-numeric:tabular-nums; }
  .tip .r span { color:var(--muted); }
  .tip .r.sum { border-top:1px solid var(--border); margin-top:2px; padding-top:2px; }
  .chart-table { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
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
      list.forEach(function(s){total+=s.v[i];if(d.kind==="line"||s.v[i])tip.appendChild(row(s.c,fmt(s.v[i],d.unit),s.l))});
      if(d.kind==="stack"&&d.s.length>1)tip.appendChild(row("",fmt(total,d.unit),"Total",true));
      tip.hidden=false;var r=plot.getBoundingClientRect(),tw=tip.offsetWidth,th=tip.offsetHeight,
        x=r.left+cx+12,y=r.top-th-6;
      if(x+tw>r.right)x=r.left+cx-12-tw;if(y<8)y=r.bottom+6;
      tip.style.left=Math.max(8,x)+"px";tip.style.top=y+"px";
    }
    function hide(){at=-1;tip.hidden=true;xh.hidden=true;cols.forEach(function(c){c.classList.remove("on")})}
    function point(e){var r=plot.getBoundingClientRect();show(Math.floor((e.clientX-r.left)/r.width*n))}
    plot.addEventListener("pointermove",point);
    plot.addEventListener("pointerdown",point);
    // A tap keeps its readout until a tap elsewhere (the plot loses focus).
    plot.addEventListener("pointerleave",function(e){if(e.pointerType==="mouse"&&document.activeElement!==plot)hide()});
    plot.addEventListener("focus",function(){show(at<0?n-1:at)});
    plot.addEventListener("blur",hide);
    window.addEventListener("scroll",function(){if(at>=0)show(at)},{passive:true});
    plot.addEventListener("keydown",function(e){
      if(e.key==="ArrowLeft"||e.key==="ArrowRight"){e.preventDefault();show((at<0?n-1:at)+(e.key==="ArrowLeft"?-1:1))}
      else if(e.key==="Home"){e.preventDefault();show(0)}else if(e.key==="End"){e.preventDefault();show(n-1)}
      else if(e.key==="Escape")plot.blur();
    });
  });
})()</script>`;
