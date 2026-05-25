/**
 * LayerPanel — left collapsible "operating picture" panel.
 *
 * Extracted from the GaiaMap monolith (Phase C of the map split).
 * Owns its own scroll region but every piece of state it mutates lives in
 * GaiaMap and flows down as props — that keeps cross-panel coordination
 * (selecting an imagery row pans the map, hiding a category toggles the
 * map's GeoJSON layer) in one place.
 *
 * The panel houses:
 *   - basemap selector + opacity slider
 *   - Overlays (satellite / detections / tracks / static / grid / analytics)
 *   - Detection Classes tree (CAT or SRC group mode, ALL/NONE/INV, search,
 *     per-class hide/solo, LLM advisory pills)
 *   - Imagery list (sat scenes; clicking one drives selectedImagery)
 */

import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Layers,
  Lock,
  RefreshCw,
  Satellite,
  Search,
} from 'lucide-react';
import {
  categoryFor,
  type DetectionCategoryId,
  type DetectionCategoryMap,
} from '../../utils/detectionTaxonomy';
import type { OntologyBranch } from '../../utils/useOntology';
import { threatClass, type DetectionClassStat } from './_helpers';
import { BasemapThumb, CategoryIcon, DetectionSubclassIcon } from './_icons';
import { BASEMAP_OVERLAY_MAX_ZOOM } from './MapStage';

export type ActiveLayerMap = {
  satellite: boolean;
  detections: boolean;
  tracks: boolean;
  detectionTracks: boolean;
  static: boolean;
  borders: boolean;
  graticule: boolean;
  viewshed: boolean;
  los: boolean;
  routes: boolean;
};

export type DetectionGroup = {
  id: string;
  label: string;
  short: string;
  color: string;
  count: number;
  classes: DetectionClassStat[];
};

export type BaseLayer = 'base' | 'sat' | 'terrain';

type Props = {
  /* Header */
  onRefresh: () => void;
  onCollapse: () => void;

  /* Basemap selector */
  activeBaseLayer: BaseLayer;
  setActiveBaseLayer: (l: BaseLayer) => void;
  layerOpacities: Record<'base' | 'terrain', number>;
  setLayerOpacities: React.Dispatch<React.SetStateAction<Record<'base' | 'terrain', number>>>;
  /** Current Leaflet zoom — used to autohide the BASE/TERRAIN overlay past the offline bake ceiling. */
  mapZoom: number;

  /* Overlays section */
  overlaysOpen: boolean;
  setOverlaysOpen: React.Dispatch<React.SetStateAction<boolean>>;
  activeLayers: ActiveLayerMap;
  setActiveLayers: React.Dispatch<React.SetStateAction<ActiveLayerMap>>;

  /* Counts for overlay rows */
  imagery: any[];
  visibleDetectionCount: number;
  tracksCount: number;
  staticCount: number;
  analyticsCounts: {
    viewshed: number; viewshedAvailable: boolean;
    los: number;     losAvailable: boolean;
    routes: number;  routesAvailable: boolean;
  };

  /* Detection classes section */
  detectionGroups: DetectionGroup[];
  detectionGroupMode: 'CAT' | 'SRC';
  setDetectionGroupMode: (m: 'CAT' | 'SRC') => void;
  detectionLabelSearch: string;
  setDetectionLabelSearch: (s: string) => void;
  expandedDetectionGroups: string[];
  hiddenDetectionCategories: DetectionCategoryId[];
  hiddenDetectionLabels: string[];
  detectionClassFilter: string | null;
  maxDetectionLabelCount: number;
  branchById: Map<string, OntologyBranch>;
  categories: DetectionCategoryMap;
  showAllDetectionClasses: () => void;
  hideAllDetectionClasses: () => void;
  invertDetectionClasses: () => void;
  toggleDetectionGroupExpanded: (id: string) => void;
  toggleDetectionGroupVisibility: (group: DetectionGroup) => void;
  toggleDetectionClassVisibility: (rawClass: string) => void;
  soloDetectionClass: (rawClass: string) => void;

  /* Imagery list */
  selectedImagery: number | null;
  setSelectedImagery: (id: number | null) => void;
};

/**
 * One overlay row. The full row is the click target; the 10 px coloured dot
 * (filled = on, hollow = off) is the visibility *signal*, not the
 * affordance. Disabled analytics tools show a lock instead — see
 * docs/decisions/why-layerpanel-dot-toggle.md.
 */
function OverlayRow({
  label,
  metric,
  colorVar,
  active,
  disabled = false,
  onToggle,
}: {
  label: string;
  metric: number | string;
  colorVar: string;
  active: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onToggle}
      className={`layer-panel-overlay-row ${active && !disabled ? 'is-on' : 'is-off'} ${disabled ? 'is-disabled' : ''}`}
      title={disabled ? 'Run the tool first to enable this layer' : ''}
      aria-pressed={!disabled && active}
    >
      {disabled ? (
        <Lock className="layer-panel-overlay-dot is-lock" aria-hidden />
      ) : (
        <span
          className={`layer-panel-overlay-dot ${active ? 'is-on' : 'is-off'}`}
          style={{ ['--dot-color' as string]: colorVar }}
          aria-hidden
        />
      )}
      <span className="layer-panel-overlay-label">{label}</span>
      {!disabled && <span className="layer-panel-overlay-metric">{metric}</span>}
    </button>
  );
}

export default function LayerPanel({
  onRefresh,
  onCollapse,
  activeBaseLayer,
  setActiveBaseLayer,
  layerOpacities,
  setLayerOpacities,
  mapZoom,
  overlaysOpen,
  setOverlaysOpen,
  activeLayers,
  setActiveLayers,
  imagery,
  visibleDetectionCount,
  tracksCount,
  staticCount,
  analyticsCounts,
  detectionGroups,
  detectionGroupMode,
  setDetectionGroupMode,
  detectionLabelSearch,
  setDetectionLabelSearch,
  expandedDetectionGroups,
  hiddenDetectionCategories,
  hiddenDetectionLabels,
  detectionClassFilter,
  maxDetectionLabelCount,
  branchById,
  categories,
  showAllDetectionClasses,
  hideAllDetectionClasses,
  invertDetectionClasses,
  toggleDetectionGroupExpanded,
  toggleDetectionGroupVisibility,
  toggleDetectionClassVisibility,
  soloDetectionClass,
  selectedImagery,
  setSelectedImagery,
}: Props) {
  const liveLayerRows = [
    { key: 'satellite',  label: 'Satellite Imagery', metric: imagery.length,        colorVar: 'var(--color-sentinel-info)'   },
    { key: 'detections', label: 'AI Detections',     metric: visibleDetectionCount, colorVar: 'var(--color-sentinel-accent)' },
    { key: 'tracks',     label: 'Active Tracks',     metric: tracksCount,           colorVar: 'var(--color-sentinel-info)'   },
    { key: 'static',     label: 'Static Features',   metric: staticCount,           colorVar: 'var(--color-sentinel-crit)'   },
    { key: 'borders',    label: 'Borders',           metric: 'ADMIN',               colorVar: 'var(--color-sentinel-muted)'  },
    { key: 'graticule',  label: 'Graticule',         metric: 'MGRS',                colorVar: 'var(--color-sentinel-info)'   },
  ] as const;
  const analyticsToolRows = [
    { key: 'viewshed', label: 'Viewshed',      metric: analyticsCounts.viewshed, colorVar: 'var(--color-sentinel-accent)', available: analyticsCounts.viewshedAvailable },
    { key: 'los',      label: 'Line of Sight', metric: analyticsCounts.los,      colorVar: 'var(--color-sentinel-accent)', available: analyticsCounts.losAvailable },
    { key: 'routes',   label: 'Routes',        metric: analyticsCounts.routes,   colorVar: 'var(--color-sentinel-accent)', available: analyticsCounts.routesAvailable },
  ] as const;

  const toggleLayer = (key: keyof ActiveLayerMap) =>
    setActiveLayers((prev) => ({ ...prev, [key]: !prev[key] }));

  // The opacity slider drives the BASE/TERRAIN reference overlay. SAT mode has
  // no fade-able overlay (imagery always renders at 100%), so the slider is
  // disabled there and falls back to the BASE value for display.
  const opacityLayer = activeBaseLayer === 'terrain' ? 'terrain' : 'base';

  // The overlay autohides past BASEMAP_OVERLAY_MAX_ZOOM. Tell the user why the
  // slider went dead and the layer vanished — see docs/decisions/why-basemap-z14-cap.md.
  const overlayAutohidden =
    activeBaseLayer !== 'sat' && mapZoom > BASEMAP_OVERLAY_MAX_ZOOM;

  return (
    <section
      data-tour="layer-panel"
      className="sentinel-panel map-float-panel map-left-panel"
      style={{
        position: 'absolute',
        left: 14,
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
        containerName: 'layer-panel',
      }}
    >
      <div className="sentinel-panel-header">
        <Layers className="h-4 w-4" />
        <span>Operating picture</span>
        <button type="button" onClick={onRefresh} className="sentinel-icon-btn ml-auto h-6 w-6" title="Refresh">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
        <button type="button" onClick={onCollapse} className="sentinel-icon-btn h-6 w-6" title="Collapse panel">
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="sentinel-scroll">
        {/* Basemap selector */}
        <div data-tour="basemap-selector" className="border-b border-sentinel-line p-2">
          <div className="layer-panel-basemap-grid">
            {([
              { k: 'base',    label: 'BASE',    sub: 'Dark vector' },
              { k: 'sat',     label: 'SAT',     sub: 'Imagery'     },
              { k: 'terrain', label: 'TERRAIN', sub: 'Hillshade'   },
            ] as const).map(({ k, label, sub }) => {
              const isActive = activeBaseLayer === k;
              return (
                <button
                  key={k}
                  type="button"
                  onClick={() => {
                    if (k === 'sat' && selectedImagery === null && imagery.length > 0) {
                      setSelectedImagery(imagery[0].id);
                    }
                    setActiveBaseLayer(k);
                  }}
                  className={`layer-panel-basemap-tile ${isActive ? 'is-active' : ''}`}
                  aria-pressed={isActive}
                >
                  <BasemapThumb kind={k} />
                  {isActive && <Check className="layer-panel-basemap-check" />}
                  <span className="layer-panel-basemap-label">{label}</span>
                  <span className="layer-panel-basemap-sub">{sub}</span>
                </button>
              );
            })}
          </div>
          <div data-tour="opacity-slider" className="mt-2 flex items-center gap-2">
            <span className="sentinel-label">
              {activeBaseLayer === 'sat' ? 'IMAGERY' : `${activeBaseLayer.toUpperCase()} OVERLAY`}
            </span>
            <input
              type="range" min="0" max="1" step="0.05"
              disabled={activeBaseLayer === 'sat' || overlayAutohidden}
              value={layerOpacities[opacityLayer]}
              onChange={(event) => {
                const next = parseFloat(event.target.value);
                setLayerOpacities((prev) => ({ ...prev, [opacityLayer]: next }));
              }}
              className="flex-1"
            />
            <span className="font-mono text-[10px] text-sentinel-muted w-8 text-right">
              {Math.round(layerOpacities[opacityLayer] * 100)}%
            </span>
          </div>
          {overlayAutohidden && (
            <div className="px-2 pt-1 font-mono text-[10px] text-sentinel-muted">
              Reference hidden past zoom {BASEMAP_OVERLAY_MAX_ZOOM} · imagery only
            </div>
          )}
        </div>

        {/* Overlays section header */}
        <div className="flex items-center gap-2 border-b border-sentinel-line bg-sentinel-panel-2 px-3 py-2">
          <button
            type="button"
            onClick={() => setOverlaysOpen((v) => !v)}
            className="text-sentinel-muted hover:text-slate-200"
            title={overlaysOpen ? 'Collapse overlays' : 'Expand overlays'}
          >
            {overlaysOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
          <span className="sentinel-label flex-1">Overlays</span>
        </div>

        {overlaysOpen && (
          <>
            <div data-tour="layer-toggles">
              {liveLayerRows.map((layer) => (
                <OverlayRow
                  key={layer.key}
                  label={layer.label}
                  metric={layer.metric}
                  colorVar={layer.colorVar}
                  active={activeLayers[layer.key as keyof ActiveLayerMap]}
                  onToggle={() => toggleLayer(layer.key as keyof ActiveLayerMap)}
                />
              ))}
            </div>
            <div data-tour="analytics-tools">
              <div className="layer-panel-subhead">Analytics tools</div>
              {analyticsToolRows.map((layer) => (
                <OverlayRow
                  key={layer.key}
                  label={layer.label}
                  metric={layer.metric}
                  colorVar={layer.colorVar}
                  active={activeLayers[layer.key as keyof ActiveLayerMap]}
                  disabled={!layer.available}
                  onToggle={() => toggleLayer(layer.key as keyof ActiveLayerMap)}
                />
              ))}
            </div>
          </>
        )}

        {/* Detection Classes section header */}
        <div data-tour="detection-classes" className="border-b border-sentinel-line bg-sentinel-panel-2 px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="sentinel-label flex-1">Detection Classes / {visibleDetectionCount}</span>
            <div className="grid grid-cols-2 border border-sentinel-line-2">
              {(['CAT', 'SRC'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setDetectionGroupMode(mode)}
                  className={`h-6 px-2 font-mono text-[10px] ${detectionGroupMode === mode ? 'bg-sentinel-panel text-slate-100' : 'text-sentinel-muted'}`}
                >
                  {mode}
                </button>
              ))}
            </div>
            <button type="button" className="sentinel-btn h-6" onClick={showAllDetectionClasses}>ALL</button>
            <button type="button" className="sentinel-btn h-6" onClick={hideAllDetectionClasses}>NONE</button>
            <button type="button" className="sentinel-btn h-6" onClick={invertDetectionClasses}>INV</button>
          </div>
          <div className="mt-2 flex h-8 items-center gap-2 border border-sentinel-line-2 bg-sentinel-bg px-2">
            <Search className="h-3.5 w-3.5 text-sentinel-muted" />
            <input
              value={detectionLabelSearch}
              onChange={(event) => setDetectionLabelSearch(event.target.value)}
              placeholder="search classes"
              className="min-w-0 flex-1 bg-transparent text-xs text-slate-200 outline-none placeholder:text-sentinel-muted"
            />
          </div>
        </div>

        {detectionGroups.length === 0 && (
          <div className="p-4 text-xs text-sentinel-muted">No detections in current view.</div>
        )}

        {detectionGroups.map((group) => {
          const expanded = expandedDetectionGroups.includes(group.id);
          const category = group.id as DetectionCategoryId;
          const groupHidden = detectionGroupMode === 'CAT'
            ? hiddenDetectionCategories.includes(category)
            : group.classes.every((item) => hiddenDetectionLabels.includes(item.rawClass));
          const groupColor = groupHidden ? 'var(--ink-2)' : group.color;
          return (
            <div key={group.id} className="border-b border-sentinel-line">
              <div className="grid grid-cols-[22px_22px_1fr_auto_auto] items-center gap-2 px-3 py-2">
                <button type="button" className="text-sentinel-muted" onClick={() => toggleDetectionGroupExpanded(group.id)}>
                  {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                </button>
                <button type="button" style={{ color: groupColor }} onClick={() => toggleDetectionGroupVisibility(group)}>
                  {groupHidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
                <button type="button" className="min-w-0 text-left" onClick={() => toggleDetectionGroupExpanded(group.id)}>
                  <span className="flex min-w-0 items-center gap-2">
                    {detectionGroupMode === 'CAT' && (
                      <span style={{ color: groupColor }}>
                        <CategoryIcon category={category} branchById={branchById} />
                      </span>
                    )}
                    <span className={`truncate text-xs ${groupHidden ? 'text-sentinel-muted' : 'text-slate-200'}`}>{group.label}</span>
                  </span>
                </button>
                <span className="font-mono text-[10px] text-sentinel-muted">{group.classes.length}</span>
                <span className="font-mono text-[10px]" style={{ color: groupColor }}>{group.count}</span>
              </div>
              <div className="px-3 pb-2">
                <div className="h-1.5 bg-sentinel-bg">
                  <div
                    className="h-full"
                    style={{
                      width: `${Math.max(3, (group.count / maxDetectionLabelCount) * 100)}%`,
                      backgroundColor: group.color,
                    }}
                  />
                </div>
              </div>
              {expanded && (
                <div className="border-t border-sentinel-line bg-sentinel-bg/70">
                  {group.classes.map((item) => {
                    const hidden = Boolean(detectionClassFilter && detectionClassFilter !== item.rawClass)
                      || hiddenDetectionCategories.includes(item.category)
                      || hiddenDetectionLabels.includes(item.rawClass);
                    const solo = detectionClassFilter === item.rawClass;
                    const advisory = item.llmAdvisory;
                    const advisoryLabel = advisory?.label && advisory.label !== item.label ? advisory.label : null;
                    const advisoryTitle = advisory
                      ? `AI suggestion (non-authoritative): ${advisory.label || ''}${advisory.description ? ` — ${advisory.description}` : ''}\nGenerated by ${advisory.generated_by || 'llm'}. Deterministic ontology remains the canonical class.`
                      : '';
                    return (
                      <div key={item.rawClass} className="grid grid-cols-[22px_18px_1fr_auto_auto] items-center gap-2 px-3 py-1.5">
                        <button
                          type="button"
                          style={{ color: hidden ? 'var(--ink-2)' : item.color }}
                          onClick={() => toggleDetectionClassVisibility(item.rawClass)}
                        >
                          {hidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                        </button>
                        <span style={{ color: hidden ? 'var(--ink-2)' : item.color }}>
                          <DetectionSubclassIcon
                            label={item.rawClass}
                            category={item.category}
                            branchById={branchById}
                            className="h-3 w-3"
                          />
                        </span>
                        <button type="button" className="min-w-0 text-left" onClick={() => soloDetectionClass(item.rawClass)}>
                          <span className={`block truncate text-[11px] ${hidden ? 'text-sentinel-muted' : 'text-slate-200'}`}>
                            {item.label}{solo ? ' / SOLO' : ''}
                            {advisoryLabel && (
                              <span
                                className="ml-1.5 inline-flex items-center rounded-sm border border-amber-500/60 px-1 py-[1px] font-mono text-[9px] uppercase tracking-wider text-amber-300"
                                title={advisoryTitle}
                              >
                                AI · {advisoryLabel}
                              </span>
                            )}
                          </span>
                        </button>
                        <span className={`sentinel-tag ${threatClass(item.threatLevel)}`}>
                          {item.threatLevel || categoryFor(item.category, categories).short}
                        </span>
                        <span
                          className="font-mono text-[10px]"
                          style={{ color: hidden ? 'var(--ink-2)' : item.color }}
                        >
                          {item.count}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}

        {imagery.length > 0 && (
          <div data-tour="imagery-list">
            <div className="sentinel-panel-header border-t border-sentinel-line">
              <Satellite className="h-4 w-4" />
              <span>Imagery</span>
            </div>
            {imagery.slice(0, 10).map((img) => (
              <button
                key={img.id}
                type="button"
                onClick={() => {
                  const next = selectedImagery === img.id ? null : img.id;
                  setSelectedImagery(next);
                  if (next !== null) setActiveBaseLayer('sat');
                }}
                className={`sentinel-row w-full grid-cols-[1fr_auto] text-left ${selectedImagery === img.id ? 'selected' : ''}`}
              >
                <span className="min-w-0">
                  <span className="block truncate text-xs text-slate-200">{img.name}</span>
                  <span className="block truncate font-mono text-[10px] text-sentinel-muted">
                    {img.sensor_type} / {img.cloud_cover ?? 0}% cloud
                  </span>
                </span>
                <span className="sentinel-tag info">SAT</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
