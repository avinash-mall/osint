import axios from 'axios';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type LatLon = { latitude: number; longitude: number };

export type AnalyticsMode = 'dem' | 'graph' | 'fixture_no_dem' | 'fixture_no_graph' | 'offline_fixture' | string;

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
  routing_graph: boolean;
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
