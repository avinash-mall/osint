"""Pre-fetch OpenTopoMap raster terrain tiles for air-gap deployments.

Run this on a connected host before `docker compose build`. Tiles land at
``assets/static/terrain/{z}/{x}/{y}.png`` and are baked into the
``sentinel-assets:offline`` image by ``assets/Dockerfile``.

The script is idempotent: re-running fills only missing tiles, so a
crashed run can be resumed with the same command.

    python scripts/build_offline_terrain.py --zoom 0-14

Notes
-----
* OpenTopoMap is volunteer-hosted and rate-limits aggressively. We back
  off exponentially on 429/5xx; expect the first full bake to take
  significantly longer than the CARTO basemap bake. Use a smaller
  ``--zoom`` range (e.g. 0-4) for a smoke test.
* World coverage at z=0..14 is ~358 M tiles / ~22 GB OpenTopoMap. Allow
  multiple days at low concurrency — the upstream rate-limits
  aggressively. Use ``--zoom 0-10`` for a faster bake if block-level
  terrain isn't needed. z=14 matches the frontend overlay autohide
  threshold — see ``docs/decisions/why-basemap-z14-cap.md``.
* Per OpenTopoMap's policy, cache aggressively and keep concurrency low.
* Attribution is mandatory under OpenTopoMap/OSM's CC-BY-SA: an
  ``ATTRIBUTION.txt`` is dropped next to the tile tree.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import itertools
import json
import logging
import random
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("offline-terrain")
URL_TEMPLATE = "https://tile.opentopomap.org/{z}/{x}/{y}.png"
ATTRIBUTION = "© OpenStreetMap contributors © OpenTopoMap (CC-BY-SA)\n"
USER_AGENT = "sentinel-offline-terrain/1.0 (operator-built tile cache)"
MAX_RETRIES = 6


def parse_zoom_range(spec: str) -> range:
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return range(int(lo), int(hi) + 1)
    z = int(spec)
    return range(z, z + 1)


def tile_path(root: Path, z: int, x: int, y: int) -> Path:
    return root / str(z) / str(x) / f"{y}.png"


def fetch_one(root: Path, z: int, x: int, y: int) -> str:
    path = tile_path(root, z, x, y)
    if path.exists() and path.stat().st_size > 0:
        return "skip"
    path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(MAX_RETRIES):
        url = URL_TEMPLATE.format(z=z, x=x, y=y)
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
                tmp = path.with_suffix(".png.partial")
                tmp.write_bytes(data)
                tmp.replace(path)
                return "ok"
        except HTTPError as exc:
            if exc.code == 404:
                return "404"
            if exc.code in (429, 500, 502, 503, 504):
                backoff = (2 ** attempt) + random.random()
                LOG.warning("z%s/%s/%s HTTP %s — sleep %.1fs", z, x, y, exc.code, backoff)
                time.sleep(backoff)
                continue
            LOG.error("z%s/%s/%s HTTP %s (giving up)", z, x, y, exc.code)
            return f"err{exc.code}"
        except URLError as exc:
            backoff = (2 ** attempt) + random.random()
            LOG.warning("z%s/%s/%s %s — sleep %.1fs", z, x, y, exc.reason, backoff)
            time.sleep(backoff)
        except Exception as exc:  # noqa: BLE001
            LOG.error("z%s/%s/%s unexpected %r", z, x, y, exc)
            return "err"
    return "retry-exhausted"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zoom", default="0-14", help="zoom range, e.g. 0-14 or 6")
    parser.add_argument("--out", default="assets/static/terrain", help="output directory")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="parallel HTTP workers (keep low — OpenTopoMap rate-limits aggressively)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "ATTRIBUTION.txt").write_text(ATTRIBUTION, encoding="utf-8")

    zooms = parse_zoom_range(args.zoom)
    total = sum(4 ** z for z in zooms)
    LOG.info("planning %d tiles for zoom %s into %s", total, args.zoom, root)

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

    # Bound in-flight futures (see build_offline_basemap.py): materialising all
    # 4**z futures up front exhausts memory at high zoom. Sliding window instead.
    max_inflight = max(args.concurrency * 4, 64)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for z in zooms:
            n = 2 ** z
            total_z = n * n
            LOG.info("zoom %d: %d × %d tiles", z, n, n)
            coords = ((x, y) for x in range(n) for y in range(n))  # lazy
            inflight = {
                pool.submit(fetch_one, root, z, x, y)
                for x, y in itertools.islice(coords, max_inflight)
            }
            done = 0
            while inflight:
                completed, inflight = concurrent.futures.wait(
                    inflight, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for fut in completed:
                    update(fut.result())
                    done += 1
                    nxt = next(coords, None)
                    if nxt is not None:
                        inflight.add(pool.submit(fetch_one, root, z, nxt[0], nxt[1]))
                    if done % 500 == 0 or done == total_z:
                        elapsed = time.time() - started
                        LOG.info(
                            "z%d %d/%d  (ok=%d skip=%d 404=%d err=%d, %.0fs)",
                            z, done, total_z,
                            counters["ok"], counters["skip"], counters["404"], counters["err"],
                            elapsed,
                        )
                        progress_path.write_text(json.dumps({**counters, "elapsed_s": elapsed}), encoding="utf-8")

    LOG.info("done: %s", counters)
    return 0 if counters["err"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
