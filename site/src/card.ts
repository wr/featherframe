// The label card's data and wording.
export interface Species { name: string; latin: string; heard: string }
export interface Size { label: string; model: string; poster: string; waveform: 'spectra6' | 'gc16'; screens: string[] }
export interface SiteData { species: Species[]; sizes: Record<'13' | '10', Size> }

export function heardText(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  const part = h < 5 ? 'last night' : h < 12 ? 'this morning' : h < 17 ? 'this afternoon' : 'this evening';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `Heard at ${h12}:${String(m).padStart(2, '0')} ${part}`;
}

/** Names past this length set smaller in the label, so none clips or breaks badly. */
export const LONG_NAME = 18;
export const isLongName = (name: string) => name.length > LONG_NAME;
