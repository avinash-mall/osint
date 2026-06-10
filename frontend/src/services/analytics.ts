import axios from 'axios';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type LatLon = { latitude: number; longitude: number };

export type AnalyticsMode = 'dem' | 'osrm' | 'fixture_no_dem' | 'fixture_no_passes' | 'fixture_no_graph' | string;

export type AnalyticsJob = {
  id: number | string;
  job_type: string;
  status: string;
  input?: any;
  result?: any;
  created_at?: string;
};

export type AnalyticsResponse = {
  job: AnalyticsJob;
  result: GeoJSON.FeatureCollection & { mode?: AnalyticsMode };
};

export type AnalyticsCapabilities = {
  dem: boolean;
  routing: boolean;
  demo_fixtures?: boolean;
};

export async function getCapabilities(): Promise<AnalyticsCapabilities> {
  const r = await axios.get<AnalyticsCapabilities>(`${API_URL}/api/analytics/capabilities`);
  return r.data;
}

export async function runViewshed(args: {
  observer: LatLon;
  radius_m: number;
  observer_height_m?: number;
  target_height_m?: number;
}): Promise<AnalyticsResponse> {
  const r = await axios.post<AnalyticsResponse>(`${API_URL}/api/analytics/viewshed`, args);
  return r.data;
}

export async function runLineOfSight(args: {
  observer: LatLon;
  destination: LatLon;
  observer_height_m?: number;
  target_height_m?: number;
}): Promise<AnalyticsResponse> {
  const r = await axios.post<AnalyticsResponse>(`${API_URL}/api/analytics/los`, args);
  return r.data;
}

export async function runRoutes(args: {
  observer: LatLon;
  destination: LatLon;
  strategy?: 'shortest' | 'least_exposure' | 'balanced';
}): Promise<AnalyticsResponse> {
  const r = await axios.post<AnalyticsResponse>(`${API_URL}/api/analytics/routes`, args);
  return r.data;
}

export async function runIsochrone(args: {
  observer: LatLon;
  minutes: number;
  nominal_speed_kmh?: number;
}): Promise<AnalyticsResponse> {
  const r = await axios.post<AnalyticsResponse>(`${API_URL}/api/analytics/isochrone`, args);
  return r.data;
}

export async function runODFlows(args: {
  cell_deg?: number;
  min_flow?: number;
}): Promise<AnalyticsResponse> {
  const r = await axios.post<AnalyticsResponse>(`${API_URL}/api/analytics/od-flows`, args);
  return r.data;
}
