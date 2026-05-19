/**
 * Icon factories extracted from the GaiaMap monolith.
 *
 * Two flavours:
 *   - Leaflet ``L.Icon`` / ``L.DivIcon`` factories for map markers.
 *   - React components (``CategoryIcon``, ``DetectionSubclassIcon``) for
 *     panel chrome (legend rows, selection panel headers).
 *
 * Kept here because both consume the same ontology lookup tables and need
 * to stay in lock-step when the icon-key fallback chain changes.
 */

import L from 'leaflet';
import { renderToStaticMarkup } from 'react-dom/server';

import { objectIconComponent } from '../../utils/branchIcons';
import {
  categoryFor,
  type DetectionCategoryId,
  type DetectionCategoryMap,
} from '../../utils/detectionTaxonomy';
import { IconRenderer, iconComponentByKey } from '../../utils/iconLibrary';
import type { OntologyBranch } from '../../utils/useOntology';

import { detectionCategoryForFeature } from './_helpers';


// Leaflet's default-marker assets are bundled separately; the prototype's
// auto-detect path collides with Vite's asset pipeline. Suppress it.
delete (L.Icon.Default.prototype as any)._getIconUrl;


/* ── Coloured pin factories ───────────────────────────────────────────── */

export const createIcon = (color: string): L.Icon =>
  new L.Icon({
    iconUrl: `data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIlMjMzYjgyZjYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjEgMTBsLTkgMTVMMyAxMGw5LTl6Ii8+PC9zdmc+`
      .replace('%233b82f6', encodeURIComponent(color)),
    iconSize: [20, 20],
    iconAnchor: [10, 20],
    popupAnchor: [0, -20],
  });

export const blueIcon = createIcon('#4ea1ff');
export const redIcon = createIcon('#ff3b30');
export const emeraldIcon = createIcon('#3dd68c');


/* ── Branch-aware icon components for panel chrome ────────────────────── */

export function CategoryIcon({
  category,
  branchById,
  className = 'h-3.5 w-3.5',
}: {
  category: DetectionCategoryId;
  branchById: Map<string, OntologyBranch>;
  className?: string;
}) {
  const branch = branchById.get(category);
  return <IconRenderer iconKey={branch?.icon_key ?? null} className={className} />;
}

export function DetectionSubclassIcon({
  iconKey,
  label,
  category,
  branchById,
  className = 'h-3.5 w-3.5',
}: {
  iconKey?: string | null;
  label?: string | null;
  category: DetectionCategoryId;
  branchById: Map<string, OntologyBranch>;
  className?: string;
}) {
  const branch = branchById.get(category);
  const branchIconKey = branch?.icon_key ?? null;
  // Prefer an explicit iconKey from the feature; fall back to the branch-
  // level key. If neither resolves, fall through to the legacy regex
  // matcher (kept available as a last-resort) and finally to CircleHelp
  // via IconRenderer.
  if (iconKey || branchIconKey) {
    return (
      <IconRenderer
        iconKey={iconKey ?? null}
        fallbackBranchKey={branchIconKey as any}
        className={className}
      />
    );
  }
  // Last-resort: regex on the raw label.
  const Icon = objectIconComponent(label, branchIconKey as any);
  return <Icon className={className} />;
}


/* ── Detection map-marker factory (Leaflet divIcon) ───────────────────── */

/**
 * Builds the ``L.divIcon`` used on each detection marker. The returned
 * function reads the feature's ``icon_key`` first, then the branch-level
 * key, and finally falls back to the regex-based ``objectIconComponent``
 * matcher — same priority chain the legacy GaiaMap implemented.
 */
export function makeDetectionIcon(
  categories: DetectionCategoryMap,
  branchById: Map<string, OntologyBranch>,
) {
  return (feature: any): L.DivIcon => {
    const category = detectionCategoryForFeature(feature);
    const color = categoryFor(category, categories).color;
    const props = feature?.properties || {};
    const branch = branchById.get(category);
    const branchIconKey = branch?.icon_key ?? null;
    const featureIconKey: string | null = props.icon_key ?? null;
    const Icon =
      iconComponentByKey(featureIconKey) ??
      iconComponentByKey(branchIconKey) ??
      objectIconComponent(
        props.original_class || props.class || props.label,
        branchIconKey as any,
      );
    const iconMarkup = renderToStaticMarkup(<Icon size={12} strokeWidth={2.2} />);
    return L.divIcon({
      className: '',
      iconSize: [14, 14],
      iconAnchor: [15, 15],
      html: `<div class="sentinel-detection-icon" style="color:${color};border-color:${color};box-shadow:0 0 8px ${color}55;">${iconMarkup}</div>`,
    });
  };
}
