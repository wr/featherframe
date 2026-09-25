// The label card's data and wording.
export interface Species { name: string; latin: string; heard: string }
export interface Size { label: string; model: string; poster: string; waveform: 'spectra6' | 'gc16'; screens: string[] }
export interface SiteData { species: Species[]; sizes: Record<'13' | '10', Size> }

/** "This morning at 08:14": the part of the day, then the time on a 24-hour clock. */
export function heardText(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  const part = h < 5 ? 'Last night' : h < 12 ? 'This morning' : h < 17 ? 'This afternoon' : 'This evening';
  return `${part} at ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** Names past this length set smaller in the label, so none clips or breaks badly. */
export const LONG_NAME = 18;
export const isLongName = (name: string) => name.length > LONG_NAME;
