// II. A wall frame, opened large: a click (or Enter / Space) on any of the
// wall's frames lifts it off the wall into the middle of the window, as large
// as fits, with its caption's three lines under it, over the page's own paper
// at 96%. It flies from its place on the wall and back to it (FLIP). Closes on
// a click outside it, the Close button, or Esc; focus stays in it while it is
// open and returns to the frame after. The still first, then a larger render
// of it (img/wall/large/, scripts/wall.mjs large) once that has loaded.

const EASE = 'cubic-bezier(.2, .8, .2, 1)';
const MS = 420;

export function startLightbox(reduced: boolean): void {
  const frames = [...document.querySelectorAll<HTMLElement>('.wall .cat .im')];
  const page = document.querySelector<HTMLElement>('.page');
  const root = document.documentElement;
  let open: { close(): void } | null = null;

  for (const im of frames) {
    const fig = im.closest('figure')!;
    const name = fig.querySelector('.nm')?.textContent ?? '';
    im.tabIndex = 0;
    im.setAttribute('role', 'button');
    im.setAttribute('aria-label', name);
    im.addEventListener('click', () => show(im));
    im.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(im); }
    });
  }

  /** The transform that puts `to`'s box on `from`'s. */
  const invert = (from: DOMRect, to: DOMRect) =>
    `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width}, ${from.height / to.height})`;

  function show(im: HTMLElement) {
    if (open) return;
    const fig = im.closest('figure')!;
    const still = im.querySelector('img')!;
    const box = document.createElement('div');
    box.className = 'lightbox';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', im.getAttribute('aria-label') ?? '');
    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.alt = '';
    img.src = still.currentSrc || still.src;
    img.width = still.naturalWidth || 604;
    img.height = still.naturalHeight || 760;
    const cap = fig.querySelector('figcaption')!.cloneNode(true) as HTMLElement;
    figure.append(img, cap);
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'lb-close';
    close.setAttribute('aria-label', 'Close');
    close.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M5 5l14 14M19 5L5 19" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    box.append(figure, close);
    document.body.append(box);
    // the larger render, in the tone the wall is showing
    const large = new Image();
    large.onload = () => { if (box.isConnected) img.src = large.src; };
    large.src = (still.getAttribute('src') ?? '').replace('img/wall/', 'img/wall/large/');

    root.classList.add('lb-open');
    if (page) page.inert = true;
    const from = im.getBoundingClientRect();
    const to = img.getBoundingClientRect();
    if (!reduced) {
      img.animate([{ transform: invert(from, to) }, { transform: 'none' }], { duration: MS, easing: EASE });
      box.animate([{ backgroundColor: 'transparent' }, {}], { duration: MS, easing: 'ease' });
      for (const el of [cap, close]) el.animate([{ opacity: 0 }, { opacity: 0, offset: 0.5 }, { opacity: 1 }], { duration: MS, easing: 'ease' });
    }
    close.focus();

    let closing = false;
    const done = () => {
      box.remove();
      root.classList.remove('lb-open');
      if (page) page.inert = false;
      document.removeEventListener('keydown', onKey, true);
      open = null;
      im.focus();
    };
    const shut = () => {
      if (closing) return;
      closing = true;
      if (reduced) { done(); return; }
      const back = invert(im.getBoundingClientRect(), img.getBoundingClientRect());
      for (const el of [cap, close]) el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: MS / 2, fill: 'forwards' });
      box.animate([{}, { backgroundColor: 'transparent' }], { duration: MS, easing: 'ease', fill: 'forwards' });
      img.animate([{ transform: 'none' }, { transform: back }], { duration: MS, easing: EASE, fill: 'forwards' }).finished.then(done, done);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); shut(); }
      // the one control in it keeps the focus
      else if (e.key === 'Tab') { e.preventDefault(); close.focus(); }
    };
    document.addEventListener('keydown', onKey, true);
    close.addEventListener('click', shut);
    box.addEventListener('click', (e) => {
      const t = e.target as Node;
      if (!img.contains(t) && !cap.contains(t) && !close.contains(t)) shut();
    });
    open = { close: shut };
  }
}
