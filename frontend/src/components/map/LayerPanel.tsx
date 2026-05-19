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
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Layers,
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
import { CategoryIcon, DetectionSubclassIcon } from './_icons';

export type ActiveLayerMap = {
  satellite: boolean;
  detections: boolean;
  tracks: boolean;
  detectionTracks: boolean;
  static: boolean;
  grid: boolean;
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
  layerOpacities: Record<BaseLayer, number>;
  setLayerOpacities: React.Dispatch<React.SetStateAction<Record<BaseLayer, number>>>;

  /* Overlays section */
  overlaysOpen: boolean;
  setOverlaysOpen: React.Dispatch<React.SetStateAction<boolean>>;
  activeLayers: ActiveLayerMap;
  setActiveLayers: React.Dispatch<React.SetStateAction<ActiveLayerMap>>;
  showBbox: boolean;
  setShowBbox: React.Dispatch<React.SetStateAction<boolean>>;

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

export default function LayerPanel({
  onRefresh,
  onCollapse,
  activeBaseLayer,
  setActiveBaseLayer,
  layerOpacities,
  setLayerOpacities,
  overlaysOpen,
  setOverlaysOpen,
  activeLayers,
  setActiveLayers,
  showBbox,
  setShowBbox,
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
  const overlayRows = [
    { key: 'satellite',  label: 'Satellite Imagery', metric: imagery.length,           color: 'text-sentinel-info',   available: true },
    { key: 'detections', label: 'AI Detections',     metric: visibleDetectionCount,    color: 'text-sentinel-accent', available: true },
    { key: 'tracks',     label: 'Active Tracks',     metric: tracksCount,              color: 'text-sentinel-info',   available: true },
    { key: 'static',     label: 'Static Features',   metric: staticCount,              color: 'text-sentinel-crit',   available: true },
    { key: 'grid',       label: 'Tactical Grid',     metric: 'WGS84' as const,          color: 'text-sentinel-muted',  available: true },
    { key: 'viewshed',   label: 'Viewshed',          metric: analyticsCounts.viewshed, color: 'text-sentinel-accent', available: analyticsCounts.viewshedAvailable },
    { key: 'los',        label: 'Line of Sight',     metric: analyticsCounts.los,      color: 'text-sentinel-accent', available: analyticsCounts.losAvailable },
    { key: 'routes',     label: 'Routes',            metric: analyticsCounts.routes,   color: 'text-sentinel-accent', available: analyticsCounts.routesAvailable },
  ];

  return (
    <section
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
        <div className="border-b border-sentinel-line p-2">
          <div className="grid grid-cols-3 border border-sentinel-line-2">
            {(['base', 'sat', 'terrain'] as const).map((key) => {
              const isActive = activeBaseLayer === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setActiveBaseLayer(key)}
                  className={`h-7 font-mono text-[10px] uppercase tracking-widest ${isActive ? 'bg-sentinel-panel-2 text-slate-100' : 'text-sentinel-muted'}`}
                >
                  {key}
                </button>
              );
            })}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className="sentinel-label">OPACITY</span>
            <input
              type="range" min="0" max="1" step="0.05"
              value={layerOpacities[activeBaseLayer]}
              onChange={(event) => {
                const next = parseFloat(event.target.value);
                setLayerOpacities((prev) => ({ ...prev, [activeBaseLayer]: next }));
              }}
              className="flex-1"
            />
            <span className="font-mono text-[10px] text-sentinel-muted w-8 text-right">
              {Math.round(layerOpacities[activeBaseLayer] * 100)}%
            </span>
          </div>
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
          <button type="button" onClick={() => setShowBbox((value) => !value)} className={`sentinel-btn h-6 ${showBbox ? 'primary' : ''}`}>
            BBOX
          </button>
        </div>

        {overlaysOpen && overlayRows.map((layer) => {
          const active = activeLayers[layer.key as keyof ActiveLayerMap];
          const disabled = layer.available === false;
          return (
            <button
              key={layer.key}
              type="button"
              disabled={disabled}
              onClick={() => setActiveLayers((prev) => ({ ...prev, [layer.key]: !active }))}
              className={`sentinel-row w-full grid-cols-[22px_1fr_auto] text-left ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
              title={disabled ? 'Run the tool first to enable this layer' : ''}
            >
              <span className={active && !disabled ? layer.color : 'text-sentinel-muted'}>
                {active && !disabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
              </span>
              <span className="truncate text-xs text-slate-200">{layer.label}</span>
              <span className="font-mono text-[10px] text-sentinel-muted">{layer.metric}</span>
            </button>
          );
        })}

        {/* Detection Classes section header */}
        <div className="border-b border-sentinel-line bg-sentinel-panel-2 px-3 py-2">
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
          <>
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
          </>
        )}
      </div>
    </section>
  );
}
