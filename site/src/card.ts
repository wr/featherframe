// The site's species data (public/species.json).
export interface Species { name: string; latin: string; heard: string }
export interface Size { label: string; model: string; poster: string; waveform: 'spectra6' | 'gc16'; screens: string[] }
export interface SiteData { species: Species[]; sizes: Record<'13' | '10', Size> }
