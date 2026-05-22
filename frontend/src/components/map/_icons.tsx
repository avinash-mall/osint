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


/* ── Basemap thumbnail previews (hand-painted, no asset fetches) ──────── */

/**
 * 56×40 SVG preview of a basemap option, painted entirely with inline
 * gradients/paths so the LayerPanel gallery stays inside the air-gap rule
 * (no tile fetch, no image asset). Active-tile outline is drawn by CSS.
 */
export function BasemapThumb({ kind }: { kind: 'base' | 'sat' | 'terrain' }) {
  if (kind === 'sat') {
    return (
      <svg width="56" height="40" viewBox="0 0 56 40" aria-hidden>
        <defs>
          <linearGradient id="bt-sat" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#3a4a2e" />
            <stop offset="1" stopColor="#202a1a" />
          </linearGradient>
        </defs>
        <rect width="56" height="40" fill="url(#bt-sat)" />
        <polygon points="2,4 24,2 20,16 4,18" fill="#556b3a" opacity="0.8" />
        <polygon points="30,3 54,6 50,20 32,17" fill="#6a7d48" opacity="0.7" />
        <polygon points="6,22 26,20 30,38 8,38" fill="#47592f" opacity="0.85" />
        <polygon points="34,22 54,24 54,38 36,38" fill="#5c6e3c" opacity="0.7" />
        <path
          d="M0 14 C14 18 20 28 40 30 S52 36 56 34"
          stroke="#3b5566" strokeWidth="2" fill="none" opacity="0.8"
        />
      </svg>
    );
  }
  if (kind === 'terrain') {
    return (
      <svg width="56" height="40" viewBox="0 0 56 40" aria-hidden>
        <defs>
          <linearGradient id="bt-terrain" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#4a5562" />
            <stop offset="1" stopColor="#21262e" />
          </linearGradient>
        </defs>
        <rect width="56" height="40" fill="url(#bt-terrain)" />
        <path
          d="M0 16 C12 8 22 12 30 9 C40 5 48 12 56 8 L56 0 L0 0 Z"
          fill="#5a6571" opacity="0.6"
        />
        <g stroke="#7b8794" strokeWidth="0.9" fill="none" opacity="0.85">
          <path d="M0 22 C12 16 22 20 32 16 C42 12 50 18 56 14" />
          <path d="M0 29 C12 24 22 27 32 24 C42 20 50 25 56 22" />
          <path d="M0 36 C12 32 24 34 34 31 C44 28 50 32 56 30" />
        </g>
      </svg>
    );
  }
  // 'base' — dark vector
  return (
    <svg width="56" height="40" viewBox="0 0 56 40" aria-hidden>
      <rect width="56" height="40" fill="#0b0d10" />
      <rect x="4" y="5" width="18" height="13" fill="#11151a" />
      <rect x="30" y="4" width="22" height="15" fill="#11151a" />
      <rect x="6" y="24" width="20" height="12" fill="#11151a" />
      <rect x="34" y="24" width="18" height="12" fill="#11151a" />
      <g stroke="#2a323d" strokeWidth="1.5" fill="none">
        <path d="M0 21 H56" />
        <path d="M27 0 V40" />
      </g>
      <path
        d="M4 32 L27 21 L52 9"
        stroke="#4ea1ff" strokeWidth="1.5" fill="none"
      />
    </svg>
  );
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
