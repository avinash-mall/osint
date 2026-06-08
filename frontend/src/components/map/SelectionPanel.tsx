/**
 * SelectionPanel — right collapsible panel.
 *
 * Hosts four tabs:
 *   - Details:   detection facts, capture provenance, geolocation, taxonomy,
 *                cross-nav buttons (FMV / Graph), affiliation tagging, the
 *                shared ObjectDetailsForm in either Edit or Review mode,
 *                candidate links, action footer.
 *   - Analytics: AnalyticsToolsPanel wrapper (viewshed / LOS / routes).
 *   - Similar:   SimilarPanel — nearest-neighbour detections by embedding.
 *   - Tracks:    Active-tracks list + "Track Object" pin action.
 *
 * Extracted from the GaiaMap monolith (Phase E of the map split). Owns
 * none of the underlying state — every value and callback flows down from
 * GaiaMap so the orchestrator can coordinate cross-panel behaviour
 * (selecting a detection updates the map highlight too).
 */

import { forward as mgrsForward } from 'mgrs';
import { useEffect, useState } from 'react';
import {
  Activity,
  ChevronRight,
  CircleHelp,
  Cpu,
  Crosshair,
  Database,
  FileDown,
  GitBranch,
  Navigation,
  Satellite,
  Send,
  Shield,
  Sparkles,
  Swords,
} from 'lucide-react';

import {
  categoryFor,
  detectionClassLabel,
  type DetectionCategoryMap,
} from '../../utils/detectionTaxonomy';
import type { OntologyBranch } from '../../utils/useOntology';

import {
  detectionCategoryForFeature,
  detectionDisplayLabel,
  detectionProvenance,
  featureCentroid,
  featureLatLonBounds,
  labelQuality,
  type DetectionTrack,
} from './_helpers';
import { CategoryIcon } from './_icons';
import IdentificationPanel from './IdentificationPanel';
import ObjectDetailsForm from '../ObjectDetailsForm';
import ReviewPanel from './ReviewPanel';
import SimilarPanel from './SimilarPanel';
import ProvenancePanel from './ProvenancePanel';
import AnalyticsToolsPanel, {
  type AnalyticsKind,
  type AnalyticsPick,
} from './AnalyticsToolsPanel';
import type { AnalyticsResponse } from '../../services/analytics';
import type { ActiveLayerMap } from './LayerPanel';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type SelectionRightTab = 'details' | 'analytics' | 'satellites' | 'similar' | 'tracks' | 'provenance';
export type SelectionEditTab = 'edit' | 'review';

export type SelectionPanelActions = {
  /** Update `props.allegiance` for the detection (server-side). */
  tagDetection: (id: number, allegiance: string) => void;
  /** Soft-delete the detection (server-side). */
  deleteDetection: (id: number) => void;
  /** Re-fetch the GeoJSON layer (post-save / post-review). */
  fetchDetections: () => void;
  /** Generate candidate target links for the selected detection. */
  addToLinkGraph: () => void;
  /** Trigger a "cue collection" pass schedule for the detection. */
  cueCollection: () => void;
  /** Force-create / pin a track from a detection id. */
  pinTrack: (id: number) => void;
  /** Approve / reject the candidate link (review queue). */
  approveCandidate: (id: number) => void;
  rejectCandidate: (id: number) => void;
};

export type CandidateLink = {
  id: number;
  target_id: string;
  target_name?: string;
  status: 'pending' | 'approved' | 'rejected' | string;
  score?: number;
  reason?: string;
};

type Props = {
  /* tab state */
  rightTab: SelectionRightTab;
  setRightTab: (t: SelectionRightTab) => void;
  selectionTab: SelectionEditTab;
  setSelectionTab: (t: SelectionEditTab) => void;
  onClose: () => void;

  /* selected detection + cross-coupled data */
  selectedDetection: any;
  setSelectedDetection: (feature: any) => void;
  detectionTracks: DetectionTrack[];
  selectedImageryData: any;
  detectionsGeoJSON: { features?: any[]; [k: string]: any };
  candidateLinks: CandidateLink[];

  /* analytics tab */
  pendingPick: AnalyticsPick | null;
  setPendingPick: (p: AnalyticsPick | null) => void;
  lastMapClick: any;
  setLastMapClick: (c: any) => void;
  activeLayers: ActiveLayerMap;
  setActiveLayers: React.Dispatch<React.SetStateAction<ActiveLayerMap>>;
  analyticsResults: Record<string, AnalyticsResponse | null | undefined>;
  setAnalyticsResults: (updater: any) => void;

  /* satellites tab — rendered node supplied by GaiaMap so this panel stays
     decoupled from the satellites service. */
  satellitesSlot?: React.ReactNode;

  /* tracks tab */
  data: { tracks: any[] };

  /* action state */
  isActionBusy: boolean;
  actionStatus: string | null;

  /* taxonomy / ontology lookups */
  categories: DetectionCategoryMap;
  branchById: Map<string, OntologyBranch>;

  /* current user (for canDelete) */
  userRole?: string;

  /* cross-nav */
  onOpenFmv?: (clipId: number) => void;

  /* action callbacks */
  actions: SelectionPanelActions;
};

export default function SelectionPanel(props: Props) {
  const {
    rightTab,
    setRightTab,
    selectionTab,
    setSelectionTab,
    onClose,
    selectedDetection,
    setSelectedDetection,
    detectionTracks,
    selectedImageryData,
    detectionsGeoJSON,
    candidateLinks,
    pendingPick,
    setPendingPick,
    lastMapClick,
    setLastMapClick,
    activeLayers,
    setActiveLayers,
    analyticsResults,
    setAnalyticsResults,
    data,
    isActionBusy,
    actionStatus,
    categories,
    branchById,
    userRole,
    onOpenFmv,
    actions,
    satellitesSlot,
  } = props;

  // Sample DEM elevation at the selected detection centroid. Triggered on
  // detection change; falls back to "—" when the DEM endpoint is unavailable.
  // See docs/backend-routers/analytics-router.md /api/analytics/elevation.
  const detCentroid = selectedDetection ? featureCentroid(selectedDetection) : null;
  const [elevation, setElevation] = useState<{ value: number | null; status: 'idle' | 'loading' | 'unavailable' }>(
    { value: null, status: 'idle' },
  );
  useEffect(() => {
    if (!detCentroid) {
      setElevation({ value: null, status: 'idle' });
      return;
    }
    const [lat, lon] = detCentroid;
    let cancelled = false;
    setElevation({ value: null, status: 'loading' });
    fetch(`${API_URL}/api/analytics/elevation?lat=${lat}&lon=${lon}`, { credentials: 'include' })
      .then((r) => r.ok ? r.json() : Promise.reject(r.status))
      .then((data) => {
        if (cancelled) return;
        const v = typeof data?.elevation_m === 'number' ? data.elevation_m : null;
        setElevation({ value: v, status: v == null ? 'unavailable' : 'idle' });
      })
      .catch(() => { if (!cancelled) setElevation({ value: null, status: 'unavailable' }); });
    return () => { cancelled = true; };
  }, [detCentroid?.[0], detCentroid?.[1]]);

  // Bump to force ObjectDetailsForm to refetch object_details (used after
  // IdentificationPanel approve/reject lands fresh platform_* fields).
  const [objectDetailsRefreshKey, setObjectDetailsRefreshKey] = useState(0);

  const [exportingPkg, setExportingPkg] = useState(false);
  const exportTargetPackage = async () => {
    if (!selectedDetection || exportingPkg) return;
    const id = selectedDetection.properties?.id;
    if (id == null) return;
    setExportingPkg(true);
    try {
      const r = await fetch(`${API_URL}/api/reports/target-package/${id}`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `target-${id}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.warn('Target package export failed', err);
    } finally {
      setExportingPkg(false);
    }
  };

  const rightHeader =
    rightTab === 'analytics' ? { Icon: Sparkles,    label: 'Analytics',     tag: 'ANALYTICS' } :
    rightTab === 'satellites'? { Icon: Satellite,   label: 'Satellites',    tag: 'OVERPASS'  } :
    rightTab === 'similar'   ? { Icon: Crosshair,   label: 'Similar',       tag: 'NEAREST'   } :
    rightTab === 'tracks'    ? { Icon: Navigation,  label: 'Active Tracks', tag: 'TRACKS'    } :
    rightTab === 'provenance'? { Icon: Database,    label: 'Provenance',    tag: 'LINEAGE'   } :
                               { Icon: Crosshair,   label: selectedDetection ? `DET-${selectedDetection.properties?.id}` : 'Selection', tag: 'DETAIL' };
  const HeaderIcon = rightHeader.Icon;
  const allegianceLabel = String(selectedDetection?.properties?.allegiance || '').toLowerCase();
  const allegianceTagClass =
    allegianceLabel === 'hostile'  ? 'crit' :
    allegianceLabel === 'friendly' ? 'ok' :
    allegianceLabel === 'neutral'  ? 'info' :
    'acc';

  return (
    <section
      data-tour="selection-panel"
      className="sentinel-panel map-float-panel map-right-panel selection-panel"
      style={{
        position: 'absolute',
        right: 14,
        top: 14,
        bottom: 14,
        zIndex: 500,
        border: '1px solid var(--line)',
        borderRadius: 10,
        background: 'color-mix(in oklab, var(--bg-1) 94%, transparent)',
        backdropFilter: 'blur(8px)',
        boxShadow: '0 8px 30px rgba(0,0,0,.35)',
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        containerType: 'inline-size',
        containerName: 'selection-panel',
      }}
    >
      <div className="sentinel-panel-header">
        <HeaderIcon className="h-4 w-4" />
        <span>{rightHeader.label}</span>
        {rightTab === 'details' && selectedDetection ? (
          <span data-tour="selection-header-chip" className={`sentinel-tag ${allegianceTagClass} ml-auto uppercase`}>{selectedDetection.properties?.allegiance || 'unknown'}</span>
        ) : (
          <span data-tour="selection-header-chip" className="sentinel-tag acc ml-auto">{rightHeader.tag}</span>
        )}
        <button type="button" data-tour="selection-collapse" onClick={onClose} className="sentinel-icon-btn h-6 w-6" title="Collapse panel">
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex border-b border-sentinel-line bg-sentinel-panel-2">
        {([
          ['details', 'Details'],
          ['analytics', 'Analytics'],
          ['satellites', 'Sat'],
          ['similar', 'Similar'],
          ['provenance', 'Prov'],
          ['tracks', 'Active Tracks'],
        ] as const).map(([k, label]) => {
          const isActive = rightTab === k;
          return (
            <button
              key={k}
              type="button"
              data-tour={`tab-${k}`}
              onClick={() => setRightTab(k)}
              className={`flex-1 h-[34px] font-mono text-[10.5px] uppercase tracking-[.08em] flex items-center justify-center gap-1.5 border-r border-sentinel-line last:border-r-0 ${
                isActive ? 'bg-sentinel-panel text-slate-100' : 'text-sentinel-muted'
              }`}
              style={{ borderBottom: isActive ? '2px solid var(--accent, #ff7a1a)' : '2px solid transparent' }}
            >
              {label}
            </button>
          );
        })}
      </div>
      <div className="sentinel-scroll">
        {rightTab === 'details' && (selectedDetection ? (() => {
          const detProps = selectedDetection.properties || {};
          const category = detectionCategoryForFeature(selectedDetection);
          const categoryMeta = categoryFor(category, categories);
          const confidencePct = Math.round(Number(detProps.confidence || 0) * 100);
          const centroid = featureCentroid(selectedDetection);
          const llBounds = featureLatLonBounds(selectedDetection);
          const mgrsString = centroid
            ? (() => { try { return mgrsForward([centroid[1], centroid[0]], 5); } catch { return null; } })()
            : null;
          const trackForDetection = detectionTracks.find((t) => {
            const ids = (t.metadata as any)?.detection_ids;
            return Array.isArray(ids) && ids.includes(Number(detProps.id));
          });
          const vx = trackForDetection?.last_velocity?.vx_mps;
          const vy = trackForDetection?.last_velocity?.vy_mps;
          const motion = (typeof vx === 'number' && typeof vy === 'number')
            ? (() => {
                const speedMs = Math.sqrt(vx * vx + vy * vy);
                const speedKmh = speedMs * 3.6;
                let bearing = (Math.atan2(vx, vy) * 180) / Math.PI;
                if (bearing < 0) bearing += 360;
                return `${speedKmh.toFixed(1)} km/h · bearing ${String(Math.round(bearing)).padStart(3, '0')}°`;
              })()
            : null;
          const captureSource = selectedImageryData?.name
            ? `${selectedImageryData.name}${selectedImageryData.sensor_type ? ` / ${selectedImageryData.sensor_type}` : ''}`
            : detProps.metadata?.source_cog || 'n/a';
          const captureTime = detProps.metadata?.acquisition_time || selectedImageryData?.acquisition_time;
          const resolution = detProps.metadata?.resolution_m ?? selectedImageryData?.resolution_m;
          const sizeEstimate = detProps.metadata?.size_estimate as
            | {
                length_m: number;
                width_m: number;
                area_m2: number;
                orientation_deg: number;
                uncertainty?: { length_m?: number; width_m?: number; area_m2?: number };
              }
            | undefined;
          const fmtLen = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`);
          const fmtArea = (m2: number) => {
            if (m2 >= 1_000_000) return `${(m2 / 1_000_000).toFixed(2)} km²`;
            if (m2 >= 10_000) return `${(m2 / 10_000).toFixed(2)} ha`;
            return `${Math.round(m2).toLocaleString()} m²`;
          };
          return (
            <>
              <div className="border-b border-sentinel-line p-3">
                <div className="font-mono text-[10px] text-sentinel-muted">DET-{detProps.id} / {detProps.parent_class || detProps.class}</div>
                <div className="mt-1 flex items-center gap-2">
                  <span style={{ color: categoryMeta.color }}><CategoryIcon category={category} branchById={branchById} /></span>
                  <div className="text-lg font-semibold uppercase tracking-wide text-slate-100">
                    {detectionDisplayLabel(detProps) || detectionClassLabel(detProps.class)}
                  </div>
                  {(() => {
                    const lq = labelQuality(detProps);
                    if (lq === 'generic') {
                      return (
                        <span
                          data-testid="label-quality-chip"
                          className="sentinel-tag warn uppercase"
                          title="Detector emitted a generic class; no specific ontology match without a verifier."
                        >
                          generic
                        </span>
                      );
                    }
                    if (lq === 'verified') {
                      return (
                        <span
                          data-testid="label-quality-chip"
                          className="sentinel-tag ok uppercase"
                          title="Confirmed by a label verifier (semantic_margin meets the configured floor)."
                        >
                          verified
                        </span>
                      );
                    }
                    return null;
                  })()}
                  {(() => {
                    // Task 1.3 — [DETECTOR] provenance chip. Blue when ≥1
                    // fusion partner (multi-detector agreement is the
                    // trusted state); neutral grey when alone.
                    const prov = detectionProvenance(detProps);
                    const fused = prov.partners.length > 0;
                    return (
                      <span
                        data-testid="detector-provenance-chip"
                        className={`sentinel-tag ${fused ? 'info' : ''} uppercase`}
                        title={prov.tooltip}
                      >
                        <Cpu size={10} />
                        {prov.primary}
                        {fused ? ` +${prov.partners.length}` : ''}
                      </span>
                    );
                  })()}
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <div className="h-1 flex-1 bg-sentinel-bg">
                    <div className="h-full" style={{ width: `${confidencePct}%`, background: 'var(--accent, #ff7a1a)' }} />
                  </div>
                  <span className="font-mono text-[10px] text-sentinel-muted">{confidencePct}% CONF</span>
                </div>
              </div>

              <div className="border-b border-sentinel-line p-3">
                <div className="flex items-center gap-2 pb-2">
                  <span className="inline-flex h-4 w-4 items-center justify-center bg-sentinel-line-2 font-mono text-[9px] text-slate-200">B</span>
                  <span className="sentinel-label">Capture</span>
                </div>
                <div className="grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10.5px]">
                  <span className="text-sentinel-muted">SOURCE</span><span className="truncate">{captureSource}</span>
                  <span className="text-sentinel-muted">CAPTURE</span><span className="truncate">{captureTime ? new Date(captureTime).toISOString().replace(/\.\d+/, '') : 'n/a'}</span>
                  <span className="text-sentinel-muted">RESOLUTION</span><span>{resolution ? `${Number(resolution).toFixed(2)} m / px` : 'n/a'}</span>
                  <span className="text-sentinel-muted">BBOX</span>
                  <span className="truncate">
                    {llBounds
                      ? `${llBounds.south.toFixed(4)},${llBounds.west.toFixed(4)} → ${llBounds.north.toFixed(4)},${llBounds.east.toFixed(4)}`
                      : 'n/a'}
                  </span>
                </div>
              </div>

              <div className="border-b border-sentinel-line p-3">
                <div className="flex items-center gap-2 pb-2">
                  <span className="inline-flex h-4 w-4 items-center justify-center bg-sentinel-line-2 font-mono text-[9px] text-slate-200">C</span>
                  <span className="sentinel-label">Geolocation</span>
                </div>
                <div className="grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10.5px]">
                  <span className="text-sentinel-muted">WGS84</span>
                  <span>{centroid ? `${centroid[0].toFixed(4)}° N, ${centroid[1].toFixed(4)}° E` : 'n/a'}</span>
                  <span className="text-sentinel-muted">MGRS</span><span>{mgrsString || 'n/a'}</span>
                  <span className="text-sentinel-muted">ELEV</span>
                  <span>
                    {elevation.status === 'loading'
                      ? '…'
                      : elevation.status === 'unavailable' || elevation.value == null
                        ? '—'
                        : `${elevation.value.toFixed(0)} m MSL`}
                  </span>
                  <span className="text-sentinel-muted">MOTION</span><span>{motion || 'static'}</span>
                </div>
                <button
                  type="button"
                  onClick={exportTargetPackage}
                  disabled={exportingPkg}
                  className="mt-3 inline-flex w-full items-center justify-center gap-2 border border-sentinel-line bg-sentinel-bg px-2 py-1.5 font-mono text-[10.5px] uppercase tracking-[.08em] text-slate-200 hover:bg-sentinel-panel disabled:opacity-50"
                  title="Generate a PDF Target Package for this detection"
                >
                  <FileDown size={12} />
                  {exportingPkg ? 'Generating…' : 'Generate Target Package'}
                </button>
              </div>

              {sizeEstimate && (
                <div className="border-b border-sentinel-line p-3">
                  <div className="flex items-center gap-2 pb-2">
                    <span className="inline-flex h-4 w-4 items-center justify-center bg-sentinel-line-2 font-mono text-[9px] text-slate-200">D</span>
                    <span className="sentinel-label">Dimensions</span>
                  </div>
                  <div className="grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10.5px]">
                    <span className="text-sentinel-muted">L × W</span>
                    <span>
                      {fmtLen(sizeEstimate.length_m)} × {fmtLen(sizeEstimate.width_m)}
                      {sizeEstimate.uncertainty?.length_m != null && (
                        <span className="text-sentinel-muted"> (±{Math.max(1, Math.round(sizeEstimate.uncertainty.length_m))} m)</span>
                      )}
                    </span>
                    <span className="text-sentinel-muted">AREA</span>
                    <span>{fmtArea(sizeEstimate.area_m2)}</span>
                    <span className="text-sentinel-muted">BEARING</span>
                    <span>{String(Math.round(sizeEstimate.orientation_deg)).padStart(3, '0')}°</span>
                  </div>
                </div>
              )}

              <div className="border-b border-sentinel-line p-3">
                <div className="flex items-center gap-2 pb-2">
                  <span className="inline-flex h-4 w-4 items-center justify-center bg-sentinel-line-2 font-mono text-[9px] text-slate-200">E</span>
                  <span className="sentinel-label">Taxonomy</span>
                </div>
                <div className="grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10.5px]">
                  <span className="text-sentinel-muted">CLASS</span><span className="truncate">{detProps.class || 'n/a'}</span>
                  <span className="text-sentinel-muted">VERSION</span><span>{detProps.metadata?.taxonomy_version || 'n/a'}</span>
                  <span className="text-sentinel-muted">MODEL</span><span>{detProps.metadata?.model_version || 'n/a'}</span>
                </div>
              </div>

              <IdentificationPanel
                detectionId={Number(detProps.id)}
                onChanged={() => {
                  // Approve/reject landed platform_* on object_details — force
                  // ObjectDetailsForm to refetch and refresh the GeoJSON layer.
                  setObjectDetailsRefreshKey((k) => k + 1);
                  actions.fetchDetections();
                }}
              />

              <div className="grid grid-cols-2 gap-2 border-b border-sentinel-line p-3">
                <button
                  type="button"
                  disabled={!detProps.fmv_clip_id || !onOpenFmv}
                  onClick={() => detProps.fmv_clip_id && onOpenFmv && onOpenFmv(Number(detProps.fmv_clip_id))}
                  className="sentinel-btn justify-center disabled:opacity-40"
                >
                  OPEN IN FMV →
                </button>
                <button
                  type="button"
                  disabled={isActionBusy}
                  onClick={actions.addToLinkGraph}
                  className="sentinel-btn justify-center disabled:opacity-40"
                >
                  OPEN IN GRAPH →
                </button>
              </div>

              <div className="grid grid-cols-2 gap-2 border-b border-sentinel-line p-3">
                <button type="button" disabled={isActionBusy} onClick={() => actions.tagDetection(detProps.id, 'friendly')} className="sentinel-btn justify-center disabled:opacity-40"><Shield className="h-3.5 w-3.5" /> Friendly</button>
                <button type="button" disabled={isActionBusy} onClick={() => actions.tagDetection(detProps.id, 'hostile')} className="sentinel-btn justify-center disabled:opacity-40"><Swords className="h-3.5 w-3.5" /> Hostile</button>
                <button type="button" disabled={isActionBusy} onClick={() => actions.tagDetection(detProps.id, 'neutral')} className="sentinel-btn justify-center disabled:opacity-40"><CircleHelp className="h-3.5 w-3.5" /> Neutral</button>
                <button type="button" disabled={isActionBusy} onClick={() => actions.tagDetection(detProps.id, 'unknown')} className="sentinel-btn justify-center disabled:opacity-40">Clear</button>
              </div>

              <div className="flex border-b border-sentinel-line">
                {(['edit', 'review'] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setSelectionTab(k)}
                    className={`flex-1 px-2 py-2 font-mono text-[10.5px] uppercase tracking-widest transition border-b-2 ${
                      selectionTab === k
                        ? 'border-sentinel-accent text-sentinel-accent bg-sentinel-panel-2'
                        : 'border-transparent text-sentinel-muted hover:text-slate-200'
                    }`}
                  >
                    {k}
                  </button>
                ))}
              </div>

              {selectionTab === 'edit' && (
                <ObjectDetailsForm
                  key={`map-det-${detProps.id}-${objectDetailsRefreshKey}`}
                  source="map"
                  detectionId={Number(detProps.id)}
                  defaultClass={detProps.class}
                  title={detProps.label || detectionClassLabel(detProps.class)}
                  initial={{
                    designation: detProps.metadata?.designation,
                    military_classification: detProps.metadata?.military_classification,
                    threat_level: detProps.threat_level,
                    affiliation: detProps.allegiance,
                  }}
                  canDelete={
                    (detProps.source || detProps.metadata?.source) === 'operator'
                    || userRole === 'admin'
                  }
                  onDeleted={() => actions.deleteDetection(Number(detProps.id))}
                  onSaved={() => actions.fetchDetections()}
                  onViewInFmv={
                    detProps.fmv_clip_id && onOpenFmv
                      ? () => onOpenFmv(Number(detProps.fmv_clip_id))
                      : undefined
                  }
                />
              )}
              {selectionTab === 'review' && (
                <ReviewPanel
                  selectedDetection={selectedDetection}
                  onReviewed={() => actions.fetchDetections()}
                  onJump={(id) => {
                    const feat = detectionsGeoJSON?.features?.find(
                      (f: any) => Number(f.properties?.id) === id,
                    );
                    if (feat) setSelectedDetection(feat);
                  }}
                />
              )}

              <div className="border-b border-sentinel-line p-3">
                <div className="mb-2 flex items-center gap-2">
                  <span className="sentinel-label flex-1">Candidate Links</span>
                  <span className="sentinel-tag">{candidateLinks.length}</span>
                </div>
                {candidateLinks.length === 0 && (
                  <div className="text-[11px] text-sentinel-muted">No candidate target links. Use Add To Link Graph to generate review candidates.</div>
                )}
                <div className="space-y-2">
                  {candidateLinks.slice(0, 4).map((candidate) => (
                    <div key={candidate.id} className="border border-sentinel-line bg-sentinel-bg p-2">
                      <div className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-xs text-slate-200">{candidate.target_name || candidate.target_id}</span>
                        <span className={`sentinel-tag ${candidate.status === 'approved' ? 'ok' : candidate.status === 'rejected' ? 'crit' : 'warn'}`}>{candidate.status}</span>
                      </div>
                      <div className="mt-1 font-mono text-[10px] text-sentinel-muted">{Math.round(Number(candidate.score || 0) * 100)} score / {candidate.reason}</div>
                      {candidate.status === 'pending' && (
                        <div className="mt-2 grid grid-cols-2 gap-2">
                          <button type="button" disabled={isActionBusy} onClick={() => actions.approveCandidate(candidate.id)} className="sentinel-btn justify-center disabled:opacity-40">Approve</button>
                          <button type="button" disabled={isActionBusy} onClick={() => actions.rejectCandidate(candidate.id)} className="sentinel-btn justify-center disabled:opacity-40">Reject</button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <div className="sentinel-panel-header">
                <Activity className="h-4 w-4" />
                <span>Actions</span>
              </div>
              <div className="space-y-2 p-3">
                <button
                  type="button"
                  disabled={isActionBusy || !selectedDetection}
                  onClick={actions.cueCollection}
                  className="sentinel-btn primary w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Send className="h-3.5 w-3.5" /> Cue Collection
                </button>
                <button
                  type="button"
                  disabled={isActionBusy || !selectedDetection}
                  onClick={actions.addToLinkGraph}
                  className="sentinel-btn w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <GitBranch className="h-3.5 w-3.5" /> Add To Link Graph
                </button>
                <div className="min-h-8 border border-sentinel-line bg-sentinel-bg px-2 py-1 font-mono text-[10px] text-sentinel-muted">
                  {actionStatus || 'Detection action ready.'}
                </div>
              </div>
            </>
          );
        })() : (
          <div className="border-b border-sentinel-line p-3 text-xs text-sentinel-muted">Select a detection polygon to inspect classification details.</div>
        ))}

        {rightTab === 'analytics' && (
          <AnalyticsToolsPanel
            pendingPick={pendingPick}
            onRequestPick={setPendingPick}
            lastMapClick={lastMapClick}
            layers={{
              viewshed: { on: !!activeLayers.viewshed, disabled: !analyticsResults.viewshed },
              los: { on: !!activeLayers.los, disabled: !analyticsResults.los },
              routes: { on: !!activeLayers.routes, disabled: !analyticsResults.routes },
            }}
            onToggleLayer={(kind: AnalyticsKind) =>
              setActiveLayers((prev) => ({ ...prev, [kind]: !prev[kind] }))
            }
            onResult={(kind: AnalyticsKind, response: AnalyticsResponse | null) => {
              setAnalyticsResults((prev: any) => ({ ...prev, [kind]: response }));
              if (response) setActiveLayers((prev) => ({ ...prev, [kind]: true }));
              setLastMapClick(null);
            }}
          />
        )}

        {rightTab === 'satellites' && satellitesSlot}

        {rightTab === 'similar' && (
          selectedDetection ? (
            <SimilarPanel
              selectedDetection={selectedDetection}
              onSelect={(id) => {
                const feat = detectionsGeoJSON?.features?.find(
                  (f: any) => Number(f.properties?.id) === id,
                );
                if (feat) setSelectedDetection(feat);
              }}
            />
          ) : (
            <div className="border-b border-sentinel-line p-3 text-xs text-sentinel-muted">Select a detection polygon to inspect similar objects.</div>
          )
        )}

        {rightTab === 'provenance' && (
          <ProvenancePanel selectedDetection={selectedDetection} />
        )}

        {rightTab === 'tracks' && (
          <>
            <div className="sentinel-panel-header">
              <Navigation className="h-4 w-4" />
              <span>Active Tracks</span>
              <span className="sentinel-tag info ml-auto">{data.tracks.length}</span>
            </div>
            <div className="border-b border-sentinel-line p-3">
              <button
                type="button"
                data-tour="tracks-track-object"
                disabled={isActionBusy || !selectedDetection}
                onClick={() => selectedDetection && actions.pinTrack(selectedDetection.properties.id)}
                className="sentinel-btn w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
                title={selectedDetection ? 'Force-create a track from the selected detection' : 'Select a detection first'}
              >
                <Crosshair className="h-3.5 w-3.5" /> Track Object
              </button>
            </div>
            {data.tracks.length === 0 ? (
              <div className="border-b border-sentinel-line p-3 text-[11px] text-sentinel-muted">No active tracks.</div>
            ) : (
              data.tracks.map((track: any) => (
                <div key={track.id} className="sentinel-row grid-cols-[1fr_auto]">
                  <span className="min-w-0">
                    <span className="block truncate text-xs text-slate-200">{track.properties?.callsign || track.asset_id || track.id}</span>
                    <span className="block truncate font-mono text-[10px] text-sentinel-muted">{track.label}</span>
                  </span>
                  <span className="sentinel-tag info">LIVE</span>
                </div>
              ))
            )}
          </>
        )}
      </div>
    </section>
  );
}
