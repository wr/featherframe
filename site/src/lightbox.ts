// II. A wall frame, opened large: a click (or Enter / Space) on any of the
// wall's frames lifts it off the wall into the middle of the window, as large
// as fits, with its caption's three lines under it, over the page's own paper
// at 96%. It flies from its place on the wall and back to it (FLIP). The
// Left / Right arrow keys (or the small Previous / Next buttons, shown on
// hover or focus) step through the wall's frames, wrapping, with a quick
// cross-fade. Closes on a click outside it, the Close button, or Esc; focus
// stays in it while it is open and returns, after, to the frame it closes on.
// The still first, then a larger render of it (img/wall/large/,
// scripts/wall.mjs large) once that has loaded.

const EASE = 'cubic-bezier(.2, .8, .2, 1)';
const MS = 420;
const FADE = 220;

const ICON = (d: string) => `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

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

  /** One wall frame, large: its picture and its caption. */
  function plate(im: HTMLElement) {
    const still = im.querySelector('img')!;
    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.alt = '';
    img.src = still.currentSrc || still.src;
    img.width = still.naturalWidth || 604;
    img.height = still.naturalHeight || 760;
    const cap = im.closest('figure')!.querySelector('figcaption')!.cloneNode(true) as HTMLElement;
    figure.append(img, cap);
    // the larger render, in the tone the wall is showing
    const large = new Image();
    large.onload = () => { if (figure.isConnected) img.src = large.src; };
    large.src = (still.getAttribute('src') ?? '').replace('img/wall/', 'img/wall/large/');
    return { figure, img, cap };
  }

  const button = (cls: string, label: string, icon: string) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = cls;
    b.setAttribute('aria-label', label);
    b.innerHTML = ICON(icon);
    return b;
  };

  function show(first: HTMLElement) {
    if (open) return;
    let index = frames.indexOf(first);
    let im = first;
    const box = document.createElement('div');
    box.className = 'lightbox';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', im.getAttribute('aria-label') ?? '');
    let cur = plate(im);
    const close = button('lb-close', 'Close', 'M5 5l14 14M19 5L5 19');
    const prev = button('lb-nav prev', 'Previous', 'M15 5l-7 7 7 7');
    const next = button('lb-nav next', 'Next', 'M9 5l7 7-7 7');
    box.append(cur.figure, prev, next, close);
    document.body.append(box);

    root.classList.add('lb-open');
    if (page) page.inert = true;
    const from = im.getBoundingClientRect();
    const to = cur.img.getBoundingClientRect();
    if (!reduced) {
      cur.img.animate([{ transform: invert(from, to) }, { transform: 'none' }], { duration: MS, easing: EASE });
      box.animate([{ backgroundColor: 'transparent' }, {}], { duration: MS, easing: 'ease' });
      for (const el of [cur.cap, close, prev, next]) el.animate([{ opacity: 0 }, { opacity: 0, offset: 0.5 }, { opacity: 1 }], { duration: MS, easing: 'ease' });
    }
    close.focus();

    /** Step `d` frames along the wall, wrapping, cross-fading to it. */
    const go = (d: number) => {
      if (closing) return;
      index = (index + d + frames.length) % frames.length;
      im = frames[index];
      box.setAttribute('aria-label', im.getAttribute('aria-label') ?? '');
      const old = cur;
      cur = plate(im);
      box.insertBefore(cur.figure, prev);
      if (reduced) { old.figure.remove(); return; }
      old.figure.style.pointerEvents = 'none';
      cur.figure.animate([{ opacity: 0 }, { opacity: 1 }], { duration: FADE, easing: 'ease' });
      old.figure.animate([{ opacity: 1 }, { opacity: 0 }], { duration: FADE, easing: 'ease', fill: 'forwards' })
        .finished.then(() => old.figure.remove(), () => old.figure.remove());
    };

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
      for (const f of box.querySelectorAll('figure')) if (f !== cur.figure) f.remove();
      if (reduced) { done(); return; }
      const back = invert(im.getBoundingClientRect(), cur.img.getBoundingClientRect());
      for (const el of [cur.cap, close, prev, next]) el.animate([{ opacity: getComputedStyle(el).opacity }, { opacity: 0 }], { duration: MS / 2, fill: 'forwards' });
      box.animate([{}, { backgroundColor: 'transparent' }], { duration: MS, easing: 'ease', fill: 'forwards' });
      cur.img.animate([{ transform: 'none' }, { transform: back }], { duration: MS, easing: EASE, fill: 'forwards' }).finished.then(done, done);
    };
    const controls = [close, prev, next];
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); shut(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
      // the focus goes round its three controls
      else if (e.key === 'Tab') {
        e.preventDefault();
        const i = controls.indexOf(document.activeElement as HTMLButtonElement);
        controls[(i + (e.shiftKey ? -1 : 1) + controls.length) % controls.length].focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    close.addEventListener('click', shut);
    prev.addEventListener('click', () => go(-1));
    next.addEventListener('click', () => go(1));
    box.addEventListener('click', (e) => {
      const t = e.target as Element;
      if (!t.closest('img, figcaption, button')) shut();
    });
    open = { close: shut };
  }
}
