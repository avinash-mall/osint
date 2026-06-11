/**
 * DetectionTileLayer — Martin vector-tile (MVT) rendering of persisted
 * detection BOXES (DEFAULT ON; legacy fat path only when VITE_DETECTION_TILES=0
 * — see MapStage). The tile carries two layers: `detections` (polygons, styled
 * here) and `detection_points` (centroids, HIDDEN here so we don't double the
 * dots — markers/dots come from the lite feed, preserving the lucide icons).
 * `geomMode` (obb|hbb|mask) is appended to the tile URL so the box geometry
 * follows GaiaMap's box-mode toggle.
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
 * Selection: VectorGrid features carry only tile props, so a click hands the
 * id to `onSelectById` (GaiaMap's shared selectDetectionById), which fetches
 * the fully-enriched GeoJSON Feature from /api/detections/{id}/enriched — the
 * enriched shape matches the old /api/detections/geojson feature, so the
 * SelectionPanel works identically. Marker/dot clicks route through the same
 * helper.
 *
 * The layer is re-created whenever `version` or any filter dep changes (Phase 2
 * keeps it simple — no per-feature setFeatureStyle), so style/visibility stay
 * in sync with the LayerPanel controls and tile-version cache-busting.
 */

import { useEffect } from 'react';
import { useMap } from 'react-leaflet';
import L from 'leaflet';
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
  /** Box geometry mode — mirrors GaiaMap's bboxMode toggle. Appended to the
      tile URL as &geom_mode=; the backend serves the matching polygon
      (oriented box / horizontal box / mask). Default `obb`. */
  geomMode: 'obb' | 'hbb' | 'mask';
  /** Live ontology category → colour map (same map the box layer uses). */
  categories: DetectionCategoryMap;
  /** Client-side filters — must mirror filteredDetectionsGeoJSON in GaiaMap. */
  confidenceThreshold: number;
  detectionClassFilter: string | null;
  hiddenDetectionCategories: DetectionCategoryId[];
  /** Select by id — fetches /api/detections/{id}/enriched (the shared
      selectDetectionById in GaiaMap) so the SelectionPanel gets the fat shape. */
  onSelectById: (id: any) => void;
};

export default function DetectionTileLayer({
  version,
  geomMode,
  categories,
  confidenceThreshold,
  detectionClassFilter,
  hiddenDetectionCategories,
  onSelectById,
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
      const url = `${API_URL}/maps/detections_mvt/{z}/{x}/{y}?v=${version}&geom_mode=${geomMode}`;
      layer = (L as any).vectorGrid.protobuf(url, {
        interactive: true,
        maxNativeZoom: 18,
        // VectorGrid is a GridLayer → it renders into the TILE pane, where
        // GridLayer's default zIndex is 1. The raster stack sits at 100
        // (basemap fallback) / 200 (SAT imagery) / 300 (reference overlay),
        // so without this the boxes draw UNDER the imagery and are invisible
        // wherever any tile painted. 500 puts them above every raster layer
        // (markers/popups live in higher panes and are unaffected).
        zIndex: 500,
        getFeatureId: (f: any) => f.properties?.id,
        vectorTileLayerStyles: {
          // BOXES — styled persisted-detection polygons.
          detections: (props: any) => styleForTileProps(props),
          // POINTS — hidden. The tile ships a `detection_points` centroid
          // sublayer too, but markers/dots come from the lite feed (so the
          // lucide icons are preserved); drawing this would double the dots.
          detection_points: () => ({ stroke: false, fill: false }),
        },
      });
      layer.on('click', (e: any) => {
        const id = e.layer?.properties?.id;
        if (id == null) return;
        // Selection routes through GaiaMap's selectDetectionById, which fetches
        // /api/detections/{id}/enriched (fat shape) for the SelectionPanel.
        onSelectById(id);
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
    geomMode,
    categories,
    confidenceThreshold,
    detectionClassFilter,
    hiddenDetectionCategories,
    onSelectById,
  ]);

  return null;
}
