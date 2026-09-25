// IV. The collage is the night's: as its section scrolls in, the page turns
// from day to night (white to #0f0f0e, the type to #f2f1ec) over ~40% of the
// window's height, and back again as it scrolls out. styles.css mixes every
// colour by --night (0 day … 1 night); <html class="night"> says it is more
// night than day. The frames and collages keep their own light.
const SPAN = 0.4; // of the window's height, each way

export function startNight(): void {
  const section = document.getElementById('collage');
  if (!section) return;
  const root = document.documentElement;
  let raf = 0, last = -1;
  const paint = () => {
    raf = 0;
    const vh = root.clientHeight;
    const r = section.getBoundingClientRect();
    // in: its top from 90% of the way down the window to 50%; out: its bottom from 60% up to 20%
    const into = (0.9 * vh - r.top) / (SPAN * vh);
    const out = (r.bottom - 0.2 * vh) / (SPAN * vh);
    const t = Math.max(0, Math.min(1, into, out));
    const m = t * t * (3 - 2 * t);
    const v = Math.round(m * 1000) / 1000;
    if (v === last) return;
    last = v;
    if (v) root.style.setProperty('--night', String(v));
    else root.style.removeProperty('--night');
    root.classList.toggle('night', v >= 0.5);
  };
  const request = () => { if (!raf) raf = requestAnimationFrame(paint); };
  addEventListener('scroll', request, { passive: true });
  addEventListener('resize', request);
  new ResizeObserver(request).observe(document.body);
  request();
}
