"""Pre-fetch the Copernicus GLO-30 worldwide DEM for air-gap deployments.

Run this on a connected host once via the ``dem-baker`` Compose profile:

    docker compose --profile bake-dem up --build dem-baker

The bake downloads ~26,000 1°-tile GeoTIFFs from the AWS Open Data mirror
``copernicus-dem-30m`` (unauthenticated S3 + HTTPS) into the ``dem_data``
named volume and then runs ``gdalbuildvrt`` to assemble them into a single
``glo30.vrt`` mosaic that ``rasterio.open()`` reads transparently.

Idempotent: re-runs only fetch missing tiles, so a crashed bake can be
resumed with the same command.

Notes
-----
* The AWS Open Data mirror is unauthenticated; we use the HTTPS endpoint
  (``copernicus-dem-30m.s3.amazonaws.com``) to avoid needing an AWS SDK.
* Tile path on the mirror is::

      Copernicus_DSM_COG_10_<NS><lat>_00_<EW><lon>_00_DEM/
        Copernicus_DSM_COG_10_<NS><lat>_00_<EW><lon>_00_DEM.tif

  where ``<NS>`` is ``N``/``S`` and ``<EW>`` is ``E``/``W``. Ocean-only 1°
  cells do not exist on the mirror and produce 404 — we record those and
  skip them; the VRT simply has no coverage there (rasterio returns nodata).
* Worldwide coverage at 30 m: ~150 GB across ~26,000 tiles. Allow ~6-24 h
  on a typical link.
* The Copernicus DEM is provided under the ESA Standard Licence; an
  ``ATTRIBUTION.txt`` is dropped next to the tile tree.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import random
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("offline-dem")
MIRROR_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
ATTRIBUTION = (
    "Copernicus DEM GLO-30 © European Space Agency (ESA)\n"
    "Produced from the Copernicus Programme of the European Union;\n"
    "distributed under the ESA Standard Licence.\n"
)
USER_AGENT = "sentinel-offline-dem/1.0 (operator-built DEM cache)"
MAX_RETRIES = 6


def tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def tile_url(lat: int, lon: int) -> str:
    name = tile_name(lat, lon)
    return f"{MIRROR_BASE}/{name}/{name}.tif"


def tile_path(root: Path, lat: int, lon: int) -> Path:
    return root / "glo30" / f"{tile_name(lat, lon)}.tif"


def fetch_one(root: Path, lat: int, lon: int) -> str:
    path = tile_path(root, lat, lon)
    if path.exists() and path.stat().st_size > 0:
        return "skip"
    path.parent.mkdir(parents=True, exist_ok=True)
    url = tile_url(lat, lon)

    for attempt in range(MAX_RETRIES):
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=60) as resp:
                data = resp.read()
                tmp = path.with_suffix(".tif.partial")
                tmp.write_bytes(data)
                tmp.replace(path)
                return "ok"
        except HTTPError as exc:
            if exc.code == 404:
                # Ocean-only 1° cell; expected for most water tiles.
                return "404"
            if exc.code in (429, 500, 502, 503, 504):
                backoff = (2 ** attempt) + random.random()
                LOG.warning("%s HTTP %s — sleep %.1fs", path.name, exc.code, backoff)
                time.sleep(backoff)
                continue
            LOG.error("%s HTTP %s (giving up)", path.name, exc.code)
            return f"err{exc.code}"
        except URLError as exc:
            backoff = (2 ** attempt) + random.random()
            LOG.warning("%s %s — sleep %.1fs", path.name, exc.reason, backoff)
            time.sleep(backoff)
        except Exception as exc:  # noqa: BLE001
            LOG.error("%s unexpected %r", path.name, exc)
            return "err"
    return "retry-exhausted"


def build_vrt(root: Path) -> Path:
    """Assemble all fetched tiles into ``glo30.vrt`` via gdalbuildvrt."""
    tiles_dir = root / "glo30"
    vrt_path = root / "glo30.vrt"
    list_path = root / "glo30.tiles.txt"
    tiles = sorted(p for p in tiles_dir.glob("*.tif") if p.stat().st_size > 0)
    if not tiles:
        raise RuntimeError(f"no tiles found under {tiles_dir}; nothing to mosaic")
    list_path.write_text("\n".join(str(p) for p in tiles), encoding="utf-8")
    LOG.info("building VRT from %d tiles → %s", len(tiles), vrt_path)
    subprocess.run(
        ["gdalbuildvrt", "-input_file_list", str(list_path), str(vrt_path)],
        check=True,
    )
    return vrt_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="/data/dem", help="output root (held by dem_data volume)")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="parallel HTTPS workers (keep moderate — S3 throttles per-IP at high concurrency)",
    )
    parser.add_argument(
        "--lat-min", type=int, default=-90, help="southern latitude bound (deg)",
    )
    parser.add_argument(
        "--lat-max", type=int, default=90, help="northern latitude bound (deg, exclusive)",
    )
    parser.add_argument(
        "--lon-min", type=int, default=-180, help="western longitude bound (deg)",
    )
    parser.add_argument(
        "--lon-max", type=int, default=180, help="eastern longitude bound (deg, exclusive)",
    )
    parser.add_argument(
        "--vrt-only",
        action="store_true",
        help="skip the fetch loop and only rebuild glo30.vrt from existing tiles",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "ATTRIBUTION.txt").write_text(ATTRIBUTION, encoding="utf-8")

    if not args.vrt_only:
        cells: list[tuple[int, int]] = []
        for lat in range(args.lat_min, args.lat_max):
            for lon in range(args.lon_min, args.lon_max):
                cells.append((lat, lon))
        LOG.info("planning %d 1° cells into %s", len(cells), root)

        progress_path = root / ".progress.json"
        counters = {"ok": 0, "skip": 0, "404": 0, "err": 0}
        started = time.time()

        def update(result: str) -> None:
            if result == "ok":
                counters["ok"] += 1
            elif result == "skip":
                counters["skip"] += 1
            elif result == "404":
                counters["404"] += 1
            else:
                counters["err"] += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(fetch_one, root, lat, lon) for (lat, lon) in cells]
            done = 0
            for fut in concurrent.futures.as_completed(futures):
                update(fut.result())
                done += 1
                if done % 200 == 0 or done == len(futures):
                    elapsed = time.time() - started
                    LOG.info(
                        "%d/%d  (ok=%d skip=%d 404=%d err=%d, %.0fs)",
                        done, len(futures),
                        counters["ok"], counters["skip"], counters["404"], counters["err"],
                        elapsed,
                    )
                    progress_path.write_text(
                        json.dumps({**counters, "elapsed_s": elapsed}),
                        encoding="utf-8",
                    )

        LOG.info("fetch done: %s", counters)
        if counters["err"] > 0:
            LOG.warning("%d tiles failed; VRT will skip them", counters["err"])

    build_vrt(root)
    LOG.info("VRT ready at %s/glo30.vrt", root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
