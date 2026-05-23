/**
 * Map event handlers — small react-leaflet child components that hook
 * map events and bubble state up through callbacks.
 *
 * These are the FULLY-EXTRACTED versions of the helpers that used to be
 * defined inline in the monolithic GaiaMap.tsx:
 *
 *   MapBoundsUpdater     emit current viewport bounds on moveend
 *   MapZoomTracker       emit current zoom on zoomend
 *   MapCursorTracker     emit lat/lon on mousemove
 *   AnalyticsPickHandler one-shot click → callback while pick mode active
 *   DrawRectHandler      drag-to-draw a rectangle, emit bounds on mouseup
 *   MapFitToImagery      auto-fit to an imagery footprint on prop change
 *   MapFitToDetections   auto-fit to a filtered detection collection
 *
 * The plus: ``MapStage.tsx`` mounts the actual ``<MapContainer>`` and
 * composes these handlers as its children. Each handler here returns
 * ``null`` (or a single ``<Rectangle>`` in DrawRectHandler's case) — they
 * exist purely to receive Leaflet hooks via the react-leaflet context.
 */

import { useEffect, useState } from 'react';
import { Rectangle, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';

/* ── small geometry helpers ──────────────────────────────────────────── */

export function imageryBounds(imagery: any): L.LatLngBounds | null {
  if (!imagery?.footprint_geojson) return null;
  try {
    const geometry = typeof imagery.footprint_geojson === 'string'
      ? JSON.parse(imagery.footprint_geojson)
      : imagery.footprint_geojson;
    const bounds = L.geoJSON(geometry).getBounds();
    return bounds.isValid() ? bounds : null;
  } catch {
    return null;
  }
}

export function geojsonFeatureBounds(geojson: any): L.LatLngBounds | null {
  const bounds = L.latLngBounds([]);
  for (const feature of geojson?.features || []) {
    if (!feature?.geometry) continue;
    try {
      const fb = L.geoJSON(feature.geometry).getBounds();
      if (fb.isValid()) bounds.extend(fb);
    } catch {
      // ignore invalid features
    }
  }
  return bounds.isValid() ? bounds : null;
}

/* ── handlers ────────────────────────────────────────────────────────── */

/** One-shot click handler — when ``enabled``, the next map click fires
 *  ``onPicked(lat, lon)`` and is meant to be deactivated by the caller.
 *  Used for the Range Ring placement tool.
 */
export function MapClickPicker({
  enabled,
  onPicked,
}: {
  enabled: boolean;
  onPicked: (lat: number, lon: number) => void;
}) {
  useMapEvents({
    click(e) {
      if (!enabled) return;
      onPicked(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

export function MapBoundsUpdater({ onBoundsChange }: { onBoundsChange: (bounds: string) => void }) {
  const map = useMap();
  useEffect(() => {
    const handleMoveEnd = () => {
      const b = map.getBounds();
      onBoundsChange(`${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`);
    };
    map.on('moveend', handleMoveEnd);
    handleMoveEnd();
    return () => { map.off('moveend', handleMoveEnd); };
  }, [map, onBoundsChange]);
  return null;
}

export function MapZoomTracker({ onZoomChange }: { onZoomChange: (zoom: number) => void }) {
  const map = useMap();
  useEffect(() => { onZoomChange(map.getZoom()); }, [map, onZoomChange]);
  useMapEvents({
    zoomend() { onZoomChange(map.getZoom()); },
  });
  return null;
}

export function MapCursorTracker({
  onCursorChange,
  onLeave,
}: {
  onCursorChange: (cursor: { lat: number; lon: number }) => void;
  onLeave?: () => void;
}) {
  useMapEvents({
    mousemove(event) {
      onCursorChange({ lat: event.latlng.lat, lon: event.latlng.lng });
    },
    mouseout() {
      onLeave?.();
    },
  });
  return null;
}

/** Generic analytics pick: any one-shot "click on the map to set this slot". */
export function AnalyticsPickHandler<T = string>({
  pickFor,
  onPicked,
}: {
  pickFor: T | null;
  onPicked: (lat: number, lon: number, pickFor: T) => void;
}) {
  const map = useMap();
  useEffect(() => {
    if (pickFor == null) return;
    const container = map.getContainer();
    const prev = container.style.cursor;
    container.style.cursor = 'crosshair';
    return () => { container.style.cursor = prev; };
  }, [pickFor, map]);
  useMapEvents({
    click(event) {
      if (pickFor == null) return;
      onPicked(event.latlng.lat, event.latlng.lng, pickFor as T);
    },
  });
  return null;
}

/**
 * Drag-to-draw a rectangle on the map and emit it as a Leaflet LatLngBounds.
 * Active only while ``enabled`` is true; disables map drag while active so
 * the user can box-select without panning, then re-enables on completion or
 * when the mode is turned off. Single-click (zero-size) drags are ignored
 * — minimum 6 px in each axis required.
 */
export function DrawRectHandler({
  enabled,
  onFinish,
}: {
  enabled: boolean;
  onFinish: (bounds: L.LatLngBounds) => void;
}) {
  const map = useMap();
  const [draftStart, setDraftStart] = useState<L.LatLng | null>(null);
  const [draftEnd, setDraftEnd] = useState<L.LatLng | null>(null);

  useEffect(() => {
    if (!enabled) return;
    map.dragging.disable();
    map.boxZoom.disable();
    const container = map.getContainer();
    container.style.cursor = 'crosshair';
    return () => {
      map.dragging.enable();
      map.boxZoom.enable();
      container.style.cursor = '';
    };
  }, [enabled, map]);

  useMapEvents({
    mousedown(event) {
      if (!enabled) return;
      setDraftStart(event.latlng);
      setDraftEnd(event.latlng);
    },
    mousemove(event) {
      if (!enabled || !draftStart) return;
      setDraftEnd(event.latlng);
    },
    mouseup() {
      if (!enabled || !draftStart || !draftEnd) {
        setDraftStart(null);
        setDraftEnd(null);
        return;
      }
      const bounds = L.latLngBounds(draftStart, draftEnd);
      setDraftStart(null);
      setDraftEnd(null);
      // Reject zero-size rectangles (single click).
      const minPx = 6;
      const swPt = map.latLngToContainerPoint(bounds.getSouthWest());
      const nePt = map.latLngToContainerPoint(bounds.getNorthEast());
      if (Math.abs(swPt.x - nePt.x) < minPx || Math.abs(swPt.y - nePt.y) < minPx) return;
      onFinish(bounds);
    },
  });

  if (!enabled || !draftStart || !draftEnd) return null;
  return (
    <Rectangle
      bounds={L.latLngBounds(draftStart, draftEnd)}
      pathOptions={{ color: '#ff7a1a', weight: 2, dashArray: '6 4', fillOpacity: 0.18 }}
    />
  );
}

export function MapFitToImagery({ imagery }: { imagery: any }) {
  const map = useMap();
  useEffect(() => {
    const bounds = imageryBounds(imagery);
    if (bounds) {
      map.fitBounds(bounds.pad(0.15), { animate: true, maxZoom: 13 });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, imagery?.id]);
  return null;
}

export function MapFitToDetections({
  geojson,
  filterKey,
}: {
  geojson: any;
  filterKey: string | null;
}) {
  const map = useMap();
  const [lastFittedKey, setLastFittedKey] = useState<string | null>(null);

  useEffect(() => {
    if (!filterKey) {
      setLastFittedKey(null);
      return;
    }
    if (filterKey === lastFittedKey) return;
    if (!geojson?.features?.length) return;

    try {
      const bounds = geojsonFeatureBounds(geojson);
      if (bounds?.isValid()) {
        map.fitBounds(bounds.pad(0.25), { animate: true, maxZoom: 15 });
        setLastFittedKey(filterKey);
      }
    } catch {
      // Ignore invalid geometries; the GeoJSON layer itself will skip
      // what Leaflet cannot draw.
    }
  }, [filterKey, geojson, map, lastFittedKey]);
  return null;
}
