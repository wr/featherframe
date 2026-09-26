// IV. The collage is the night's: the page turns from day to night (white to
// #1a1a1a, the type to #f2f1ec, the running head with it) when the collage's
// eyebrow comes up to EARLY px (or LINE of the window) below the bottom of the running head, and stays night for the
// rest of the page (Technical details, Reserve, Questions, the colophon); it
// turns back to day only when that eyebrow is scrolled back down below that
// line. The flip is a threshold, not scrubbed by the
// scroll: <html class="night"> sets --night to 1 and styles.css eases it over
// 450 ms (instantly with reduced motion), whatever the scroll's speed; every
// colour is its day and its night mixed by --night. The frames and collages
// keep their own light.
/** How far below the running head's bottom the eyebrow flips the page: EARLY px,
 *  or LINE of the window's height where that is further (a taller window), so the
 *  page is dark before the chapter's headline is read. */
export const EARLY = 150;
export const LINE = 0.45;

export function startNight(): void {
  const section = document.getElementById('collage');
  const eyebrow = section?.querySelector('.eyebrow');
  if (!section || !eyebrow) return;
  const root = document.documentElement;
  const head = document.querySelector('.head');
  let raf = 0;
  const paint = () => {
    raf = 0;
    const line = (head ? head.getBoundingClientRect().bottom : 0) + Math.max(EARLY, Math.round(LINE * innerHeight));
    const night = eyebrow.getBoundingClientRect().top <= line;
    if (root.classList.contains('night') !== night) root.classList.toggle('night', night);
  };
  const request = () => { if (!raf) raf = requestAnimationFrame(paint); };
  addEventListener('scroll', request, { passive: true });
  addEventListener('resize', request);
  new ResizeObserver(request).observe(document.body);
  request();
}
