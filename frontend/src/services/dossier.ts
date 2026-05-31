/**
 * Area dossier service (Tier C) — offline right-click context lookup.
 *
 * Resolves the country at a map point from the locally-baked `ne_countries`
 * table and counts nearby Sentinel detections. No internet — see
 * docs/backend-routers/imagery-router.md (`/api/dossier`).
 */

import axios from 'axios';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type Dossier = {
  point: { lat: number; lon: number };
  country: {
    name: string | null;
    admin: string | null;
    iso_a3: string | null;
    pop_est: number | null;
    gdp_md_est: number | null;
  } | null;
  detections_within_25km: number;
  source: string;
};

export async function fetchDossier(lat: number, lon: number): Promise<Dossier> {
  const r = await axios.get<Dossier>(`${API_URL}/api/dossier`, { params: { lat, lon } });
  return r.data;
}
