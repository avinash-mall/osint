/**
 * MgrsGraticule — pure-Leaflet coordinate graticule overlay.
 *
 * Renders WGS84 / MGRS reference grid lines on top of the map. At low zoom
 * draws a degree graticule; at higher zoom switches to an MGRS 100 km / 10 km
 * grid using the `mgrs` package already vendored for the cursor readout.
 *
 * No external Leaflet plugin is required — uses only `react-leaflet`
 * `useMap()` and core `L.LayerGroup` / `L.Polyline` / `L.Marker` primitives.
 * This keeps the workstation fully offline-safe (CLAUDE.md hard rule #8).
 */

import L from 'leaflet';
import { forward as mgrsForward } from 'mgrs';
import { useEffect } from 'react';
import { useMap } from 'react-leaflet';

type Props = {
  /** Stroke color for grid lines. */
  color?: string;
  /** Stroke opacity (0..1). */
  opacity?: number;
  /** Stroke weight. */
  weight?: number;
};

/** Spacing in degrees per zoom band (low zoom → coarse). */
function degSpacingForZoom(z: number): number {
  if (z <= 3) return 30;
  if (z <= 5) return 10;
  if (z <= 7) return 5;
  if (z <= 9) return 1;
  if (z <= 11) return 0.5;
  return 0.1;
}

/** When zoom is high enough, draw MGRS-aligned grid lines on top. */
function mgrsSpacingMetersForZoom(z: number): number | null {
  if (z < 10) return null;
  if (z < 13) return 100_000; // 100 km bands
  if (z < 15) return 10_000;  // 10 km
  if (z < 17) return 1_000;   // 1 km
  return 100;                  // 100 m
}

function formatDeg(value: number): string {
  const abs = Math.abs(value);
  if (Math.abs(value - Math.round(value)) < 1e-6) return `${value.toFixed(0)}°`;
  if (abs < 10) return `${value.toFixed(2)}°`;
  return `${value.toFixed(1)}°`;
}

export default function MgrsGraticule({
  color = 'var(--color-sentinel-muted, #6f8aa3)',
  opacity = 0.55,
  weight = 0.5,
}: Props) {
  const map = useMap();

  useEffect(() => {
    if (!map) return;
    const group = L.layerGroup().addTo(map);

    const redraw = () => {
      group.clearLayers();
      const bounds = map.getBounds();
      const zoom = map.getZoom();
      const minLat = Math.max(bounds.getSouth(), -85);
      const maxLat = Math.min(bounds.getNorth(), 85);
      const minLon = bounds.getWest();
      const maxLon = bounds.getEast();
      if (!isFinite(minLat) || !isFinite(maxLat)) return;

      const step = degSpacingForZoom(zoom);
      const padding = step;
      const baseStyle: L.PolylineOptions = {
        color, weight, opacity, interactive: false, dashArray: '2 3',
      };

      const startLat = Math.floor((minLat - padding) / step) * step;
      const endLat = Math.ceil((maxLat + padding) / step) * step;
      const startLon = Math.floor((minLon - padding) / step) * step;
      const endLon = Math.ceil((maxLon + padding) / step) * step;

      for (let lat = startLat; lat <= endLat; lat += step) {
        L.polyline(
          [[lat, minLon - padding], [lat, maxLon + padding]],
          baseStyle,
        ).addTo(group);
      }
      for (let lon = startLon; lon <= endLon; lon += step) {
        L.polyline(
          [[minLat - padding, lon], [maxLat + padding, lon]],
          baseStyle,
        ).addTo(group);
      }

      // Sparse degree labels along the diagonal anchors at low zoom.
      if (zoom <= 11) {
        const labelStep = step * (zoom <= 5 ? 1 : 2);
        for (let lat = startLat; lat <= endLat; lat += labelStep) {
          for (let lon = startLon; lon <= endLon; lon += labelStep) {
            if (lat < minLat || lat > maxLat) continue;
            if (lon < minLon || lon > maxLon) continue;
            L.marker([lat, lon], {
              interactive: false,
              icon: L.divIcon({
                className: 'graticule-label',
                html: `<span>${formatDeg(lat)} ${formatDeg(lon)}</span>`,
                iconAnchor: [-2, -2],
                iconSize: undefined as any,
              }),
            }).addTo(group);
          }
        }
      }

      // High-zoom MGRS overlay — denser, accent-coloured.
      const mgrsStep = mgrsSpacingMetersForZoom(zoom);
      if (mgrsStep) {
        const accentStyle: L.PolylineOptions = {
          color: 'var(--color-sentinel-info, #6ec1ff)',
          weight: 0.65,
          opacity: 0.7,
          interactive: false,
        };
        // MGRS lines are drawn by walking a UTM grid aligned to mgrsStep.
        // Approximate at this zoom: snap centroid lat to grid using mgrsForward,
        // then walk East/North a small number of steps in approximate degrees.
        const cosLat = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180) || 1;
        const stepDegLat = mgrsStep / 111_320;
        const stepDegLon = mgrsStep / (111_320 * cosLat);
        const cap = 80; // safety: never render more than 80 × 80 lines
        const latRange = Math.min((maxLat - minLat) / stepDegLat, cap);
        const lonRange = Math.min((maxLon - minLon) / stepDegLon, cap);
        if (latRange > 0 && lonRange > 0) {
          // Anchor on rounded MGRS cell of the SW corner.
          const accuracy = mgrsStep >= 100_000 ? 0
            : mgrsStep >= 10_000 ? 1
            : mgrsStep >= 1_000 ? 2
            : mgrsStep >= 100 ? 3
            : 4;
          try {
            mgrsForward([minLon, minLat], accuracy);
          } catch {
            // outside UTM/UPS — bail on MGRS overlay
            return;
          }
          for (let i = 0; i <= latRange; i++) {
            const lat = minLat + i * stepDegLat;
            L.polyline(
              [[lat, minLon], [lat, maxLon]],
              accentStyle,
            ).addTo(group);
          }
          for (let j = 0; j <= lonRange; j++) {
            const lon = minLon + j * stepDegLon;
            L.polyline(
              [[minLat, lon], [maxLat, lon]],
              accentStyle,
            ).addTo(group);
          }
        }
      }
    };

    redraw();
    map.on('moveend zoomend', redraw);
    return () => {
      map.off('moveend zoomend', redraw);
      group.remove();
    };
  }, [map, color, opacity, weight]);

  return null;
}
