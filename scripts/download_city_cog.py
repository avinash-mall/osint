#!/usr/bin/env python3
"""
Sentinel-2 COG Imagery Downloader

This script resolves any city or country name to its geographic bounding box
using the open OpenStreetMap Nominatim geocoding API, queries the public STAC API
for the latest low-cloud Sentinel-2 L2A Cloud-Optimized GeoTIFF (COG), and downloads
the true-color visual composite with full georeferencing metadata.
"""

import sys
import os
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

def main():
    parser = argparse.ArgumentParser(
        description="Download a Sentinel-2 true-color Cloud-Optimized GeoTIFF (COG) for any city or country."
    )
    parser.add_argument(
        "location",
        help="Name of the city, country, or region (e.g. 'Tehran', 'Paris', 'Germany')"
    )
    parser.add_argument(
        "--output", "-o",
        help="Custom local output path for the downloaded GeoTIFF file. Defaults to sample directory."
    )
    parser.add_argument(
        "--cloud-cover", "-c",
        type=float,
        default=10.0,
        help="Maximum cloud cover percentage allowed (default: 10.0)"
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=30,
        help="Initial search range in days back from today (default: 30)"
    )
    
    args = parser.parse_args()
    
    # Set default output path if not specified
    if not args.output:
        clean_name = "".join(c if c.isalnum() else "_" for c in args.location.lower())
        args.output = f"/nvme/osint/sample/{clean_name}_sentinel2_cog.tif"
        
    bbox_coords = geocode_location(args.location)
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
