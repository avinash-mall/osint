/**
 * Satellites service: typed wrappers over /api/satellites/* (offline overpass).
 *
 * TLEs are analyst-imported (air-gap) and stored server-side; prediction is pure
 * SGP4 maths on the backend, so this works in a fully offline deployment. See
 * docs/backend-routers/satellites-router.md.
 *
 * Mirrors services/analytics.ts: axios direct against VITE_API_URL.
 */

import axios from 'axios';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type StoredTle = {
  norad_id: number;
  name: string;
  epoch: string | null;
  source: string | null;
  imported_at: string;
};

export type OverpassPass = {
  aos: string;
  los: string;
  max_elevation_deg: number;
  max_elevation_time: string;
  duration_s: number;
};

export type SatellitePasses = {
  norad_id: number;
  name: string;
  passes: OverpassPass[];
};

export type OverpassResponse = {
  observer: { lat: number; lon: number };
  window: { start: string; end: string };
  satellites: SatellitePasses[];
};

export type GroundTrack = {
  norad_id: number;
  name: string;
  coordinates: [number, number][]; // [lon, lat]
  altitudes_km: number[];
};

export type OverpassRequest = {
  norad_ids?: number[];
  aoi_id?: number;
  lat?: number;
  lon?: number;
  start?: string;
  end?: string;
  hours?: number;
  min_elevation_deg?: number;
  step_s?: number;
};

export async function listTles(): Promise<StoredTle[]> {
  const r = await axios.get<{ tles: StoredTle[] }>(`${API_URL}/api/satellites/tle`);
  return r.data.tles;
}

export async function importTle(text: string, source?: string): Promise<{ imported: number }> {
  const r = await axios.post<{ imported: number }>(`${API_URL}/api/satellites/tle`, { text, source });
  return r.data;
}

export async function predictPasses(req: OverpassRequest): Promise<OverpassResponse> {
  const r = await axios.post<OverpassResponse>(`${API_URL}/api/satellites/passes`, req);
  return r.data;
}

export async function getGroundTrack(
  noradId: number,
  hours = 1.5,
  stepS = 60,
): Promise<GroundTrack> {
  const r = await axios.get<GroundTrack>(`${API_URL}/api/satellites/ground-track/${noradId}`, {
    params: { hours, step_s: stepS },
  });
  return r.data;
}
