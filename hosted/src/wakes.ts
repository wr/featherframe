// When the front door wakes the household's server (W-847): for news, at
// most once per MIN_GAP_MS; at a time the server named; once a day
// regardless. A look (W-946) is a stale page someone opened: it waits out
// the same gap as news, and is never a reason to wake sooner.

export const POLL_MS = 2 * 60 * 1000;
export const MIN_GAP_MS = 5 * 60 * 1000;
export const SAFETY_MS = 24 * 60 * 60 * 1000;

export interface WakeState {
  now: number;
  lastWake: number;     // ms; 0 when never
  lastSwitch: number;   // ms; the front door's last switch on its own (W-1025); 0 when never
  named: number;        // ms; 0 when none
  news: boolean;        // a pairing, a BirdWeather look's news
  pushNews: boolean;    // a pushed detection the server did not say changes nothing (W-1025)
  look: boolean;
  birdweather: boolean; // a BirdWeather station is polled (and not in quiet hours)
}

// The gap after a push counts from the front door's own last switch as well as
// the server's last wake (W-1025): a switch changes the glass as a wake does.
const pushGap = (s: WakeState) => Math.max(s.lastWake, s.lastSwitch) + MIN_GAP_MS;

export function due(s: WakeState): boolean {
  return (s.named > 0 && s.named <= s.now)
    || ((s.news || s.look) && s.now - s.lastWake >= MIN_GAP_MS)
    || (s.pushNews && s.now >= pushGap(s))
    || s.now - s.lastWake >= SAFETY_MS;
}

/** Due only because detections arrived (W-1025): the front door may switch
 * the screens itself instead of waking the server. */
export function onlyDetections(s: WakeState): boolean {
  return s.pushNews && s.now >= pushGap(s) && !s.news && !s.look
    && !(s.named > 0 && s.named <= s.now) && s.now - s.lastWake < SAFETY_MS;
}

export function nextAlarm(s: WakeState): number {
  const times = [s.lastWake + SAFETY_MS];
  if (s.named > s.now) times.push(s.named);
  if (s.news || s.look) times.push(Math.max(s.now, s.lastWake + MIN_GAP_MS));
  if (s.pushNews) times.push(Math.max(s.now, pushGap(s)));
  if (s.birdweather) times.push(s.now + POLL_MS);
  return Math.max(s.now + 1000, Math.min(...times));
}
