// The Black Friday waitlist (W-1017): each form.wl posts its address to the shop's /api/waitlist, then, before the
// sale and in its early hour, opens the one follow-up dialog (size, gift) with the token the answer carries. A port of
// the shop's src/lib/waitlist-form.ts. Turnstile loads only once someone starts on a form.

// The Turnstile widget shared with shop.wells.ee, filled in at rollout (W-1016). An empty key sends no token.
const SITEKEY = '';
const SHOP = 'https://shop.wells.ee';

type Answer = { ok?: boolean; error?: string; details?: string; state?: string };
type TurnstileApi = {
  render(el: HTMLElement, opts: Record<string, unknown>): string;
  reset(id: string): void;
};

const say = (el: HTMLElement | null, text: string) => {
  if (!el) return;
  el.textContent = text;
  el.hidden = !text;
};

let api: Promise<TurnstileApi> | null = null;

/** Turnstile's script, once per page, however many widgets ask for it. */
function turnstileApi(): Promise<TurnstileApi> {
  api ??= new Promise<TurnstileApi>((resolve, reject) => {
    const w = window as unknown as Record<string, unknown>;
    const cb = `wlTurnstile${Math.random().toString(36).slice(2)}`;
    w[cb] = () => {
      delete w[cb];
      resolve(w.turnstile as TurnstileApi);
    };
    const s = document.createElement('script');
    s.src = `https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=${cb}`;
    s.async = true;
    s.onerror = () => {
      api = null;
      reject(new Error('turnstile'));
    };
    document.head.append(s);
  });
  return api;
}

export interface TurnstileWidget {
  /** Start loading the widget (once). A no-op without a sitekey. */
  load(): void;
  /** A token: '' at once without a sitekey, else the widget's, waiting up to `ms` for it. */
  token(ms?: number): Promise<string>;
  /** A token is good for one request: ask for the next one. */
  reset(): void;
}

/**
 * A lazily loaded Turnstile widget that shows itself only if it needs the
 * visitor, appended to `host`. With no sitekey there is no widget and token()
 * is ''. Rejects after `ms` (20 s) without one.
 */
export function turnstileWidget(host: HTMLElement, sitekey: string | undefined, action: string): TurnstileWidget {
  let token = '';
  let widget = '';
  let ts: TurnstileApi | null = null;
  let started = false;
  // A script that will not load is tried again only twice, so an offline visitor does not keep a request going.
  let attempts = 0;
  let waiting: ((t: string) => void)[] = [];
  const load = () => {
    if (!sitekey || started || attempts >= 3) return;
    started = true;
    attempts++;
    turnstileApi()
      .then((t) => {
        ts = t;
        widget = t.render(host.appendChild(document.createElement('div')), {
          sitekey,
          action,
          appearance: 'interaction-only',
          callback: (value: string) => {
            token = value;
            waiting.splice(0).forEach((f) => f(value));
          },
          'expired-callback': () => (token = ''),
          // Handled: a caller that waits gives up after its own timeout, and Turnstile retries meanwhile.
          'error-callback': () => true,
        });
      })
      .catch(() => (started = false));
  };
  return {
    load,
    token(ms = 20_000) {
      if (!sitekey) return Promise.resolve('');
      load();
      if (token) return Promise.resolve(token);
      return new Promise((resolve, reject) => {
        const give = (t: string) => {
          clearTimeout(timer);
          resolve(t);
        };
        const timer = setTimeout(() => {
          waiting = waiting.filter((f) => f !== give);
          reject(new Error('turnstile'));
        }, ms);
        waiting.push(give);
      });
    },
    reset() {
      token = '';
      if (ts && widget) ts.reset(widget);
    },
  };
}

export function initWaitlist(root: ParentNode = document): void {
  const dialog = root.querySelector<HTMLDialogElement>('dialog.wl-dialog');
  const details = dialog?.querySelector<HTMLFormElement>('form.wl-details') ?? null;
  // The details token from the latest signup, and the status line of the form that opened the dialog (focus returns there).
  let token = '';
  let opener: HTMLElement | null = null;

  root.querySelectorAll<HTMLFormElement>('form.wl').forEach((form) => {
    const status = form.querySelector<HTMLElement>('.wl-status');
    const button = form.querySelector<HTMLButtonElement>('button[type="submit"]');
    const ts = turnstileWidget(form, SITEKEY, 'waitlist');
    form.addEventListener('focusin', ts.load, { once: true });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const email = form.querySelector<HTMLInputElement>('input[name="email"]')?.value.trim() ?? '';
      if (!email) return;
      say(status, '');
      if (button) button.disabled = true;
      try {
        const turnstile = await ts.token().catch(() => null);
        if (turnstile === null) {
          say(status, 'The form could not be verified. Reload the page and try again.');
          ts.reset();
          if (button) button.disabled = false;
          return;
        }
        const res = await fetch(`${SHOP}/api/waitlist`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            slug: 'featherframe',
            email,
            source: 'featherframe',
            website: form.querySelector<HTMLInputElement>('input[name="website"]')?.value ?? '',
            turnstile,
          }),
        });
        const data = (await res.json().catch(() => ({}))) as Answer;
        if (res.ok && data.ok) {
          form.querySelectorAll<HTMLElement>('label, .field').forEach((el) => (el.hidden = true));
          say(status, data.state === 'open' ? 'The link is on its way to your inbox.' : 'You are on the waitlist. A confirmation is on its way.');
          // Before the sale and in its early hour the follow-up earns the early link; later there is nothing to ask for.
          if (dialog && details && data.details && (data.state === 'before' || data.state === 'early')) {
            token = data.details;
            opener = status;
            if (!dialog.open) dialog.showModal();
          }
          return;
        }
        say(status, data.error ?? 'That did not save. Try again.');
      } catch {
        say(status, 'That didn’t go through. Try again.');
      }
      ts.reset();
      if (button) button.disabled = false;
    });
  });

  if (!dialog || !details) return;
  dialog.querySelectorAll<HTMLElement>('.wl-skip, .wl-close').forEach((el) => el.addEventListener('click', () => dialog.close()));
  // The signup button that opened it is hidden by now, so focus would fall to the page's top: put it on the status line.
  dialog.addEventListener('close', () => {
    if (opener && !opener.hidden) opener.focus();
  });
  // A click on the backdrop lands on the dialog itself; one on its padding does too, so check where it fell.
  dialog.addEventListener('click', (e) => {
    if (e.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
  });

  details.addEventListener('submit', async (e) => {
    e.preventDefault();
    const dstatus = details.querySelector<HTMLElement>('.wl-status');
    const size = details.querySelector<HTMLInputElement>('input[name="size"]:checked')?.value;
    const gift = details.querySelector<HTMLInputElement>('input[name="gift"]:checked')?.value;
    if (!size || !gift) return;
    const send = details.querySelector<HTMLButtonElement>('button[type="submit"]');
    say(dstatus, '');
    if (send) send.disabled = true;
    try {
      const res = await fetch(`${SHOP}/api/waitlist/details`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ details: token, size, gift: gift === 'yes' }),
      });
      const data = (await res.json().catch(() => ({}))) as Answer;
      if (res.ok && data.ok) {
        const title = dialog.querySelector<HTMLElement>('.wl-title');
        if (title && details.dataset.done) title.textContent = details.dataset.done;
        const questions = details.querySelector<HTMLElement>('.wl-q');
        if (questions) questions.hidden = true;
        const close = details.querySelector<HTMLElement>('.wl-close');
        if (close) {
          close.hidden = false;
          close.focus();
        }
        say(dstatus, '');
        return;
      }
      say(dstatus, data.error ?? 'That did not save. Try again.');
    } catch {
      say(dstatus, 'That didn’t go through. Try again.');
    }
    if (send) send.disabled = false;
  });
}
