#!/usr/bin/env python3
"""
City Imagery Downloader (Sentinel-2 + Esri World Imagery)

This script resolves any city or country name to its geographic bounding box
using the open OpenStreetMap Nominatim geocoding API, then downloads a
georeferenced GeoTIFF from one of two selectable sources (--source):

  * sentinel2 — latest low-cloud Sentinel-2 L2A Cloud-Optimized GeoTIFF (COG)
    true-color composite at 10 m/px, global, with acquisition date metadata.
  * esri      — sub-meter Esri World Imagery basemap (~0.3-1.2 m/px depending
    on --zoom), fetched as XYZ tiles and stitched into a Web-Mercator GeoTIFF.

Sentinel-2 is radiometric, dated satellite data; Esri World Imagery is a sharper
basemap mosaic with mixed/unknown acquisition dates and basemap licensing terms.
"""

import sys
import os
import io
import math
import argparse
import urllib.request
import urllib.parse
import json
import datetime

def geocode_location(query):
    print(f"Resolving location '{query}' using Nominatim geocoding API...")
    encoded_query = urllib.parse.quote(query)
    url = f"https://nominatim.openstreetmap.org/search?q={encoded_query}&format=json&limit=1"
    
    headers = {
        "User-Agent": "Sentinel-GEOINT-Downloader/1.0 (avinash.mall@osint.sentinel)"
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            if not data:
                print(f"Error: Could not resolve location '{query}'. Please check the spelling or try a more specific name.")
                sys.exit(1)
            
            result = data[0]
            display_name = result.get("display_name")
            bbox_str = result.get("boundingbox") # [min_lat, max_lat, min_lon, max_lon]
            
            min_lat = float(bbox_str[0])
            max_lat = float(bbox_str[1])
            min_lon = float(bbox_str[2])
            max_lon = float(bbox_str[3])
            
            print(f"Successfully resolved to: {display_name}")
            return min_lat, max_lat, min_lon, max_lon
    except Exception as e:
        print(f"Geocoding network error: {e}")
        sys.exit(1)

def search_stac(bbox_coords, max_cloud_cover, days_back):
    min_lat, max_lat, min_lon, max_lon = bbox_coords
    lat_diff = max_lat - min_lat
    lon_diff = max_lon - min_lon
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2
    
    # Check if the bounding box is too large (like a whole country or large state)
    if lat_diff > 1.5 or lon_diff > 1.5:
        print(f"Note: Resolved boundary is very wide ({lon_diff:.2f}° x {lat_diff:.2f}°).")
        print(f"Centering search on geographic centroid: Lat {center_lat:.4f}, Lon {center_lon:.4f}")
        # Standard 0.2 degree bounding box (~22km x 22km)
        search_bbox = [center_lon - 0.1, center_lat - 0.1, center_lon + 0.1, center_lat + 0.1]
    else:
        search_bbox = [min_lon, min_lat, max_lon, max_lat]
        
    url = "https://earth-search.aws.element84.com/v1/search"
    headers = {"Content-Type": "application/json"}
    
    # Try incrementally expanding the time range if we don't find low-cloud imagery
    today = datetime.datetime.now(datetime.timezone.utc)
    for search_attempt in range(4): # Try up to 4 search ranges (30d, 90d, 180d, 360d)
        attempt_days = days_back * (search_attempt * 3 + 1)
        start_date = today - datetime.timedelta(days=attempt_days)
        
        datetime_range = f"{start_date.strftime('%Y-%m-%dT%H:%M:%SZ')}/{today.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        print(f"Searching Sentinel-2 catalog from {start_date.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')} with cloud cover < {max_cloud_cover}%...")
        
        params = {
            "collections": ["sentinel-2-l2a"],
            "bbox": search_bbox,
            "datetime": datetime_range,
            "query": {
                "eo:cloud_cover": {
                    "lt": max_cloud_cover
                }
            },
            "limit": 10
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(params).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                features = res_data.get("features", [])
                if features:
                    # Select the feature with the lowest cloud cover
                    features.sort(key=lambda f: f.get("properties", {}).get("eo:cloud_cover", 100))
                    selected_scene = features[0]
                    return selected_scene, search_bbox
        except Exception as e:
            print(f"STAC API query error: {e}")
            sys.exit(1)
            
    print(f"Error: Could not find any Sentinel-2 scenes with cloud cover < {max_cloud_cover}% in the last year.")
    sys.exit(1)

def download_cog(scene, output_path):
    scene_id = scene.get("id")
    props = scene.get("properties", {})
    cloud_cover = props.get("eo:cloud_cover", "unknown")
    capture_time = props.get("datetime", "unknown")
    epsg = props.get("proj:epsg", "unknown")
    
    print("\n=== MATCHING SAT SCENE FOUND ===")
    print(f"Scene ID: {scene_id}")
    print(f"Acquisition Time: {capture_time}")
    print(f"Cloud Cover: {cloud_cover}%")
    print(f"Map Projection: EPSG:{epsg}")
    print("================================\n")
    
    assets = scene.get("assets", {})
    visual_asset = assets.get("visual", {})
    download_url = visual_asset.get("href")
    
    if not download_url:
        print("Error: Scene does not contain a standard true-color visual COG asset.")
        sys.exit(1)
        
    # If the user pointed -o at a directory (existing, or a trailing separator),
    # generate a filename inside it instead of trying to open the dir as a file.
    if os.path.isdir(output_path) or output_path.endswith((os.sep, "/")):
        output_path = os.path.join(output_path, f"{scene_id}.tif")

    print(f"Downloading Cloud-Optimized GeoTIFF from:")
    print(f"URL: {download_url}")
    print(f"To:  {output_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    download_req = urllib.request.Request(
        download_url,
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    
    try:
        # Fetch in chunks to show a simple progress indicator
        with urllib.request.urlopen(download_req, timeout=180) as response:
            file_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            block_size = 1024 * 1024 # 1 MB chunks
            
            with open(output_path, "wb") as f:
                while True:
                    buffer = response.read(block_size)
                    if not buffer:
                        break
                    downloaded += len(buffer)
                    f.write(buffer)
                    if file_size:
                        percent = (downloaded / file_size) * 100
                        print(f"\rDownloading: {percent:.1f}% ({downloaded / (1024*1024):.1f} MB / {file_size / (1024*1024):.1f} MB)", end="", flush=True)
                    else:
                        print(f"\rDownloading: {downloaded / (1024*1024):.1f} MB (unknown size)", end="", flush=True)
            print("\nSuccessfully downloaded COG image!")
    except Exception as e:
        print(f"\nDownload error: {e}")
        if os.path.isfile(output_path):
            os.remove(output_path)
        sys.exit(1)

    return output_path

# --- Esri World Imagery (sub-meter XYZ tiles) ---------------------------------

ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
WEBMERC_ORIGIN = math.pi * 6378137.0  # Web-Mercator half-extent (meters), ~20037508.34

def _deg2tile(lat, lon, z):
    """WGS84 lat/lon -> XYZ tile column/row at zoom z (standard slippy-map scheme)."""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))

def _tile_3857_bounds(x, y, z):
    """EPSG:3857 (minx, miny, maxx, maxy) of a single tile."""
    tile_m = 2 * WEBMERC_ORIGIN / (2 ** z)
    minx = -WEBMERC_ORIGIN + x * tile_m
    maxy = WEBMERC_ORIGIN - y * tile_m
    return minx, maxy - tile_m, minx + tile_m, maxy

def download_esri(bbox_coords, output_path, zoom, radius_km):
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.enums import Resampling
    from PIL import Image

    min_lat, max_lat, min_lon, max_lon = bbox_coords
    center_lat = (min_lat + max_lat) / 2.0
    center_lon = (min_lon + max_lon) / 2.0

    # Esri tiles are sub-meter; the full geocoded bbox (a whole city/country) would be
    # millions of tiles. Carve a fixed square AOI around the centroid instead.
    dlat = radius_km / 111.0
    dlon = radius_km / (111.320 * math.cos(math.radians(center_lat)))
    north, south = center_lat + dlat, center_lat - dlat
    west, east = center_lon - dlon, center_lon + dlon

    x0, y0 = _deg2tile(north, west, zoom)  # NW corner
    x1, y1 = _deg2tile(south, east, zoom)  # SE corner
    x_min, x_max = min(x0, x1), max(x0, x1)
    y_min, y_max = min(y0, y1), max(y0, y1)
    n_cols, n_rows = x_max - x_min + 1, y_max - y_min + 1
    n_tiles = n_cols * n_rows
    gsd = (2 * WEBMERC_ORIGIN / (2 ** zoom)) / 256.0  # nominal m/px at equator

    print("\n=== ESRI WORLD IMAGERY ===")
    print(f"Centroid: Lat {center_lat:.4f}, Lon {center_lon:.4f}")
    print(f"Zoom z{zoom} (~{gsd:.2f} m/px nominal), AOI ~{2 * radius_km:.1f} km square")
    print(f"Tile grid: {n_cols} x {n_rows} = {n_tiles} tiles")
    print("==========================\n")

    MAX_TILES = 6000  # ~1.2 GB mosaic ceiling; guards against runaway zoom/AOI
    if n_tiles > MAX_TILES:
        print(f"Error: {n_tiles} tiles exceeds the safety cap ({MAX_TILES}).")
        print("Lower --zoom or --radius-km and retry.")
        sys.exit(1)

    width, height = n_cols * 256, n_rows * 256
    mosaic = np.zeros((3, height, width), dtype=np.uint8)

    fetched = 0
    for ty in range(y_min, y_max + 1):
        for tx in range(x_min, x_max + 1):
            url = ESRI_TILE_URL.format(z=zoom, x=tx, y=ty)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    tile = Image.open(io.BytesIO(resp.read())).convert("RGB")
            except Exception as e:
                print(f"\nTile z{zoom}/{tx}/{ty} download failed: {e}")
                sys.exit(1)
            r0, c0 = (ty - y_min) * 256, (tx - x_min) * 256
            mosaic[:, r0:r0 + 256, c0:c0 + 256] = np.transpose(np.asarray(tile), (2, 0, 1))
            fetched += 1
            print(f"\rFetching tiles: {fetched}/{n_tiles} ({fetched * 100 // n_tiles}%)",
                  end="", flush=True)
    print()

    bw, _, _, bn = _tile_3857_bounds(x_min, y_min, zoom)
    _, bs, be, _ = _tile_3857_bounds(x_max, y_max, zoom)
    transform = from_bounds(bw, bs, be, bn, width, height)

    if os.path.isdir(output_path) or output_path.endswith((os.sep, "/")):
        output_path = os.path.join(output_path, "esri_world_imagery.tif")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    print(f"Writing georeferenced COG to: {output_path}")
    profile = {
        "driver": "GTiff", "dtype": "uint8", "count": 3,
        "width": width, "height": height,
        "crs": "EPSG:3857", "transform": transform,
        "compress": "deflate", "tiled": True,
        "blockxsize": 512, "blockysize": 512, "photometric": "RGB",
    }
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(mosaic)
        dst.build_overviews([2, 4, 8, 16], Resampling.average)
    print("Successfully wrote Esri World Imagery COG!")
    return output_path

def main():
    parser = argparse.ArgumentParser(
        description="Download a georeferenced GeoTIFF for any city or country from "
                    "Sentinel-2 (10 m) or Esri World Imagery (sub-meter)."
    )
    parser.add_argument(
        "location",
        help="Name of the city, country, or region (e.g. 'Tehran', 'Paris', 'Germany')"
    )
    parser.add_argument(
        "--source", "-s",
        choices=["sentinel2", "esri"],
        default="esri",
        help="Imagery source: 'sentinel2' (10 m multispectral, global, dated) or "
             "'esri' (sub-meter World Imagery basemap). Default: esri"
    )
    parser.add_argument(
        "--output", "-o",
        help="Custom local output path for the downloaded GeoTIFF file. Defaults to sample directory."
    )
    parser.add_argument(
        "--cloud-cover", "-c",
        type=float,
        default=10.0,
        help="[sentinel2] Maximum cloud cover percentage allowed (default: 10.0)"
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=30,
        help="[sentinel2] Initial search range in days back from today (default: 30)"
    )
    parser.add_argument(
        "--zoom", "-z",
        type=int,
        default=19,
        help="[esri] XYZ zoom level: z17~1.2 m/px, z18~0.6 m/px, z19~0.3 m/px (default: 19)"
    )
    parser.add_argument(
        "--radius-km",
        type=float,
        default=2.0,
        help="[esri] Half-width of the square AOI in km around the geocoded centroid "
             "(default: 2.0 => 4 km box)"
    )

    args = parser.parse_args()

    # Set default output path if not specified
    if not args.output:
        clean_name = "".join(c if c.isalnum() else "_" for c in args.location.lower())
        suffix = "esri" if args.source == "esri" else "sentinel2_cog"
        args.output = f"/nvme/osint/sample/{clean_name}_{suffix}.tif"

    bbox_coords = geocode_location(args.location)
    if args.source == "esri":
        output_path = download_esri(bbox_coords, args.output, args.zoom, args.radius_km)
    else:
        scene, search_bbox = search_stac(bbox_coords, args.cloud_cover, args.days)
        output_path = download_cog(scene, args.output)

    # Try importing rasterio to print verified metadata
    try:
        import rasterio
        from rasterio.warp import transform_bounds
        with rasterio.open(output_path) as src:
            print("\n=== VERIFIED GEOTIFF METADATA ===")
            print(f"Local Path: {output_path}")
            print(f"Dimensions: {src.width} x {src.height} pixels")
            print(f"Bands:      {src.count}")
            print(f"Projection: {src.crs}")
            bounds = src.bounds
            wgs84_bounds = transform_bounds(src.crs, 'EPSG:4326', bounds.left, bounds.bottom, bounds.right, bounds.top)
            print(f"WGS84 Bounds:")
            print(f"  Lon: [{wgs84_bounds[0]:.4f}, {wgs84_bounds[2]:.4f}]")
            print(f"  Lat: [{wgs84_bounds[1]:.4f}, {wgs84_bounds[3]:.4f}]")
            print("=================================")
    except ImportError:
        pass

if __name__ == "__main__":
    main()
