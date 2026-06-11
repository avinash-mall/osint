/**
 * FallbackTileLayer — drop-in replacement for react-leaflet's <TileLayer>
 * that, when a tile request fails (404), retries with the PARENT tile
 * (z-1, scaled ×2 and cropped to the covering quadrant), recursively down
 * to `minNativeZoom`. Port of Leaflet.TileLayer.Fallback (ghybs,
 * BSD-2-Clause) — vendored as an ES module because the upstream plugin is
 * a UMD file that needs a global `L` at eval time (same bundler hazard as
 * leaflet.vectorgrid — see docs/decisions/why-detection-mvt-tiles.md).
 *
 * Why: the offline basemap/terrain bakes can be PARTIAL on a given host —
 * the pyramid may stop below the designed z=14 ceiling (e.g. basemap z≤11,
 * terrain z≤10) and may have positional holes. A plain TileLayer renders
 * nothing for a 404 tile, so the whole reference overlay goes blank exactly
 * in the analyst's working zoom band (z12–14). Falling back to an upscaled
 * parent keeps the reference visible (≤8× stretch inside the z≤14 band)
 * with no per-host configuration. On fully-baked hosts every native tile
 * resolves and this layer behaves identically to <TileLayer>.
 *
 * Do NOT use this for the SAT/TiTiler imagery layer: outside the COG
 * footprint tiles legitimately 404, and a parent fallback would smear a
 * stretched low-zoom blob around the imagery edges.
 */
import L from 'leaflet';
import {
  createElementObject,
  createTileLayerComponent,
  updateGridLayer,
  withPane,
} from '@react-leaflet/core';
import type { TileLayerProps } from 'react-leaflet';

const TileLayerWithFallback = L.TileLayer.extend({
  options: {
    // Lowest zoom to fall back to. z=0 always exists in the bakes.
    minNativeZoom: 0,
  },

  createTile(coords: any, done: any) {
    const tile = (L.TileLayer.prototype as any).createTile.call(this, coords, done);
    tile._originalCoords = coords;
    tile._originalSrc = tile.src;
    return tile;
  },

  _createCurrentCoords(originalCoords: any) {
    const currentCoords = (this as any)._wrapCoords(originalCoords);
    currentCoords.fallback = true;
    return currentCoords;
  },

  _originalTileOnError: (L.TileLayer.prototype as any)._tileOnError,

  _tileOnError(done: any, tile: any, e: any) {
    const layer = this as any;
    const originalCoords = tile._originalCoords;
    const currentCoords = (tile._currentCoords =
      tile._currentCoords || layer._createCurrentCoords(originalCoords));
    const fallbackZoom = (tile._fallbackZoom =
      tile._fallbackZoom === undefined ? originalCoords.z - 1 : tile._fallbackZoom - 1);
    const scale = (tile._fallbackScale = (tile._fallbackScale || 1) * 2);
    const tileSize = layer.getTileSize();
    const style = tile.style;

    // Nothing left to fall back to — fail like a plain TileLayer.
    if (fallbackZoom < layer.options.minNativeZoom) {
      layer._originalTileOnError(done, tile, e);
      return;
    }

    // Parent tile coords.
    currentCoords.z = fallbackZoom;
    currentCoords.x = Math.floor(currentCoords.x / 2);
    currentCoords.y = Math.floor(currentCoords.y / 2);

    // Scale the replacement up and crop to the quadrant that covers the
    // original tile's footprint.
    style.width = `${tileSize.x * scale}px`;
    style.height = `${tileSize.y * scale}px`;
    const top = (originalCoords.y - currentCoords.y * scale) * tileSize.y;
    const left = (originalCoords.x - currentCoords.x * scale) * tileSize.x;
    style.marginTop = `${-top}px`;
    style.marginLeft = `${-left}px`;
    style.clip = `rect(${top}px ${left + tileSize.x}px ${top + tileSize.y}px ${left}px)`;

    layer.fire('tilefallback', {
      tile,
      url: tile._originalSrc,
      urlMissing: tile.src,
      urlFallback: layer.getTileUrl(currentCoords),
    });

    tile.src = layer.getTileUrl(currentCoords);
  },

  // Fallback coords carry their own z; everything else goes through the
  // normal zoom-for-url path (maxNativeZoom clamp, zoomOffset, ...).
  getTileUrl(coords: any) {
    const z = (coords.z = coords.fallback ? coords.z : (this as any)._getZoomForUrl());
    const data: Record<string, unknown> = {
      r: (L.Browser as any).retina ? '@2x' : '',
      s: (this as any)._getSubdomain(coords),
      x: coords.x,
      y: coords.y,
      z,
    };
    const map = (this as any)._map;
    if (map && !map.options.crs.infinite) {
      const invertedY = (this as any)._globalTileRange.max.y - coords.y;
      if ((this as any).options.tms) {
        data.y = invertedY;
      }
      data['-y'] = invertedY;
    }
    return (L.Util as any).template((this as any)._url, (L.Util as any).extend(data, (this as any).options));
  },
});

export type FallbackTileLayerProps = TileLayerProps;

const FallbackTileLayer = createTileLayerComponent<L.TileLayer, FallbackTileLayerProps>(
  function createFallbackTileLayer({ url, ...options }, context) {
    const layer = new (TileLayerWithFallback as any)(url, withPane(options, context));
    return createElementObject(layer, context);
  },
  function updateFallbackTileLayer(layer, props, prevProps) {
    updateGridLayer(layer, props, prevProps);
    const { url } = props;
    if (url != null && url !== prevProps.url) {
      layer.setUrl(url);
    }
  },
);

export default FallbackTileLayer;
