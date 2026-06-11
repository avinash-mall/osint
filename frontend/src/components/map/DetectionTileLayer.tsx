/**
 * DetectionTileLayer — opt-in Martin vector-tile (MVT) rendering of persisted
 * detections, behind the VITE_DETECTION_TILES flag (see MapStage).
 *
 * This is an imperative react-leaflet layer: Leaflet.VectorGrid has no
 * react-leaflet wrapper, so we attach/detach an `L.vectorGrid.protobuf`
 * instance directly to the parent map inside an effect. Importing
 * 'leaflet.vectorgrid' for its side effect patches the global `L` with
 * `L.vectorGrid` (no named export exists — hence the `(L as any)` reach-in).
 *
 * Styling parity: the tile's `branch_id` property is the exact value
 * `branchIdForFeature` returns for the GeoJSON path, so branch_id → category →
 * colour reuses `categoryFor` / `HEAVY_OUTLINE_CATEGORIES` and reproduces the
 * per-feature <Polygon> box style (confidence opacity, military dash, heavy
 * outline). The same client-side filters the box layer respects (confidence
 * threshold, SOLO class, hidden categories) are applied in `styleForTileProps`
 * by returning `{ stroke:false, fill:false }` for filtered-out features.
 *
 * Selection: VectorGrid features carry only tile props, so a click fetches the
 * fully-enriched GeoJSON Feature from /api/detections/{id}/enriched and hands
 * it to `onSelect` (the same setSelectedDetection the boxes use) — the enriched
 * shape matches /api/detections/geojson, so the SelectionPanel works
 * identically.
 *
 * The layer is re-created whenever `version` or any filter dep changes (Phase 2
 * keeps it simple — no per-feature setFeatureStyle), so style/visibility stay
 * in sync with the LayerPanel controls and tile-version cache-busting.
 */

import { useEffect } from 'react';
import { useMap } from 'react-leaflet';
import L from 'leaflet';
import axios from 'axios';
// leaflet.vectorgrid is a UMD plugin that registers on a *global* `L` at module
// eval and has no import of leaflet for the bundler to order — so it is imported
// DYNAMICALLY inside the effect, only after we set window.L. vite.config isolates
// it into its own lazy `vendor-vectorgrid` chunk so it is NOT evaluated eagerly
// at app init (which threw "L is not defined" and white-screened the map).

import {
  branchIdForFeature,
  categoryFor,
  type DetectionCategoryId,
  type DetectionCategoryMap,
} from '../../utils/detectionTaxonomy';
import { HEAVY_OUTLINE_CATEGORIES } from './_helpers';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type Props = {
  /** Cache-bust token from /api/detections/tile-version; bumped on ingest/delete. */
  version: number;
  /** Live ontology category → colour map (same map the box layer uses). */
  categories: DetectionCategoryMap;
  /** Client-side filters — must mirror filteredDetectionsGeoJSON in GaiaMap. */
  confidenceThreshold: number;
  detectionClassFilter: string | null;
  hiddenDetectionCategories: DetectionCategoryId[];
  /** Hand the enriched GeoJSON Feature to the same setSelectedDetection the boxes use. */
  onSelect: (feature: any) => void;
};

export default function DetectionTileLayer({
  version,
  categories,
  confidenceThreshold,
  detectionClassFilter,
  hiddenDetectionCategories,
  onSelect,
}: Props) {
  const map = useMap();

  useEffect(() => {
    const hiddenSet = new Set<string>(hiddenDetectionCategories);

    // Reproduce makeDetectionStyle (_helpers.ts) using only tile props, and
    // apply the same client-side filters the box layer respects. Filtered-out
    // features return { stroke:false, fill:false } so VectorGrid hides them.
    const styleForTileProps = (props: any): L.PathOptions => {
      const rawConf = Number(props?.confidence);
      const conf = Number.isFinite(rawConf) ? rawConf : 1;
      if (conf < confidenceThreshold) return { stroke: false, fill: false };

      // SOLO mode: only the leaf class matching the filter survives.
      if (detectionClassFilter && String(props?.class) !== detectionClassFilter) {
        return { stroke: false, fill: false };
      }

      // branch_id on the tile is the same value branchIdForFeature returns.
      const category = branchIdForFeature({ properties: props });
      if (hiddenSet.has(category)) return { stroke: false, fill: false };

      const color = categoryFor(category, categories).color;
      const isHeavy = HEAVY_OUTLINE_CATEGORIES.has(category);
      return {
        color,
        weight: isHeavy ? 2.4 : 2,
        opacity: 1,
        fillColor: color,
        fillOpacity: conf > 0.85 ? 0.14 : 0.05,
        dashArray: category === 'Military_Forces' ? '6, 3' : undefined,
        fill: true,
        stroke: true,
      };
    };

    let layer: any = null;
    let cancelled = false;

    (async () => {
      (window as any).L = L;          // expose the global the UMD plugin needs
      await import('leaflet.vectorgrid');  // lazy chunk; now patches L.vectorGrid
      if (cancelled) return;
      const url = `${API_URL}/maps/detections_mvt/{z}/{x}/{y}?v=${version}`;
      layer = (L as any).vectorGrid.protobuf(url, {
        interactive: true,
        maxNativeZoom: 18,
        getFeatureId: (f: any) => f.properties?.id,
        vectorTileLayerStyles: {
          detections: (props: any) => styleForTileProps(props),
        },
      });
      layer.on('click', async (e: any) => {
        const id = e.layer?.properties?.id;
        if (id == null) return;
        try {
          const r = await axios.get(`${API_URL}/api/detections/${id}/enriched`);
          onSelect(r.data);
        } catch {
          /* ignore — a missing/deleted detection just won't select */
        }
      });
      layer.addTo(map);
    })();

    return () => {
      cancelled = true;
      if (layer) map.removeLayer(layer);
    };
    // Re-create on version or any filter/style dep change (Phase 2 simplicity).
  }, [
    map,
    version,
    categories,
    confidenceThreshold,
    detectionClassFilter,
    hiddenDetectionCategories,
    onSelect,
  ]);

  return null;
}
