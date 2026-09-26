// IV's year of collages: on a desktop the row of seasons is pinned (styles.css
// .seasons-live) and slides sideways as the page scrolls down — spring's sheet
// centred at the start of the stretch, winter's at its end, where it rests
// centred for the last part of the stretch (SETTLE) before the page goes on.
// Scroll-linked, no snapping: a trackpad, a wheel and the keyboard all drive it
// as they drive the page. Phones and reduced motion keep the stacked sheets.
export function startSeasons(): void {
  const box = document.querySelector<HTMLElement>('.seasons');
  const row = box?.querySelector<HTMLElement>('.row');
  if (!box || !row) return;
  const root = document.documentElement;
  const wide = matchMedia('(min-width: 821px)');
  const SETTLE = 0.12; // the last share of the pinned stretch, winter held centred
  let start = 0, run = 1, from = 0, to = 0, raf = 0, live = false;

  const measure = () => {
    row.style.transform = '';
    const vw = root.clientWidth;
    const nav = document.querySelector('.head')?.getBoundingClientRect().height ?? 0;
    const b = box.getBoundingClientRect();
    const stage = root.clientHeight - nav;
    start = b.top + scrollY - nav;
    run = Math.max(1, b.height - stage);
    const panels = [...row.children] as HTMLElement[];
    const centre = (el: HTMLElement) => el.offsetLeft + el.offsetWidth / 2;
    from = vw / 2 - centre(panels[0]);
    to = vw / 2 - centre(panels[panels.length - 1]);
  };
  const paint = () => {
    raf = 0;
    if (!live) return;
    const p = Math.max(0, Math.min(1, (scrollY - start) / (run * (1 - SETTLE))));
    row.style.transform = `translate3d(${(from + (to - from) * p).toFixed(1)}px, 0, 0)`;
    row.dataset.progress = p.toFixed(3); // test hook
    // the season in the middle of the window: its mark on the timeline is drawn full
    const panels = row.children;
    const now = Math.round(p * (panels.length - 1));
    for (let i = 0; i < panels.length; i++) panels[i].classList.toggle('now', i === now);
  };
  const request = () => { if (live && !raf) raf = requestAnimationFrame(paint); };
  const setup = () => {
    live = wide.matches;
    root.classList.toggle('seasons-live', live);
    if (!live) { row.style.transform = ''; return; }
    measure();
    request();
  };
  wide.addEventListener('change', setup);
  new ResizeObserver(() => { if (live) { measure(); request(); } }).observe(document.body);
  addEventListener('scroll', request, { passive: true });
  setup();
}
