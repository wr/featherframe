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
  named: number;        // ms; 0 when none
  news: boolean;
  look: boolean;
  birdweather: boolean; // a BirdWeather station is polled (and not in quiet hours)
}

export function due(s: WakeState): boolean {
  return (s.named > 0 && s.named <= s.now)
    || ((s.news || s.look) && s.now - s.lastWake >= MIN_GAP_MS)
    || s.now - s.lastWake >= SAFETY_MS;
}

export function nextAlarm(s: WakeState): number {
  const times = [s.lastWake + SAFETY_MS];
  if (s.named > s.now) times.push(s.named);
  if (s.news || s.look) times.push(Math.max(s.now, s.lastWake + MIN_GAP_MS));
  if (s.birdweather) times.push(s.now + POLL_MS);
  return Math.max(s.now + 1000, Math.min(...times));
}
