import type { Page, Route } from '@playwright/test';

type MockOptions = { authenticated?: boolean };
const NOW = '2026-05-16T03:00:00.000Z';
const ONE_PIXEL_PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aX0AAAAASUVORK5CYII=';

const ontology = {
  version_id: 18,
  branches: [
    {
      id: 'vehicles', parent_id: null, label: 'Vehicles', color: '#ff7a1a', short: 'VEH', icon_key: 'truck',
      matchers: ['tank', 'truck'], sensors: ['rgb', 'fmv'], order_index: 1,
      objects: [{ id: 'tank', branch_id: 'vehicles', label: 'Tank', prompt: 'main battle tank', sensors: ['rgb', 'fmv'], min_gsd_meters: 0.3, icon_key: 'truck', order_index: 1 }],
      children: [],
    },
    {
      id: 'facilities', parent_id: null, label: 'Facilities', color: '#4ea1ff', short: 'FAC', icon_key: 'building',
      matchers: ['hangar'], sensors: ['rgb'], order_index: 2,
      objects: [{ id: 'hangar', branch_id: 'facilities', label: 'Hangar', prompt: 'aircraft hangar', sensors: ['rgb'], min_gsd_meters: null, icon_key: 'building', order_index: 1 }],
      children: [],
    },
  ],
};

const geojson = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [[[54.92, 24.98], [54.96, 24.98], [54.96, 25.02], [54.92, 25.02], [54.92, 24.98]]] },
    properties: {
      id: 1, class: 'tank', label: 'Tank', parent_class: 'tank', original_class: 'tank', branch_id: 'vehicles', confidence: 0.93,
      threat_level: 'high', allegiance: 'unknown', review_status: 'review_candidate',
      metadata: { designation: 'Track Alpha', branch_id: 'vehicles', original_class: 'tank', parent_class: 'tank', model_version: 'sam3-visual', taxonomy_version: 'v18', embedding: true, embedding_head: 'sat', coverage_fraction: 0.82, threshold_profile: 'imagery' },
    },
  }],
};

const fmvDetections = [{ id: 11, clip_id: 7, frame_index: 42, class: 'tank', confidence: 0.91, bbox: [0.5, 0.5, 0.2, 0.2], metadata: { track_id: 'A-17', affiliation: 'unknown', uses_multiplex: true } }];
const fulfillJson = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

export async function installMockApi(page: Page, options: MockOptions = {}) {
  const authenticated = options.authenticated ?? true;
  await page.addInitScript((fixedNow) => {
    const NativeDate = Date;
    class FixedDate extends NativeDate {
      constructor(...args: any[]) { super(...(args.length ? args : [fixedNow])); }
      static now() { return new NativeDate(fixedNow).getTime(); }
    }
    // @ts-expect-error visual-test clock override
    window.Date = FixedDate;
  }, NOW);

  const png = Buffer.from(ONE_PIXEL_PNG, 'base64');
  await page.route('**/basemap/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: png }));
  await page.route('**/tiles/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: png }));

  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return authenticated ? fulfillJson(route, { user: { username: 'ava.chen', display_name: 'Ava Chen', email: 'ava@example.test' }, role: 'admin' }) : fulfillJson(route, { detail: 'unauthorized' }, 401);
    if (path === '/api/health') return fulfillJson(route, { healthy: true, neo4j: 'ok', postgis: 'ok' });
    if (path === '/api/ingest/uploads') return fulfillJson(route, { uploads: [] });
    if (path === '/api/ontology') return fulfillJson(route, ontology);
    if (path === '/api/ontology/version') return fulfillJson(route, { version_id: ontology.version_id });
    if (path === '/api/ontology/unknown-labels') return fulfillJson(route, { unknown_labels: [{ label: 'mobile_launcher', count: 4, first_seen: '2026-05-16T02:20:00.000Z', layer: 'sam3' }] });
    if (path === '/api/geotime/features') return fulfillJson(route, { static: [], tracks: [] });
    if (path === '/api/imagery') return fulfillJson(route, { imagery: [{ id: 5, name: 'visual-pass-05', sensor_type: 'Optical', cloud_cover: 4, acquisition_time: '2026-05-16T02:30:00.000Z', file_path: '/fixtures/imagery.tif' }] });
    if (path === '/api/detections/classes') return fulfillJson(route, { classes: [{ class: 'tank', label: 'Tank', count: 1, max_confidence: 0.93, branch_id: 'vehicles', threat_level: 'high' }] });
    if (path === '/api/detections/geojson') return fulfillJson(route, geojson);
    if (path === '/api/detections/prithvi-overlays') return fulfillJson(route, { type: 'FeatureCollection', features: [] });
    if (path === '/api/tracks/detections') return fulfillJson(route, { tracks: [] });
    if (path === '/api/basemap/countries') return fulfillJson(route, { type: 'FeatureCollection', features: [] });
    if (path === '/api/detections/1/candidate-links') return fulfillJson(route, { candidates: [] });
    if (path === '/api/detections/1/details') return fulfillJson(route, { details: { object_class: 'tank', designation: 'Track Alpha', threat_level: 'high', affiliation: 'unknown', notes: 'Visual fixture' } });
    if (path === '/api/detections/queue') return fulfillJson(route, { detections: [] });
    if (path === '/api/detections/1/similar') return fulfillJson(route, { results: [] });
    if (path === '/api/analytics/capabilities') return fulfillJson(route, { dem: true, routing_graph: true });
    if (path === '/api/graph') return fulfillJson(route, { nodes: [{ id: 'veh-1', label: 'vehicle', name: 'Vehicle Alpha', properties: { class: 'tank' } }, { id: 'fac-1', label: 'facility', name: 'Forward Hangar', properties: { class: 'hangar' } }], links: [{ source: 'veh-1', target: 'fac-1', type: 'observed_near', score: 0.87 }] });
    if (path === '/api/ontology/updates') return fulfillJson(route, { updates: [{ id: 3, status: 'pending_review', summary: 'Tank matcher refined.' }] });
    if (path === '/api/fmv/clips') return fulfillJson(route, { clips: [{ id: 7, name: 'visual-sortie-07.mp4', file_path: '/fixtures/visual-sortie-07.mp4', hls_path: null, duration_seconds: 88, width: 1280, height: 720, fps: 30, status: 'ready', stream_url: '', metadata: {} }] });
    if (path === '/api/fmv/clips/7/klv') return fulfillJson(route, { frames: [{ frame_index: 42, timestamp_seconds: 1.4, telemetry: { source: 'misb-klv', platform_latitude: 25, platform_longitude: 55, frame_center_latitude: 25.001, frame_center_longitude: 55.001 }, footprint: null }] });
    if (path === '/api/fmv/clips/7/detections') return fulfillJson(route, { detections: fmvDetections });
    if (path === '/api/fmv/detections/11/details') return fulfillJson(route, { details: {} });
    if (path === '/api/fmv/detections/11/similar') return fulfillJson(route, { results: [] });
    if (path === '/api/inference/load') return fulfillJson(route, { ok: true });
    if (path === '/api/analytics/jobs' || path === '/api/training/jobs') return fulfillJson(route, { jobs: [] });
    if (path === '/api/inference/health') return fulfillJson(route, { model_loaded: true, current_profile: 'imagery', gpu_model: 'Fixture GPU', model_versions: { sam3_image: '3.1', dinov3_sat: 'v1' }, load_flags: { dinov3_sat: true } });
    if (path === '/api/alerts') return fulfillJson(route, { alerts: [] });
    if (path === '/api/inference/dashboard') return fulfillJson(route, { gpu: { model: 'Fixture GPU', profile: 'imagery', cuda_version: '12.8' }, mode: 'online', device: 'cuda:0', vram_total_gib: 24, vram_used_gib: 9.4, profile_loaded: 'imagery', available_profiles: ['imagery', 'fmv'], pool_size: 1, replicas: [{ device: 'cuda:0', components: { sam3_image: true, dinov3_sat: true } }], active_requests: 1, uptime_s: 3600, system: { cpu_pct: 12, ram_used_gib: 18, ram_total_gib: 64, disk_used_gib: 80, disk_total_gib: 512 }, request_rate_60s: 0.4, models: [{ id: 'sam3_image', name: 'SAM3 image', version: '3.1', status: 'online', requests: 8, errors: 0 }] });
    if (path === '/api/admin/auth/config') return fulfillJson(route, { enabled: false });
    if (path === '/api/ontology/version-history') return fulfillJson(route, { current_version_id: 18, versions: [{ id: 1, version_id: 18, summary: 'Fixture taxonomy', created_at: NOW }] });
    if (path === '/api/ontology/prompt-profiles') return fulfillJson(route, { profiles: [], ontology_defaults: { optical: ['tank', 'hangar'] } });
    if (path === '/api/inference/confidence-overrides') return fulfillJson(route, { per_class_confidence_overrides: { tank: 0.7 }, env_per_class_confidence_overrides: {}, global_floor: 0.1, env_global_floor: 0.1, high_confidence_threshold: 0.85, env_high_confidence_threshold: 0.85 });
    return fulfillJson(route, {});
  });
}
