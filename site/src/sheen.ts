// The glass's sheen on the gallery wall's frames: the soft highlight each
// frame's glass carries (styles.css .cat .im::after) slides across it as the
// frame moves up the screen, by the same rule as the 3D frame's (viewer.ts
// sheenAt), so a hand-off between the two does not jump. Page positions are
// read on resize only; a scroll only writes each frame's --c.
import { sheenAt } from './sheen-at';

export function startSheen(frames: HTMLElement[]): void {
  if (!frames.length) return;
  let tops: number[] = [], heights: number[] = [], raf = 0, near = false;
  const measure = () => {
    tops = frames.map((f) => f.getBoundingClientRect().top + scrollY);
    heights = frames.map((f) => f.getBoundingClientRect().height);
  };
  const paint = () => {
    raf = 0;
    const vh = document.documentElement.clientHeight;
    frames.forEach((f, i) => f.style.setProperty('--c', sheenAt(tops[i] - scrollY + heights[i] / 2, vh).toFixed(3)));
  };
  const request = () => { if (near && !raf) raf = requestAnimationFrame(paint); };
  const wall = frames[0].closest('section') ?? frames[0];
  new IntersectionObserver(([e]) => { near = e.isIntersecting; if (near) { measure(); request(); } }, { rootMargin: '50% 0px' }).observe(wall);
  new ResizeObserver(() => { measure(); request(); }).observe(document.body);
  addEventListener('scroll', request, { passive: true });
}
