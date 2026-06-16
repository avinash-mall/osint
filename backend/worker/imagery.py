"""Imagery ingest orchestration: COG conversion, slice_and_infer, SAR CFAR,
detection persistence, candidate links, process_satellite_imagery task."""

from worker.config import *  # noqa: F401,F403
from worker.app import celery_app  # noqa: F401
from worker._shared import *  # noqa: F401,F403
from worker.dispatch import *  # noqa: F401,F403
from worker.postprocess import *  # noqa: F401,F403
from worker.graph import _parse_embedding_anchor

COG_COMPRESS = (os.getenv("COG_COMPRESS") or "ZSTD").strip().upper()
COG_BLOCKSIZE = env_int("COG_BLOCKSIZE", 512)
COG_PREDICTOR = env_int("COG_PREDICTOR", 2)  # 2 = horizontal differencing; helps DEFLATE/ZSTD on imagery


def ensure_cog(input_path: str, output_path: str) -> str:
    """Convert any raster to Cloud Optimized GeoTIFF.

    Compression defaults to ZSTD (faster read/write than DEFLATE at similar
    ratio per the LERC/ZSTD benchmarks in gpxz.io and kokoalberti.com).
    Operators override via ``COG_COMPRESS`` (e.g. ``DEFLATE``, ``LERC_ZSTD``,
    ``LZW``, ``NONE``). ``COG_BLOCKSIZE`` exposes the COG tile width — 512
    matches the default chip-corner alignment used downstream.
    """
    if input_path.endswith(".nc") or input_path.endswith(".netcdf"):
        # Handle NetCDF via rioxarray
        try:
            import rioxarray
            ds = rioxarray.open_rasterio(input_path)
            # If time dimension exists, take the first slice
            if "time" in ds.dims:
                ds = ds.isel(time=0)
            # If band dimension > 1 and we want RGB or single band
            if "band" in ds.dims and ds.sizes.get("band", 0) > 1:
                ds = ds.isel(band=0)
            ds.rio.to_raster(output_path, driver="COG", compress=COG_COMPRESS)
            return output_path
        except Exception as e:
            raise RuntimeError(f"NetCDF conversion failed: {e}")
    else:
        # GeoTIFF / JP2 -> COG via GDAL
        cmd = [
            "gdal_translate",
            input_path,
            output_path,
            "-of", "COG",
            "-co", f"COMPRESS={COG_COMPRESS}",
            "-co", f"BLOCKSIZE={COG_BLOCKSIZE}",
            "-co", "OVERVIEWS=AUTO",
            "-co", "OVERVIEW_RESAMPLING=AVERAGE",
            "-co", "BIGTIFF=IF_SAFER",
            "-co", "NUM_THREADS=ALL_CPUS",
        ]
        # PREDICTOR is only honored by DEFLATE/LZW/ZSTD families; harmless
        # to pass even when COMPRESS=NONE — GDAL just ignores it. Skip for
        # LERC since LERC has its own MAX_Z_ERROR semantics.
        if not COG_COMPRESS.startswith("LERC") and COG_COMPRESS != "NONE":
            cmd.extend(["-co", f"PREDICTOR={COG_PREDICTOR}"])
        subprocess.run(cmd, check=True)
        return output_path


def get_raster_footprint(cog_path: str):
    """Extract bounding box as a Shapely Polygon in EPSG:4326."""
    with rasterio.open(cog_path) as src:
        bounds = src.bounds
        crs = src.crs
        # Reproject bounds to WGS84 if needed
        if crs and crs.to_string() != "EPSG:4326":
            from rasterio.warp import transform_bounds
            min_lon, min_lat, max_lon, max_lat = transform_bounds(
                crs, "EPSG:4326", bounds.left, bounds.bottom, bounds.right, bounds.top
            )
        else:
            min_lon, min_lat, max_lon, max_lat = bounds.left, bounds.bottom, bounds.right, bounds.top
        
        footprint = MultiPolygon([Polygon([
            (min_lon, min_lat),
            (min_lon, max_lat),
            (max_lon, max_lat),
            (max_lon, min_lat),
            (min_lon, min_lat)
        ])])
        return footprint, min_lon, min_lat, max_lon, max_lat


def _remote_imagery_max_bytes() -> int:
    try:
        return int(os.getenv("REMOTE_IMAGERY_MAX_BYTES", str(10 * 1024 * 1024 * 1024)))
    except ValueError:
        return 10 * 1024 * 1024 * 1024


def _remote_imagery_allowed(image_url: str) -> None:
    """Validate an operator-supplied remote imagery URL before worker fetch."""
    if os.getenv("ALLOW_REMOTE_IMAGERY_URLS", "0") != "1":
        raise RuntimeError("Remote imagery URLs are disabled; stage files under IMAGERY_PATH/incoming")
    parsed = urlparse(image_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RuntimeError(f"Unsupported imagery URL scheme: {parsed.scheme}")
    allowed_hosts = {
        host.strip().lower()
        for host in os.getenv("REMOTE_IMAGERY_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    hostname = parsed.hostname.lower()
    if allowed_hosts and hostname not in allowed_hosts:
        raise RuntimeError(f"Remote imagery host {hostname!r} is not allowlisted")
    try:
        infos = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise RuntimeError(f"Remote imagery host did not resolve: {hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise RuntimeError(f"Remote imagery host resolves to disallowed address {ip}")


def resolve_input_path(image_url: str) -> str:
    """Resolve local, HTTP(S), or unsupported remote imagery references into a local file."""
    parsed = urlparse(image_url)
    incoming_dir = os.path.join(IMAGERY_PATH, "incoming")
    os.makedirs(incoming_dir, exist_ok=True)

    if parsed.scheme in ("http", "https"):
        _remote_imagery_allowed(image_url)
        filename = os.path.basename(parsed.path) or f"{uuid.uuid4()}.tif"
        input_path = os.path.join(incoming_dir, filename)
        max_bytes = _remote_imagery_max_bytes()
        size = 0
        with requests.get(image_url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(input_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        size += len(chunk)
                        if max_bytes > 0 and size > max_bytes:
                            handle.close()
                            try:
                                os.remove(input_path)
                            except OSError:
                                pass
                            raise RuntimeError(f"Remote imagery exceeds REMOTE_IMAGERY_MAX_BYTES ({max_bytes})")
                        handle.write(chunk)
        return input_path

    if parsed.scheme == "s3":
        raise RuntimeError("s3:// imagery ingestion requires an S3 client configuration and is not enabled.")

    if parsed.scheme and parsed.scheme != "file":
        raise RuntimeError(f"Unsupported imagery URL scheme: {parsed.scheme}")

    local_path = parsed.path if parsed.scheme == "file" else image_url
    if os.path.exists(local_path):
        return local_path

    filename = os.path.basename(local_path)
    input_path = os.path.join(incoming_dir, filename)
    if os.path.exists(input_path):
        return input_path

    raise FileNotFoundError(f"Imagery file not found: {image_url}")


def classify_detection_ontologies(detections: list, progress_callback=None) -> dict[str, dict]:
    grouped: dict[str, list[float]] = {}
    for det in detections:
        det_class = det.get("class", "Unknown")
        grouped.setdefault(det_class, []).append(float(det.get("confidence") or 0))

    ontology_by_class: dict[str, dict] = {}
    if not grouped:
        if progress_callback:
            progress_callback("classification", 94, "No detections found; skipping class labeling.")
        return ontology_by_class

    for det_class in grouped:
        ontology_by_class[det_class] = {
            **detection_ontology(det_class),
            "status": "deterministic",
        }
    if progress_callback:
        progress_callback("classification", 94, "Detection classes labeled with deterministic ontology rules.")
    return ontology_by_class


def slice_and_infer(
    cog_path: str,
    pass_id: int,
    chip_size: int = DEFAULT_INFERENCE_CHIP_SIZE,
    overlap: int = DEFAULT_INFERENCE_OVERLAP,
    max_chips: int = MAX_INFERENCE_CHIPS,
    progress_callback=None,
    inference_metadata: dict | None = None,
    on_chip_store=None,
):
    """Slice COG into chips, send each to SAM3 /detect, dedupe and return detections.

    When `on_chip_store` is provided, surviving detections from each chip are
    handed off to that callback (which inserts them into the DB and fires a
    `detections_partial` WS event) instead of being accumulated for a single
    bulk store at the end. The returned `summary` reflects the same totals
    either way; the returned `detections` list is empty in the streaming path
    because every survivor has already been persisted."""
    inference_metadata = inference_metadata or {}
    streaming = on_chip_store is not None
    # Snapshot for the pass-level summary; the per-chip decision path calls
    # active_detection_policy() per batch so a long pass picks up admin
    # confidence-override changes without a worker restart.
    detection_policy = active_detection_policy()
    with rasterio.open(cog_path) as src:
        width = src.width
        height = src.height
        transform = src.transform
        crs = src.crs

        # Phase 2.6: opt-in WBF dedup. ``DEDUPE_METHOD=wbf`` swaps the
        # confidence-greedy NMS for confidence-averaged Weighted Boxes
        # Fusion so multi-detector agreement boosts the fused score
        # rather than the loudest single model winning. Default remains
        # NMS until the larger eval harness validates WBF doesn't
        # regress per-class recall.
        if (os.getenv("DEDUPE_METHOD", "nms") or "nms").strip().lower() == "wbf":
            dedupe_idx: _DetectionDedupeIndex | _WeightedBoxFusionIndex = _WeightedBoxFusionIndex(
                iou_threshold=float(os.getenv("WBF_IOU_THRESHOLD", "0.55")),
                expected_models=int(os.getenv("WBF_EXPECTED_MODELS", "2")),
            )
        else:
            dedupe_idx = _DetectionDedupeIndex()
        # A fused head can continue changing when later chips arrive. Persisting
        # every intermediate WBF head in streaming mode creates duplicate DB
        # rows and stores stale geometry, so WBF is flushed once at the end.
        defer_streaming_store = streaming and isinstance(dedupe_idx, _WeightedBoxFusionIndex)
        all_kept: list[dict] = []  # only populated when not streaming
        completed_chip_count = 0

        # Phase 1.3: build the list of (chip_size, overlap, max_chips) passes.
        # The first entry is the main pass at the caller's configured size;
        # an optional second entry runs at INFERENCE_SMALL_OBJECT_CHIP_SIZE so
        # small-class targets get a higher pixel-per-object budget. Both
        # passes share the same dedupe_idx, so NMS suppresses cross-scale
        # duplicates of the same object.
        chip_passes: list[tuple[int, int, int]] = [(chip_size, overlap, max_chips)]
        if (
            INFERENCE_SMALL_OBJECT_CHIP_SIZE > 0
            and INFERENCE_SMALL_OBJECT_CHIP_SIZE != chip_size
        ):
            chip_passes.append((
                INFERENCE_SMALL_OBJECT_CHIP_SIZE,
                INFERENCE_SMALL_OBJECT_OVERLAP,
                INFERENCE_SMALL_OBJECT_MAX_CHIPS,
            ))

        # Block alignment (Phase 2): read the source's internal tile size
        # once at open time and pass it to the grid planner so each chip
        # origin lands on a file-block boundary. Cuts the per-chip GDAL
        # read cost on tiled COGs (4× bytes drag in for misaligned reads).
        try:
            src_block = src.block_shapes[0]  # (block_y, block_x)
            src_block_size = (int(src_block[1]), int(src_block[0]))
        except Exception:
            src_block_size = None

        # Pre-plan every pass so total_windows = sum across passes — keeps the
        # progress callback's percentage monotonic 0-100% across multi-scale.
        pass_plans: list[dict] = []
        for pass_chip_size, pass_overlap, pass_max_chips in chip_passes:
            g = plan_inference_grid(
                width,
                height,
                pass_chip_size,
                pass_overlap,
                pass_max_chips,
                block_size=src_block_size,
            )
            pass_plans.append({
                "chip_size": pass_chip_size,
                "overlap": pass_overlap,
                "max_chips": pass_max_chips,
                "grid": g,
                "step": g["step"],
                "planned_total": g["planned_total"],
                "full_scene": False,
            })

        # Optional coarse full-scene pass. plan_inference_grid cannot express a
        # single whole-image window, so this pass is planned by hand: it reads
        # the full (0,0,width,height) extent decimated to ~chip_size (preserving
        # aspect, capped so neither side exceeds chip_size) and runs exactly one
        # inference. It contributes exactly 1 window to total_windows so the
        # progress bar stays monotonic. The grid reuses `main_plan["grid"]` for
        # summary fields, so the full-scene plan carries a None grid and is
        # skipped by every grid-dependent code path via its `full_scene` flag.
        if INFERENCE_FULL_SCENE_PASS:
            fs_chip = pass_plans[0]["chip_size"]
            longest = max(1, max(width, height))
            fs_decimation = max(1.0, longest / float(fs_chip))
            fs_w = max(1, int(round(width / fs_decimation)))
            fs_h = max(1, int(round(height / fs_decimation)))
            # The grid here is synthetic — a 1-window stand-in so the closures
            # that read grid["source_total"]/["sampled"]/["max_chips"]/["step"]
            # keep working when this pass is current. It deliberately omits the
            # sliding-window offset keys; the loop branches on `full_scene`
            # before any grid iteration, so those are never read.
            pass_plans.append({
                "chip_size": fs_chip,
                "overlap": 0,
                "max_chips": 1,
                "grid": {
                    "source_total": 1,
                    "sampled": False,
                    "max_chips": 1,
                    "step": fs_chip,
                    "planned_total": 1,
                },
                "step": fs_chip,
                "planned_total": 1,
                "full_scene": True,
                "fs_out_w": fs_w,
                "fs_out_h": fs_h,
            })
        total_windows = sum(p["planned_total"] for p in pass_plans)
        processed_windows = 0
        failed_windows = 0
        last_reported_percent = None

        # The first pass is the primary one for summary fields; per-pass
        # breakdown lives under `passes`.
        main_plan = pass_plans[0]
        grid = main_plan["grid"]
        step = main_plan["step"]
        # The full-scene plan carries only a synthetic 1-window grid; exclude it
        # from the grid-derived coverage/source-total aggregates so they reflect
        # the real sliding-window passes.
        _grid_plans = [p for p in pass_plans if not p["full_scene"]]
        coverage_fraction = round(total_windows / max(1, sum(p["grid"]["source_total"] for p in _grid_plans)), 4)
        inference_summary = {
            "chip_size": chip_size,
            "overlap": overlap,
            "step": step,
            "planned_chips": total_windows,
            "source_total_chips": sum(p["grid"]["source_total"] for p in _grid_plans),
            "processed_chips": 0,
            "inference_speed_profile": INFERENCE_SPEED_PROFILE,
            "coverage_fraction": coverage_fraction,
            "sampling_enabled": any(p["grid"]["sampled"] for p in _grid_plans),
            "max_inference_chips": grid["max_chips"],
            "dedupe_method": "wbf" if isinstance(dedupe_idx, _WeightedBoxFusionIndex) else "obb_nms",
            "threshold_profile": detection_policy["threshold_profile"],
            "taxonomy_version": detection_policy["taxonomy_version"],
            "model_version": detection_policy["model_version"],
            "candidates_by_layer": {},
            "suppressed_by_policy": 0,
            "suppressed_by_nms": 0,
            "max_pending_chips": INFERENCE_MAX_PENDING_CHIPS,
            "chip_spool_max_bytes": INFERENCE_CHIP_SPOOL_MAX_BYTES,
            "multi_scale": len(pass_plans) > 1,
            "passes": [
                {
                    "chip_size": p["chip_size"],
                    "overlap": p["overlap"],
                    "planned_chips": p["planned_total"],
                    "source_total_chips": p["grid"]["source_total"],
                    "sampling_enabled": p["grid"]["sampled"],
                    "full_scene": p["full_scene"],
                }
                for p in pass_plans
            ],
        }

        if progress_callback:
            if grid["sampled"]:
                message = f"Large raster detected; sampling {total_windows} of {grid['source_total']} chips for inference."
            else:
                message = f"Prepared {total_windows} raster chips for inference."
            progress_callback(
                "inference",
                56,
                message,
                {
                    "planned_chips": total_windows,
                    "total_chips": total_windows,
                    "source_total_chips": grid["source_total"],
                    "processed_chips": 0,
                    "failed_chips": 0,
                    "inference_speed_profile": INFERENCE_SPEED_PROFILE,
                    "max_inference_chips": grid["max_chips"],
                    "sampling_enabled": grid["sampled"],
                    "coverage_fraction": coverage_fraction,
                },
            )

        # HTTP session shared across the chip ThreadPoolExecutor so connection
        # pooling actually engages (default requests.post opens a fresh TCP per
        # call). pool_maxsize must be >= concurrency or requests will warn and
        # silently drop connections.
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=INFERENCE_CHIP_CONCURRENCY * 2,
            pool_maxsize=INFERENCE_CHIP_CONCURRENCY * 2,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        # Phase 3: producer parallelism. Reader pool runs valid-mask + read +
        # encode in parallel (GDAL releases the GIL during RasterIO), then
        # the existing poster pool fans out HTTP POSTs to the inference
        # service. A bounded `queue.Queue` of dataset handles lets each
        # reader thread reuse the same `rasterio.open(cog_path)` for its
        # lifetime instead of re-parsing the COG header per chip; see
        # docs/decisions/why-parallel-chip-readers.md for the trade-offs
        # (notably why we avoid GDAL RFC 101 thread-safe datasets for now).
        reader_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=INFERENCE_READER_POOL_SIZE,
            thread_name_prefix="chip-read",
        )
        poster_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=INFERENCE_CHIP_CONCURRENCY,
            thread_name_prefix="chip-post",
        )
        # Keep backwards-compat alias so any inner closure still referring to
        # `executor` keeps pointing at the poster pool.
        executor = poster_executor
        # Pool of rasterio handles, one per reader worker. Reader tasks borrow
        # via get()/put(); never more than `INFERENCE_READER_POOL_SIZE` handles
        # are open at once.
        _src_handles: queue.Queue = queue.Queue()
        for _ in range(INFERENCE_READER_POOL_SIZE):
            _src_handles.put(rasterio.open(cog_path))
        pending: dict[concurrent.futures.Future, dict] = {}

        # Phase 4: probe /capabilities once. When the inference service
        # advertises ``raw_endpoint=true`` we route RGB chips through the
        # raw binary path; everything else (MSI/SAR) keeps using /detect.
        _caps = _negotiate_inference_capabilities(session)
        _raw_rgb_enabled = bool(_caps.get("raw_endpoint")) and (
            "rgb" in (_caps.get("supported_modalities") or [])
        )

        def _apply_chip_response(ctx: dict, inference_response: dict) -> list[dict]:
            """Convert the chip's inference response into pass-frame detections.

            Returns the per-chip detection list (one entry per surviving
            inference output). The caller is responsible for running NMS and
            either streaming-store or accumulating these."""
            x = ctx["x"]; y = ctx["y"]
            win_width = ctx["win_width"]; win_height = ctx["win_height"]
            # scale_x/scale_y = source-px per chip-px. 1.0 for normal chips
            # (chip-px == source-window-px); >1 only for the decimated full-scene
            # pass, where chip-px coords must be scaled up before the affine.
            scale_x = ctx.get("scale_x", 1.0); scale_y = ctx.get("scale_y", 1.0)
            valid_mask = ctx.get("valid_mask")
            batch_policy = active_detection_policy()
            debug_counts = inference_response.get("debug_counts") or {}
            for layer, count in (debug_counts.get("candidates_by_layer") or {}).items():
                try:
                    inference_summary["candidates_by_layer"][str(layer)] = (
                        int(inference_summary["candidates_by_layer"].get(str(layer), 0))
                        + int(count)
                    )
                except (TypeError, ValueError):
                    continue
            try:
                inference_summary["suppressed_by_nms"] += int(debug_counts.get("suppressed_by_nms") or 0)
            except (TypeError, ValueError):
                pass
            chip_detections = []
            chip_results: list[dict] = []
            for det in inference_response.get("detections", []):
                det["model_version"] = (
                    inference_response.get("model_version")
                    or det.get("model_version")
                )
                det["taxonomy_version"] = (
                    inference_response.get("taxonomy_version")
                    or det.get("taxonomy_version")
                )
                det["model_versions"] = (
                    inference_response.get("model_versions")
                    or det.get("model_versions")
                )
                det["threshold_profile"] = (
                    inference_response.get("threshold_profile")
                    or det.get("threshold_profile")
                )
                chip_detections.append(det)

            for det in chip_detections:
                try:
                    cx, cy, w, h = [float(value) for value in det["bbox"][:4]]
                except (KeyError, TypeError, ValueError):
                    continue

                chip_px_cx = cx * win_width
                chip_px_cy = cy * win_height
                chip_px_w = max(0.0, w * win_width)
                chip_px_h = max(0.0, h * win_height)

                # Phase 3.10: apply per-class valid-fraction threshold so
                # water-edge ships aren't dropped at the same 0.20 floor as
                # ground vehicles. parent_class is derived from the
                # ontology normalizer in _apply_chip_response.
                _det_class_for_clip = det.get("parent_class") or det.get("class")
                local_box = clip_box_to_valid_mask(
                    valid_mask,
                    chip_px_cx - chip_px_w / 2,
                    chip_px_cy - chip_px_h / 2,
                    chip_px_cx + chip_px_w / 2,
                    chip_px_cy + chip_px_h / 2,
                    min_valid_fraction=_valid_fraction_threshold_for(_det_class_for_clip),
                )
                if local_box is None:
                    continue
                local_x1, local_y1, local_x2, local_y2 = local_box

                # local_* and the obb coords below are CHIP-pixel; scale_* maps
                # them to source-pixel (identity for normal passes).
                abs_px_x1 = clamp_float(x + local_x1 * scale_x, 0, width)
                abs_px_y1 = clamp_float(y + local_y1 * scale_y, 0, height)
                abs_px_x2 = clamp_float(x + local_x2 * scale_x, 0, width)
                abs_px_y2 = clamp_float(y + local_y2 * scale_y, 0, height)
                if abs_px_x2 <= abs_px_x1 or abs_px_y2 <= abs_px_y1:
                    continue

                pixel_obb = []
                if det.get("obb") and len(det["obb"]) == 8:
                    for index, value in enumerate(det["obb"]):
                        if index % 2 == 0:
                            pixel_obb.append(clamp_float(x + float(value) * win_width * scale_x, 0, width))
                        else:
                            pixel_obb.append(clamp_float(y + float(value) * win_height * scale_y, 0, height))
                else:
                    pixel_obb = [
                        abs_px_x1, abs_px_y1,
                        abs_px_x2, abs_px_y1,
                        abs_px_x2, abs_px_y2,
                        abs_px_x1, abs_px_y2,
                    ]

                pixel_points = list(zip(pixel_obb[0::2], pixel_obb[1::2]))
                lons, lats = [], []
                for px, py in pixel_points:
                    lon, lat = transform * (px, py)
                    lons.append(lon)
                    lats.append(lat)

                if crs and crs.to_string() != "EPSG:4326":
                    from rasterio.warp import transform as rasterio_transform
                    lons, lats = rasterio_transform(crs, "EPSG:4326", lons, lats)

                geo_polygon = [coord for point in zip(lons, lats) for coord in point]
                lon1, lat1, lon2, lat2 = min(lons), min(lats), max(lons), max(lats)

                original_class = det.get("original_class") or det.get("class", "unknown")
                raw_confidence = float(det.get("confidence") or 0.0)
                # Phase 2.5: apply per-model temperature scaling so different
                # detectors' confidence distributions become comparable before
                # NMS and the per-class threshold gate consume them. T defaults
                # to 1.0 (identity) when no calibration is configured for this
                # model — the call is safe and cheap.
                model_tag = _calibration_tag_for_detection(det)
                confidence = calibrate_confidence(raw_confidence, model_tag)
                from calibration import temperature_for as _t_for
                det["raw_confidence"] = raw_confidence
                det["calibrated_confidence"] = confidence
                det["model_temperature"] = _t_for(model_tag)
                det["confidence"] = confidence
                decision = detection_decision(original_class, confidence, batch_policy)
                policy_review_status = decision["review_status"]
                # Open-vocab policy: drop only when the operator explicitly raised
                # GLOBAL_CONFIDENCE_FLOOR / PER_CLASS_CONFIDENCE_OVERRIDES above
                # this detection's confidence. Otherwise everything passes through.
                if decision["review_status"] == "below_class_threshold":
                    inference_summary["suppressed_by_policy"] += 1
                    continue

                # Preserve the original SAM3 prompt as the canonical class. The
                # decision["parent_class"] is a coarse legacy bucket from naive
                # substring matching (e.g. "disturbed earth" → "bed" → "furniture")
                # and overwriting class with it discards information the
                # frontend defence-ontology classifier needs.
                det["class"] = decision["original_class"]
                det_model_version = det.get("model_version") or decision["model_version"]
                det_taxonomy_version = det.get("taxonomy_version") or decision["taxonomy_version"]
                det_threshold_profile = det.get("threshold_profile") or decision["threshold_profile"]
                det.update({**decision, **{
                    "model_version": det_model_version,
                    "taxonomy_version": det_taxonomy_version,
                    "threshold_profile": det_threshold_profile,
                    "policy_review_status": det.get("policy_review_status") or policy_review_status,
                }})
                det["pixel_bbox"] = [abs_px_x1, abs_px_y1, abs_px_x2, abs_px_y2]
                det["pixel_obb"] = pixel_obb
                det["geo_bbox"] = [lon1, lat1, lon2, lat2]
                det["geo_polygon"] = geo_polygon
                # Phase 3.11: position-uncertainty ellipse — replaces the
                # Phase 7.35 scalar with semi-major / semi-minor axes in
                # metres and a bearing in degrees (clockwise from north,
                # WGS-84 convention). The ellipse is anisotropic when the
                # raster pixel is non-square or the CRS is geographic
                # (where 1° lon shrinks with cos(latitude)), which is the
                # common case for Sentinel-1 / Landsat tiles. The
                # ``position_uncertainty_m`` scalar is preserved as the
                # 95%-CEP equivalent (semi-major × 1) so downstream code
                # that already consumes it keeps working.
                try:
                    px_w = abs(float(transform.a))
                    px_h = abs(float(transform.e))
                    if crs and crs.is_geographic:
                        mid_lat_rad = math.radians((lat1 + lat2) / 2.0)
                        meters_per_deg_lat = 111_320.0
                        meters_per_deg_lon = 111_320.0 * max(math.cos(mid_lat_rad), 0.01)
                        sigma_x_m = 2.0 * px_w * meters_per_deg_lon  # easting
                        sigma_y_m = 2.0 * px_h * meters_per_deg_lat  # northing
                    else:
                        sigma_x_m = 2.0 * px_w
                        sigma_y_m = 2.0 * px_h
                    # Map (sigma_x, sigma_y) to (semi-major, semi-minor) +
                    # bearing. With axis-aligned pixel uncertainty the
                    # bearing is 0° when sigma_y dominates (north-south) or
                    # 90° when sigma_x dominates (east-west).
                    semi_major = max(sigma_x_m, sigma_y_m)
                    semi_minor = min(sigma_x_m, sigma_y_m)
                    bearing_deg = 0.0 if sigma_y_m >= sigma_x_m else 90.0
                    det["position_uncertainty_m"] = round(semi_major, 3)
                    det["position_uncertainty_ellipse"] = {
                        "semi_major_m": round(semi_major, 3),
                        "semi_minor_m": round(semi_minor, 3),
                        "bearing_deg": bearing_deg,
                        "confidence": 0.95,  # 2-sigma ≈ 95%
                        "source": "gsd_propagation",
                    }
                    size = estimate_size(
                        geo_polygon=geo_polygon,
                        crs="EPSG:4326",
                        pixel_width_m=sigma_x_m / 2.0,
                        pixel_height_m=sigma_y_m / 2.0,
                        mask_area_px=int(det.get("area") or 0),
                    )
                    if size is not None:
                        det["size_estimate"] = size
                except Exception:
                    pass
                det["chip_id"] = f"{pass_id}:{x}:{y}:{win_width}:{win_height}"
                det["chip_window"] = [x, y, win_width, win_height]
                det["chip_valid_fraction"] = ctx.get("valid_fraction")
                det["coverage_fraction"] = coverage_fraction
                det["planned_chips"] = total_windows
                det["source_total_chips"] = grid["source_total"]
                det["sampling_enabled"] = grid["sampled"]
                det["dedupe_method"] = "obb_nms"
                chip_results.append(det)
            return chip_results

        def _report_inference_progress() -> None:
            nonlocal last_reported_percent
            if not progress_callback:
                return
            inferred_percent = int(processed_windows / total_windows * 100)
            if (
                processed_windows == 1
                or processed_windows == total_windows
                or last_reported_percent is None
                or inferred_percent > last_reported_percent
            ):
                last_reported_percent = inferred_percent
                progress_callback(
                    "inference",
                    55 + int(inferred_percent * 0.35),
                    f"Running inference on raster chips ({processed_windows}/{total_windows}).",
                    {
                        "processed_chips": processed_windows,
                        "failed_chips": failed_windows,
                        "total_chips": total_windows,
                        "planned_chips": total_windows,
                        "source_total_chips": grid["source_total"],
                        "sampling_enabled": grid["sampled"],
                        "inference_speed_profile": INFERENCE_SPEED_PROFILE,
                        "coverage_fraction": coverage_fraction,
                    },
                )

        def _reader_task(
            x: int,
            y: int,
            chip_w: int,
            chip_h: int,
            pass_index: int,
            out_shape: tuple[int, int] | None = None,
        ) -> tuple | None:
            """Read + encode one chip in a reader thread; returns
            (chip_file, chip_meta_payload, ctx) or None to skip.

            Each thread borrows a `rasterio.io.DatasetReader` from
            ``_src_handles`` for the duration of one chip. The handle is
            returned (not closed) so the next task on the same thread
            reuses it. GDAL multi-threaded decoding is enabled via the
            env block set at module load.

            ``out_shape`` is set only for the full-scene pass: the source
            window (here the whole extent) is read DECIMATED into an
            ``(out_h, out_w)`` array via rasterio's COG overviews. The chip
            image is then smaller than its source window, so the returned
            ctx carries ``scale_x``/``scale_y`` = source-px / chip-px that the
            georef path multiplies in. Normal chips pass ``out_shape=None`` and
            get scale 1.0 — their georef is byte-for-byte unchanged.
            """
            src_t = _src_handles.get()
            try:
                win_width = min(chip_w, src_t.width - x)
                win_height = min(chip_h, src_t.height - y)
                window = Window(x, y, win_width, win_height)
                if out_shape is not None:
                    out_h, out_w = out_shape
                    scale_x = win_width / float(out_w)
                    scale_y = win_height / float(out_h)
                    read_out_shape = (src_t.count, out_h, out_w)
                else:
                    out_h, out_w = win_height, win_width
                    scale_x = scale_y = 1.0
                    read_out_shape = None
                with _chip_stage_timer("valid_mask"):
                    valid_mask = valid_data_mask(
                        src_t, window, out_shape=(out_h, out_w) if out_shape else None
                    )
                valid_fraction = (
                    float(np.count_nonzero(valid_mask)) / max(1, valid_mask.size)
                    if valid_mask is not None
                    else 1.0
                )
                if valid_fraction < INFERENCE_MIN_VALID_CHIP_FRACTION:
                    return None
                with _chip_stage_timer("read_probe"):
                    chip = src_t.read(window=window, out_shape=read_out_shape)
                if np.all(chip == 0) or (src_t.nodata is not None and np.all(chip == src_t.nodata)):
                    return None
                # Pad edge RGB chips to a fixed chip_size square so torch.compile's
                # shape-specialised SAM3 graph applies to every chip (not just the
                # full-size interior ones). out_w/out_h grow to chip_size (the new
                # normalization basis); scale stays 1.0; the padded region is marked
                # invalid in valid_mask so detections there are clipped. RGB only —
                # MSI/SAR keep their GeoTIFF band path untouched.
                if (
                    INFERENCE_PAD_CHIPS_TO_SIZE
                    and out_shape is None
                    and (inference_metadata.get("modality") or "rgb") == "rgb"
                    and (chip.shape[-2] < chip_h or chip.shape[-1] < chip_w)
                ):
                    pad_h = max(0, chip_h - chip.shape[-2])
                    pad_w = max(0, chip_w - chip.shape[-1])
                    chip = np.pad(chip, ((0, 0), (0, pad_h), (0, pad_w)))
                    if valid_mask is not None:
                        valid_mask = np.pad(valid_mask, ((0, pad_h), (0, pad_w)))
                    out_h, out_w = chip_h, chip_w
                with _chip_stage_timer("encode"):
                    chip_file, chip_meta = _emit_chip_payload(
                        window, src_t, valid_mask=valid_mask, chip=chip,
                        raw_rgb_enabled=_raw_rgb_enabled,
                        # Operator/pipeline modality is authoritative — the
                        # band heuristic misses single-band GRDs and rasters
                        # with stripped descriptions.
                        modality_hint=inference_metadata.get("modality"),
                    )
                payload_obj = json.dumps({
                    "pass_id": pass_id,
                    "window": [x, y, win_width, win_height],
                    "scale_pass": pass_index,
                    **inference_metadata,
                    **chip_meta,
                })
                ctx = {
                    "x": x, "y": y,
                    # win_width/win_height here are the CHIP image dims (decimated
                    # for the full-scene pass), matching the valid_mask the clip
                    # path indexes; scale_x/scale_y carry it back to source px.
                    "win_width": out_w, "win_height": out_h,
                    "scale_x": scale_x, "scale_y": scale_y,
                    "valid_mask": valid_mask,
                    "valid_fraction": round(valid_fraction, 4),
                    "scale_pass": pass_index,
                }
                return (chip_file, payload_obj, ctx)
            finally:
                _src_handles.put(src_t)

        # Rolling-window latency tracker for memory-aware concurrency back-off.
        # When p95 / p50 > 4.0 the inference service is saturated (one slow
        # chip drags the tail); halve the effective limit to slow new submits.
        # Restore when p95 / p50 < 1.5. Floored at INFERENCE_MIN_PENDING_CHIPS
        # so the back-off never starves the GPU-replica pool (which is the most
        # common false trigger — tile latency varies with content, not just
        # saturation), and capped at INFERENCE_MAX_PENDING_CHIPS on the way up.
        _chip_latencies_ms: deque[float] = deque(maxlen=20)
        _effective_pending_limit = INFERENCE_MAX_PENDING_CHIPS

        def _consume_one(fut: concurrent.futures.Future) -> None:
            nonlocal processed_windows, failed_windows, completed_chip_count
            nonlocal _effective_pending_limit
            ctx = pending.pop(fut)
            chip_started = ctx.get("started")
            try:
                response = fut.result()
            except Exception as exc:
                failed_windows += 1
                processed_windows += 1
                logger.warning(
                    "[WORKER] Inference failed for chip pass=%s x=%s y=%s: %s",
                    pass_id, ctx["x"], ctx["y"], exc,
                )
                _report_inference_progress()
                return
            if chip_started is not None:
                # `post_roundtrip` measures wall time from `executor.submit` to a
                # consumed result — covers HTTP transport + server decode +
                # server forward pass + JSON serialize-back. Recorded once per
                # successful future and elided when profiling is off.
                _chip_record(
                    "post_roundtrip", (time.perf_counter() - chip_started) * 1000.0
                )
            if not response:
                failed_windows += 1
                processed_windows += 1
                logger.warning(
                    "[WORKER] sam3 inference returned no response for chip pass=%s x=%s y=%s",
                    pass_id, ctx["x"], ctx["y"],
                )
                _report_inference_progress()
                return
            with _chip_stage_timer("apply_response"):
                chip_dets = _apply_chip_response(ctx, response)
            with _chip_stage_timer("dedupe"):
                kept = dedupe_idx.add(chip_dets)
            processed_windows += 1
            completed_chip_count += 1
            if streaming and not defer_streaming_store:
                if kept:
                    try:
                        on_chip_store(kept, completed_chip_count)
                    except Exception as exc:
                        logger.exception(
                            "[WORKER] on_chip_store callback failed for pass=%s chip=%s: %s",
                            pass_id, completed_chip_count, exc,
                        )
            else:
                all_kept.extend(kept)
            if chip_started is not None:
                _chip_latencies_ms.append((time.perf_counter() - chip_started) * 1000)
                if len(_chip_latencies_ms) == _chip_latencies_ms.maxlen:
                    sorted_lat = sorted(_chip_latencies_ms)
                    p50 = sorted_lat[len(sorted_lat) // 2]
                    p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
                    ceiling = INFERENCE_MAX_PENDING_CHIPS
                    new_limit = _effective_pending_limit
                    if p50 > 0 and p95 > 4.0 * p50 and _effective_pending_limit > INFERENCE_MIN_PENDING_CHIPS:
                        new_limit = max(INFERENCE_MIN_PENDING_CHIPS, _effective_pending_limit // 2)
                    elif p50 > 0 and p95 < 1.5 * p50 and _effective_pending_limit < ceiling:
                        new_limit = min(ceiling, _effective_pending_limit + 1)
                    if new_limit != _effective_pending_limit:
                        logger.info(
                            "[WORKER] chip pending limit %s -> %s (p50=%.0fms p95=%.0fms)",
                            _effective_pending_limit, new_limit, p50, p95,
                        )
                        _effective_pending_limit = new_limit
            _report_inference_progress()

        # Cap in-flight chips and spool oversized PNGs to disk so large rasters
        # cannot accumulate unbounded encoded chip buffers in memory.
        # The runtime back-off in _consume_one may lower this temporarily; we
        # read `_effective_pending_limit` rather than this constant in the
        # dispatch gate.
        pending_limit = INFERENCE_MAX_PENDING_CHIPS

        try:
            for pass_index, plan in enumerate(pass_plans):
                # Rebind per-pass closure variables. _apply_chip_response,
                # _report_inference_progress, _consume_one read `grid`,
                # `chip_size`, `step`, `coverage_fraction`, `total_windows`
                # by closure, so the rebind here takes effect for them too.
                chip_size = plan["chip_size"]
                step = plan["step"]
                grid = plan["grid"]
                # `coverage_fraction` is now an across-pass average; keep the
                # single rebound value for chip metadata. _apply_chip_response
                # records this on each detection.
                if pass_index == 0 and progress_callback:
                    if grid["sampled"]:
                        msg = f"Large raster detected; sampling {plan['planned_total']} of {grid['source_total']} chips for inference."
                    else:
                        msg = f"Prepared {total_windows} raster chips for inference."
                    progress_callback(
                        "inference", 56, msg,
                        {
                            "planned_chips": total_windows,
                            "total_chips": total_windows,
                            "source_total_chips": grid["source_total"],
                            "processed_chips": 0,
                            "failed_chips": 0,
                            "inference_speed_profile": INFERENCE_SPEED_PROFILE,
                            "max_inference_chips": grid["max_chips"],
                            "sampling_enabled": inference_summary["sampling_enabled"],
                            "coverage_fraction": coverage_fraction,
                            "multi_scale": inference_summary["multi_scale"],
                        },
                    )
                elif pass_index > 0 and plan["full_scene"]:
                    logger.info(
                        "[WORKER] Starting full-scene pass %s: whole image %sx%s decimated to %sx%s",
                        pass_index, width, height, plan["fs_out_w"], plan["fs_out_h"],
                    )
                elif pass_index > 0:
                    logger.info(
                        "[WORKER] Starting small-object pass %s: chip_size=%s overlap=%s planned_chips=%s",
                        pass_index, chip_size, plan["overlap"], plan["planned_total"],
                    )

                # Full-scene pass: a single (0,0,width,height) window read
                # decimated to (fs_out_h, fs_out_w). Threaded through the same
                # producer loop as one chip carrying an `out_shape`; everything
                # downstream (dedupe, store, drain) is shared with the grid
                # passes. Grid passes yield `out_shape=None` (1:1 read).
                if plan["full_scene"]:
                    chip_iter = iter([(0, 0, height, width, (plan["fs_out_h"], plan["fs_out_w"]))])
                else:
                    # Phase 2: prefer pre-snapped pixel offsets when the planner
                    # supplied them (block-aligned origins on tiled COGs).
                    # Legacy fallback recomputes `idx * step` for any plan dict
                    # that predates the offsets keys. Window sizes carry the
                    # snap-delta extension so block-snapped grids keep full
                    # coverage; legacy plans fall back to the uniform chip_size.
                    y_offsets_seq = grid.get("y_offsets") or [idx * step for idx in grid["y_indices"]]
                    x_offsets_seq = grid.get("x_offsets") or [idx * step for idx in grid["x_indices"]]
                    y_sizes_seq = grid.get("y_window_sizes") or [chip_size] * len(y_offsets_seq)
                    x_sizes_seq = grid.get("x_window_sizes") or [chip_size] * len(x_offsets_seq)

                    # Phase 3: parallel producer. Build an iterator of
                    # (y, x, win_h, win_w, out_shape) tuples and drive a unified
                    # wait loop over both `read_pending` (read+encode futures)
                    # and `pending` (POST futures). The combined in-flight bound
                    # is the same `_effective_pending_limit` used by the runtime
                    # back-off, which keeps memory predictable on huge rasters.
                    chip_iter = (
                        (y, x, win_h, win_w, None)
                        for y, win_h in zip(y_offsets_seq, y_sizes_seq)
                        for x, win_w in zip(x_offsets_seq, x_sizes_seq)
                    )
                read_pending: dict[concurrent.futures.Future, tuple[int, int]] = {}
                exhausted = False

                while not exhausted or read_pending or pending:
                    while (
                        not exhausted
                        and len(read_pending) + len(pending) < _effective_pending_limit
                    ):
                        try:
                            y, x, win_h, win_w, out_shape = next(chip_iter)
                        except StopIteration:
                            exhausted = True
                            break
                        rf = reader_executor.submit(
                            _reader_task, x, y, win_w, win_h, pass_index, out_shape,
                        )
                        read_pending[rf] = (x, y)

                    waitable = list(read_pending.keys()) + list(pending.keys())
                    if not waitable:
                        break
                    done, _ = concurrent.futures.wait(
                        waitable,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for fut in done:
                        if fut in read_pending:
                            xy = read_pending.pop(fut)
                            try:
                                result = fut.result()
                            except Exception as exc:
                                failed_windows += 1
                                processed_windows += 1
                                logger.warning(
                                    "[WORKER] chip read failed pass=%s x=%s y=%s: %s",
                                    pass_id, xy[0], xy[1], exc,
                                )
                                _report_inference_progress()
                                continue
                            if result is None:
                                # Skipped: low valid_fraction / all-zero / nodata.
                                # These don't count against `processed_chips`
                                # because no inference was issued, matching the
                                # pre-Phase-3 `continue` behaviour.
                                continue
                            chip_file_local, chip_meta_payload_local, ctx_local = result
                            chip_label = (
                                f"pass={pass_id} scale={pass_index} "
                                f"x={ctx_local['x']} y={ctx_local['y']}"
                            )
                            ctx_local["started"] = time.perf_counter()
                            with _chip_stage_timer("submit"):
                                # Phase 4: a numpy array carrier means the
                                # RGB chip skipped PIL/PNG and goes on the
                                # /detect_raw raw-binary path. Anything
                                # else is a file-like (SpooledTemporaryFile
                                # holding PNG or GeoTIFF bytes) and uses
                                # the legacy multipart /detect path.
                                if isinstance(chip_file_local, np.ndarray):
                                    pf = poster_executor.submit(
                                        _post_chip_to_sam3_raw,
                                        session, chip_file_local,
                                        chip_meta_payload_local, chip_label,
                                    )
                                else:
                                    pf = poster_executor.submit(
                                        _post_chip_to_sam3,
                                        session, chip_file_local,
                                        chip_meta_payload_local, chip_label,
                                    )
                            pending[pf] = ctx_local
                        else:
                            _consume_one(fut)

                # Drain any remaining POSTs before starting the next pass so
                # per-pass detections are stored before the smaller-scale
                # chips run against them.
                while pending:
                    done, _ = concurrent.futures.wait(
                        list(pending.keys()),
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for fut in done:
                        _consume_one(fut)
        finally:
            reader_executor.shutdown(wait=False, cancel_futures=True)
            poster_executor.shutdown(wait=False, cancel_futures=True)
            # Drain the rasterio handle pool deterministically — leaving
            # these open until GC would leak file descriptors on long
            # Celery worker lifetimes.
            try:
                while True:
                    handle = _src_handles.get_nowait()
                    try:
                        handle.close()
                    except Exception:
                        pass
            except queue.Empty:
                pass
            session.close()
    
    inference_summary["processed_chips"] = processed_windows
    inference_summary["failed_chips"] = failed_windows
    inference_summary["raw_detections"] = dedupe_idx.raw_seen
    inference_summary["deduped_detections"] = dedupe_idx.kept_count
    inference_summary["suppressed_detections"] = max(0, dedupe_idx.raw_seen - dedupe_idx.kept_count)
    # Phase 3.12: cross-chip edge reconciliation runs only on the non-streaming
    # path (where the full survivor list is in memory). Streaming mode pushes
    # each chip's survivors to ``on_chip_store`` immediately and cannot wait
    # for a hypothetical complementary detection from a future chip; a
    # follow-up will add a small buffer of edge_truncated survivors with a
    # bounded flush window for the streaming case.
    if not streaming and all_kept:
        reconciled, merge_count = dedupe_idx.reconcile_edge_truncated(all_kept)
        all_kept = reconciled
        inference_summary["edge_reconciled_pairs"] = merge_count
        inference_summary["deduped_detections"] = dedupe_idx.kept_count
        for det in all_kept:
            if _geo_stale_after_merge(det):
                _rederive_geo_from_pixel_bbox(det, transform, crs)
    elif defer_streaming_store:
        final_heads = dedupe_idx.heads()
        for det in final_heads:
            if _geo_stale_after_merge(det):
                _rederive_geo_from_pixel_bbox(det, transform, crs)
        if final_heads:
            on_chip_store(final_heads, completed_chip_count)
        all_kept = []
    # Worker-side per-chip stage breakdown (gated by CHIP_PREP_PROFILE=1). The
    # chip-prep stages (valid_mask, read_probe, encode[_png/_geotiff],
    # post_roundtrip = HTTP+server inference, apply_response = georef, dedupe) are
    # already timed via _chip_stage_timer; surface the aggregate here so a normal
    # ingest logs the full per-component breakdown without writing to bench/.
    if _chip_profile_enabled():
        prof_stages: dict[str, dict] = {}
        for stage, samples in _chip_snapshot().items():
            if not samples:
                continue
            ordered = sorted(samples)
            n = len(ordered)
            prof_stages[stage] = {
                "n": n,
                "total_ms": round(sum(ordered), 1),
                "mean_ms": round(sum(ordered) / n, 2),
                "p50_ms": round(ordered[n // 2], 2),
                "p95_ms": round(ordered[min(n - 1, int(n * 0.95))], 2),
            }
        logger.info("[WORKER] chip_prep_profile pass=%s stages=%s", pass_id, prof_stages)
    return {"detections": all_kept, "summary": inference_summary}


def run_sar_cfar_for_pass(
    cog_path: str,
    pass_id: int,
    *,
    threshold_sigma: float = 2.5,
    guard_px: int = 4,
    background_px: int = 20,
    min_pixels: int = 4,
    on_chip_store=None,
) -> dict:
    """Phase 5.20b: run the SAR CFAR detector across a Sentinel-1 (or
    similar) GRD COG and ingest the resulting ship detections.

    Companion to Phase 5.20's "skip SAM3 on SAR by default" gate — once SAM3
    is muted, this is what produces detections for SAR rasters. The
    detector lives in :mod:`backend.sar_cfar` and runs entirely on the CPU
    worker; no GPU / inference-service round trip needed.

    Reuses :func:`plan_inference_grid` for chip planning + the same
    pixel→geo transform that ``slice_and_infer`` uses, so the resulting
    detections share the exact same provenance shape as the SAM3 path
    (chip_id, chip_window, pixel_bbox, geo_bbox, geo_polygon, sampling_*
    metadata, …). Stored via ``on_chip_store`` when streaming, or
    accumulated and stored at the end otherwise.

    Args follow ``detect_ships_cfar`` plus an ``on_chip_store`` callback
    that matches ``slice_and_infer``'s contract: ``(survivor_dets, chip_index) -> None``.
    Returns the same shape as ``slice_and_infer``::

        {"detections": [..], "summary": {..}}
    """
    from sar_cfar import detect_ships_cfar  # local import: keep worker startup cheap

    streaming = on_chip_store is not None
    summary: dict = {
        "method": "sar_cfar",
        "modality": "sar",
        "threshold_sigma": threshold_sigma,
        "guard_px": guard_px,
        "background_px": background_px,
        "min_pixels": min_pixels,
    }
    all_kept: list[dict] = []
    dedupe_idx = _DetectionDedupeIndex(
        iou_threshold=float(os.getenv("SAR_NMS_IOU_DEFAULT", "0.25"))
    )

    with rasterio.open(cog_path) as src:
        width = src.width
        height = src.height
        transform = src.transform
        crs = src.crs

        # Pick VV (band 1) + optional VH (band 2) — Sentinel-1 IW GRD is
        # always (VV, VH) in that order. Other 2-band SAR formats follow
        # the same convention. Single-band rasters fall back to VV-only.
        #
        # Bands are read per chip window below — a full-band read is ~1.7 GB
        # for an S1 GRD float32, which defeated the chip grid's purpose of
        # bounding memory. The dB-vs-linear decision is global per band, made
        # once from a downsampled overview read: if the values span > 50
        # they're already in dB; otherwise treat as linear amplitude.
        has_vh = src.count >= 2

        def _band_is_db(band: int) -> bool:
            try:
                ov = src.read(
                    band,
                    out_shape=(max(1, min(1024, height)), max(1, min(1024, width))),
                ).astype(np.float32)
            except Exception as exc:
                logger.warning("[CFAR] overview probe failed for band %s: %s", band, exc)
                return False
            if ov.size == 0:
                return True
            span = float(ov.max() - ov.min())
            return span > 50.0 or float(ov.min()) < -1.0

        vv_is_db = _band_is_db(1)
        vh_is_db = _band_is_db(2) if has_vh else False

        def _to_db(arr: np.ndarray, is_db: bool) -> np.ndarray:
            if arr.size == 0 or is_db:
                return arr
            with np.errstate(divide="ignore", invalid="ignore"):
                return 10.0 * np.log10(np.maximum(arr, 1e-6))

        # Plan a coarse chip grid so very large COGs stay bounded. CFAR is
        # cheap so the chip size can be much larger than the SAM3 inference
        # chip — 4096 px gives plenty of context for the background window.
        chip_size = int(os.getenv("SAR_CFAR_CHIP_SIZE", "4096"))
        overlap = int(os.getenv("SAR_CFAR_OVERLAP", "256"))
        grid = plan_inference_grid(width, height, chip_size, overlap, max_chips=0)
        step = grid["step"]
        summary["planned_chips"] = grid["planned_total"]
        summary["source_total_chips"] = grid["source_total"]
        summary["sampling_enabled"] = grid["sampled"]
        coverage_fraction = round(grid["planned_total"] / max(1, grid["source_total"]), 4)

        chip_index = 0
        for y_idx in grid["y_indices"]:
            y = y_idx * step
            for x_idx in grid["x_indices"]:
                x = x_idx * step
                win_w = min(chip_size, width - x)
                win_h = min(chip_size, height - y)
                if win_w <= 2 * background_px + 1 or win_h <= 2 * background_px + 1:
                    continue  # too small for the CFAR window
                window = Window(x, y, win_w, win_h)
                try:
                    tile_vv = _to_db(src.read(1, window=window).astype(np.float32), vv_is_db)
                except Exception as exc:
                    logger.warning("[CFAR] failed to read band 1 window x=%s y=%s: %s", x, y, exc)
                    continue
                tile_vh = None
                if has_vh:
                    try:
                        tile_vh = _to_db(src.read(2, window=window).astype(np.float32), vh_is_db)
                    except Exception as exc:
                        logger.warning("[CFAR] failed to read band 2 window x=%s y=%s: %s", x, y, exc)
                try:
                    cfar_dets = detect_ships_cfar(
                        tile_vv, tile_vh,
                        threshold_sigma=threshold_sigma,
                        guard_px=guard_px,
                        background_px=background_px,
                        min_pixels=min_pixels,
                    )
                except Exception as exc:
                    logger.warning("[CFAR] chip x=%s y=%s failed: %s", x, y, exc)
                    cfar_dets = []
                if not cfar_dets:
                    continue
                chip_index += 1

                survivors: list[dict] = []
                for det in cfar_dets:
                    # CFAR pixel_bbox is in tile-local coords; lift to COG-global.
                    lx1, ly1, lx2, ly2 = det["pixel_bbox"]
                    abs_px = [
                        float(x + lx1), float(y + ly1),
                        float(x + lx2), float(y + ly2),
                    ]
                    pixel_obb = [
                        abs_px[0], abs_px[1],
                        abs_px[2], abs_px[1],
                        abs_px[2], abs_px[3],
                        abs_px[0], abs_px[3],
                    ]
                    # Pixel → geo via the COG transform; reproject to WGS84
                    # when CRS isn't already lat/lon, matching slice_and_infer.
                    pts = [
                        (pixel_obb[i], pixel_obb[i + 1])
                        for i in range(0, 8, 2)
                    ]
                    lons, lats = [], []
                    for px, py in pts:
                        lon_v, lat_v = transform * (px, py)
                        lons.append(lon_v)
                        lats.append(lat_v)
                    if crs and crs.to_string() != "EPSG:4326":
                        from rasterio.warp import transform as rasterio_transform
                        lons, lats = rasterio_transform(crs, "EPSG:4326", lons, lats)
                    geo_polygon = [c for pt in zip(lons, lats) for c in pt]
                    lon1, lat1, lon2, lat2 = min(lons), min(lats), max(lons), max(lats)

                    det.update({
                        "pixel_bbox": abs_px,
                        "pixel_obb": pixel_obb,
                        "geo_bbox": [lon1, lat1, lon2, lat2],
                        "geo_polygon": geo_polygon,
                        "chip_id": f"{pass_id}:{x}:{y}:{win_w}:{win_h}:cfar",
                        "chip_window": [x, y, win_w, win_h],
                        "coverage_fraction": coverage_fraction,
                        "planned_chips": grid["planned_total"],
                        "source_total_chips": grid["source_total"],
                        "sampling_enabled": grid["sampled"],
                        "dedupe_method": "sar_cfar",
                        "source_layer": "sar_cfar",
                        "modality": "sar",
                        "scale_pass": 0,
                    })
                    try:
                        px_w = abs(float(transform.a))
                        px_h = abs(float(transform.e))
                        if crs and crs.is_geographic:
                            mid_lat_rad = math.radians((lat1 + lat2) / 2.0)
                            pixel_width_m = px_w * 111_320.0 * max(math.cos(mid_lat_rad), 0.01)
                            pixel_height_m = px_h * 111_320.0
                        else:
                            pixel_width_m = px_w
                            pixel_height_m = px_h
                        bbox_area_px = int(max(0.0, abs_px[2] - abs_px[0]) * max(0.0, abs_px[3] - abs_px[1]))
                        size = estimate_size(
                            geo_polygon=geo_polygon,
                            crs="EPSG:4326",
                            pixel_width_m=pixel_width_m,
                            pixel_height_m=pixel_height_m,
                            mask_area_px=bbox_area_px,
                        )
                        if size is not None:
                            det["size_estimate"] = size
                    except Exception:
                        pass
                    survivors.append(det)

                survivors = dedupe_idx.add(survivors)
                if streaming and survivors:
                    try:
                        on_chip_store(survivors, chip_index)
                    except Exception as exc:
                        logger.exception("[CFAR] on_chip_store failed: %s", exc)
                elif survivors:
                    all_kept.extend(survivors)

        summary["processed_chips"] = chip_index
        summary["coverage_fraction"] = coverage_fraction
        summary["raw_detections"] = dedupe_idx.raw_seen
        summary["deduped_detections"] = dedupe_idx.kept_count
        summary["suppressed_detections"] = max(0, dedupe_idx.raw_seen - dedupe_idx.kept_count)
    return {"detections": all_kept, "summary": summary}


def _aoi_default_allegiance_at(cursor, lon: float, lat: float) -> str:
    """Phase 6.26: return the ``default_allegiance`` of the AOI containing
    ``(lon, lat)`` — first match wins by smallest area (so nested AOIs work).
    Falls back to ``"unknown"`` when no AOI matches or the column is missing
    on an old install.

    Runs under a SAVEPOINT because the caller passes the live
    store_detections batch cursor: without it, a SQL error (e.g. the missing
    column on an old install) aborts the surrounding transaction and every
    subsequent detection INSERT in the batch fails with
    InFailedSqlTransaction while ingest still reports success.
    """
    try:
        cursor.execute("SAVEPOINT aoi_allegiance")
        try:
            cursor.execute(
                "SELECT default_allegiance FROM aois "
                "WHERE geom IS NOT NULL AND ST_Intersects(geom, ST_SetSRID(ST_Point(%s, %s), 4326)) "
                "ORDER BY ST_Area(geom) ASC LIMIT 1",
                (lon, lat),
            )
            row = cursor.fetchone()
            cursor.execute("RELEASE SAVEPOINT aoi_allegiance")
        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT aoi_allegiance")
            raise
    except Exception:
        return "unknown"
    if not row:
        return "unknown"
    raw = row[0] if not isinstance(row, dict) else row.get("default_allegiance")
    value = (str(raw or "unknown")).strip().lower()
    return value if value in {"friendly", "hostile", "neutral", "unknown"} else "unknown"


def store_detections(detections: list, pass_id: int, ontology_by_class: dict[str, dict] = None):
    """Store detections in PostGIS and create Neo4j nodes."""
    if not detections:
        return 0

    # Step 3 of /home/avinash/.claude/plans/the-inference-system-has-piped-nest.md:
    # Each detection now carries the new defence-ontology classification
    # (branch_id / icon_key / canonical_label / was_unknown / ontology_object_id)
    # in its metadata JSON. The `class` column itself is FROZEN as the raw
    # lowercase_underscore label from inference and the existing
    # `parent_class` field stays for backward compat (Step 7 makes
    # parent_class_for_label() a wrapper). The authoritative classification
    # going forward is `branch_id`. See backend/ontology.py::normalize().
    unknown_count = 0
    total_normalized = 0
    # Per-batch policy fetch (not the old import-time global) so admin
    # confidence-override changes reach the long-lived worker.
    batch_policy = active_detection_policy()
    # Per-batch memo for the AOI-allegiance lookup — detections in a chip
    # batch cluster spatially, so rounding the centroid to ~11 m collapses
    # most of the one-SELECT-per-detection cost.
    allegiance_cache: dict[tuple[float, float], str] = {}
    with postgis_db.get_cursor(commit=True) as cursor, db.get_session() as neo_session:
        for det in detections:
            lon1, lat1, lon2, lat2 = det["geo_bbox"]
            confidence = det.get("confidence", 0.0)
            det_class = det.get("class", "Unknown")
            original_class = det.get("original_class") or det_class
            parent_class = det.get("parent_class") or parent_class_for_label(original_class)
            decision = detection_decision(original_class, confidence, batch_policy)
            # Defence-ontology normalization (Step 3). Falls back gracefully
            # when source_layer is missing — empty string is the documented default.
            ont = ontology_normalize(original_class, layer=det.get("source_layer", ""))
            total_normalized += 1
            if ont.was_unknown:
                unknown_count += 1
            det["ontology_unknown"] = bool(ont.was_unknown)
            apply_evidence_ranking(det, ontology_unknown=ont.was_unknown)
            # Computed after apply_evidence_ranking so verifier mutations (semantic_margin) are visible. See decisions/why-generic-labels-when-unverified.md.
            display_label, label_quality = display_label_for(
                {**det, "original_class": original_class, "parent_class": parent_class},
                ont,
            )
            ontology = (ontology_by_class or {}).get(det_class) or detection_ontology(det_class)
            # Phase 6.26: per-AOI default allegiance. When the detection's
            # centroid falls inside an AOI with a non-"unknown" default, use
            # that as the starting allegiance instead of the global "unknown".
            # An explicit per-detection allegiance (set upstream by the
            # operator or another worker stage) still wins.
            allegiance = det.get("allegiance")
            if not allegiance:
                cache_key = (round((lon1 + lon2) / 2.0, 4), round((lat1 + lat2) / 2.0, 4))
                allegiance = allegiance_cache.get(cache_key)
                if allegiance is None:
                    allegiance = _aoi_default_allegiance_at(cursor, cache_key[0], cache_key[1])
                    allegiance_cache[cache_key] = allegiance
            assessment = assess_detection_threat(det_class, confidence=confidence, allegiance=allegiance)
            ontology = {
                **ontology,
                "original_class": original_class,
                "parent_class": parent_class,
                "threat_level": assessment["threat_level"],
                "threat_confidence": assessment["threat_confidence"],
                "assessment_status": assessment["assessment_status"],
                "evidence": assessment["evidence"],
                "category": assessment["category"],
            }
            pixel_bbox = det.get("pixel_bbox", [])
            geo_polygon = det.get("geo_polygon") or [lon1, lat1, lon1, lat2, lon2, lat2, lon2, lat1]
            
            # Create WKT polygons
            pairs = list(zip(geo_polygon[0::2], geo_polygon[1::2]))
            if pairs[0] != pairs[-1]:
                pairs.append(pairs[0])
            geom_wkt = "POLYGON((" + ", ".join(f"{lon} {lat}" for lon, lat in pairs) + "))"
            centroid_wkt = f"POINT({sum(lon for lon, _lat in pairs[:-1]) / max(1, len(pairs) - 1)} {sum(lat for _lon, lat in pairs[:-1]) / max(1, len(pairs) - 1)})"
            
            cursor.execute("""
                INSERT INTO detections (pass_id, class, confidence, geom, centroid, pixel_bbox, metadata)
                VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s, %s)
                RETURNING id
            """, (
                pass_id,
                det_class,
                confidence,
                geom_wkt,
                centroid_wkt,
                json.dumps({"bbox": pixel_bbox, "obb": det.get("pixel_obb", [])}),
                json.dumps({
                    "source": "inference",
                    "chip_size": det.get("chip_window", [None, None, None, None])[2] or DEFAULT_INFERENCE_CHIP_SIZE,
                    "geo_polygon": geo_polygon,
                    "confidence": confidence,
                    "calibrated_confidence": det.get("calibrated_confidence", confidence),
                    # Phase 2.5: keep the pre-calibration score visible for
                    # audit. ``calibrated_confidence`` is what NMS and the
                    # threshold gate use; the analyst sees both in provenance.
                    "raw_confidence": det.get("raw_confidence"),
                    "model_temperature": det.get("model_temperature"),
                    "original_class": original_class,
                    "parent_class": parent_class,
                    # Step 3: defence-ontology fields. Step 5 surfaces these
                    # through the API; nothing reads them yet so this is
                    # backwards-compatible. branch_id is the authoritative
                    # classification going forward.
                    "branch_id": ont.branch_id,
                    "icon_key": ont.icon_key,
                    "canonical_label": ont.canonical_label,
                    "was_unknown": ont.was_unknown,
                    "ontology_object_id": ont.ontology_object_id,
                    # Advisory fields; the canonical label remains for audit and analyst-driven promotion.
                    "display_label": display_label,
                    "label_quality": label_quality,
                    "review_status": det.get("review_status") or decision["review_status"],
                    "policy_review_status": det.get("policy_review_status") or decision["review_status"],
                    "threshold_profile": det.get("threshold_profile") or batch_policy["threshold_profile"],
                    "class_threshold": det.get("class_threshold") or decision["class_threshold"],
                    "model_version": det.get("model_version") or batch_policy["model_version"],
                    "taxonomy_version": det.get("taxonomy_version") or batch_policy["taxonomy_version"],
                    "chip_id": det.get("chip_id"),
                    "chip_window": det.get("chip_window"),
                    "chip_valid_fraction": det.get("chip_valid_fraction"),
                    "coverage_fraction": det.get("coverage_fraction"),
                    "planned_chips": det.get("planned_chips"),
                    "source_total_chips": det.get("source_total_chips"),
                    "sampling_enabled": det.get("sampling_enabled"),
                    "dedupe_method": det.get("dedupe_method", "obb_nms"),
                    "source_layer": det.get("source_layer"),
                    "wbf_member_count": det.get("wbf_member_count"),
                    "wbf_member_sources": det.get("wbf_member_sources"),
                    "member_sources": det.get("member_sources"),
                    "evidence_score": det.get("evidence_score"),
                    "evidence_tier": det.get("evidence_tier"),
                    "semantic_margin": det.get("semantic_margin"),
                    "semantic_verifier": det.get("semantic_verifier"),
                    "validator_results": det.get("validator_results"),
                    "reject_reasons": det.get("reject_reasons"),
                    # Phase 7.35: surface the per-detection position uncertainty
                    # (in metres) so the UI can render an uncertainty halo.
                    "position_uncertainty_m": det.get("position_uncertainty_m"),
                    "position_uncertainty_ellipse": det.get("position_uncertainty_ellipse"),
                    "size_estimate": det.get("size_estimate"),
                    "scale_pass": det.get("scale_pass"),
                    "ontology": ontology,
                    "threat_level": assessment["threat_level"],
                    "threat_confidence": assessment["threat_confidence"],
                    "assessment_status": assessment["assessment_status"],
                    "evidence": assessment["evidence"],
                    "allegiance": allegiance,
                    "prompt_profile": det.get("prompt_profile"),
                    "prompt_chunk_index": det.get("prompt_chunk_index"),
                    "prompt_total_chunks": det.get("prompt_total_chunks"),
                    "prompt_text": det.get("prompt_text"),
                    "mask_rle": det.get("mask_rle"),
                    "obb": det.get("obb"),
                    "pixel_obb": det.get("pixel_obb"),
                    "obb_format": det.get("obb_format"),
                    "obb_source": det.get("obb_source"),
                    "obb_angle_deg": det.get("obb_angle_deg"),
                    "obb_area_px": det.get("obb_area_px"),
                    "edge_truncated": det.get("edge_truncated"),
                    "embedding": det.get("embedding"),
                    "sar_proxy": det.get("sar_proxy"),
                    "terramind_embedding": det.get("terramind_embedding"),
                    "modality": det.get("modality"),
                    "task": det.get("task"),
                    "geo": det.get("geo"),
                    "area": det.get("area"),
                    "model_versions": det.get("model_versions"),
                })
            ))
            
            det_id = cursor.fetchone()["id"]
            # Back-fill the stored id + pass onto the in-memory dict so callers
            # (e.g. the live-streaming _store_chip callback) can reference the
            # persisted row without a re-query.
            det["id"] = det_id
            det["pass_id"] = pass_id

            # Plan C: attach reference-DB platform identification candidates and
            # (when top-1 score >= threshold) auto-apply platform_* to
            # object_details. Best-effort: any exception is logged and skipped --
            # must NEVER break the detection write.
            # Uses a SAVEPOINT so a psycopg2.Error inside the helper rolls back
            # only the auto-identify sub-work; the parent transaction (this
            # detection's INSERT + subsequent ones) stays usable. Without the
            # SAVEPOINT, a single pgvector or DDL fault would poison the
            # connection's transaction state and lose the entire batch with
            # InFailedSqlTransaction.
            emb_dict = det.get("embedding")
            if emb_dict:
                try:
                    emb_anchor = _parse_embedding_anchor(emb_dict)
                    if emb_anchor is not None:
                        cursor.execute("SAVEPOINT auto_identify")
                        try:
                            attach_identification_candidates(
                                cursor,
                                detection_id=det_id,
                                embedding=emb_anchor,
                                view_domain="overhead",
                                auto_threshold=REFERENCE_ID_AUTO_THRESHOLD,
                                top_k=3,
                            )
                            cursor.execute("RELEASE SAVEPOINT auto_identify")
                        except Exception:
                            cursor.execute("ROLLBACK TO SAVEPOINT auto_identify")
                            raise
                except Exception:
                    logger.warning(
                        "auto-identify failed for detection %s (continuing)",
                        det_id,
                        exc_info=True,
                    )

            # Neo4j is a non-authoritative mirror — a graph blip must NOT roll
            # back the committed PostGIS detections (the cursor's commit fires on
            # clean exit of this `with`). Best-effort, matching every other graph
            # write in the codebase.
            try:
                neo_session.run("""
                    MATCH (sp:SatellitePass {postgis_id: $pass_id})
                    CREATE (d:Detection {
                        postgis_id: $det_id,
                        class: $det_class,
                        label: $label,
                        original_class: $original_class,
                        parent_class: $parent_class,
                        confidence: $confidence,
                        review_status: $review_status,
                        threshold_profile: $threshold_profile,
                        model_version: $model_version,
                        taxonomy_version: $taxonomy_version,
                        threat_level: $threat_level,
                        threat_confidence: $threat_confidence,
                        assessment_status: $assessment_status,
                        ontology_category: $ontology_category,
                        allegiance: $allegiance,
                        latitude: $lat,
                        longitude: $lon,
                        created_at: datetime()
                    })
                    CREATE (sp)-[:CONTAINS_DETECTION]->(d)
                """, {
                    "pass_id": pass_id,
                    "det_id": det_id,
                    "det_class": det_class,
                    "label": ontology["label"],
                    "original_class": original_class,
                    "parent_class": parent_class,
                    "confidence": confidence,
                    "review_status": det.get("review_status") or decision["review_status"],
                    "threshold_profile": det.get("threshold_profile") or batch_policy["threshold_profile"],
                    "model_version": det.get("model_version") or batch_policy["model_version"],
                    "taxonomy_version": det.get("taxonomy_version") or batch_policy["taxonomy_version"],
                    "threat_level": assessment["threat_level"],
                    "threat_confidence": assessment["threat_confidence"],
                    "assessment_status": assessment["assessment_status"],
                    "ontology_category": ontology["category"],
                    "allegiance": allegiance,
                    "lat": (lat1 + lat2) / 2,
                    "lon": (lon1 + lon2) / 2
                })
            except Exception:  # noqa: BLE001 — graph mirror is best-effort
                logger.warning(
                    "store_detections: Neo4j projection failed for detection %s (PostGIS kept)",
                    det_id,
                    exc_info=True,
                )

    # Step 3: per-batch summary of how many labels could not be resolved by
    # the defence-ontology normalizer (branch_id == "Other" / icon == circle_help).
    if total_normalized:
        logger.info(
            "ontology.normalize: pass_id=%s normalized=%d unknown=%d",
            pass_id,
            total_normalized,
            unknown_count,
        )

    return len(detections)


def clear_existing_detections(pass_id: int) -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT id FROM detections WHERE pass_id = %s", (pass_id,))
        det_ids = [row["id"] for row in cursor.fetchall()]
        # Capture track membership before the cascade removes the member rows.
        track_ids = affected_track_ids(cursor, det_ids)
        cursor.execute("DELETE FROM detections WHERE pass_id = %s", (pass_id,))
        # Purge the rows no FK reaches: analyst object_details + emptied tracks.
        purge_object_details(cursor, "detection", det_ids)
        purge_empty_tracks(cursor, track_ids)

    if det_ids:
        with db.get_session() as neo_session:
            neo_session.run("""
                MATCH (d:Detection)
                WHERE d.postgis_id IN $det_ids
                DETACH DELETE d
            """, {"det_ids": det_ids})


def _target_history_anchor(cursor, target_id: str) -> float:
    cursor.execute(
        "SELECT count(*) AS c FROM detection_target_candidates "
        "WHERE target_id = %s AND status IN ('accepted', 'confirmed')",
        (target_id,),
    )
    row = cursor.fetchone()
    if not row:
        return 0.0
    accepted = int(row["c"] if isinstance(row, dict) else row[0])
    return min(1.0, accepted / 5.0)


def generate_candidate_links_for_pass(
    pass_id: int,
    distance_threshold_meters: float = 1500.0,
    max_candidates_per_detection: int = 5,
) -> int:
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT d.id, d.class, d.confidence, ST_X(d.centroid) AS lon, ST_Y(d.centroid) AS lat
            FROM detections d
            WHERE d.pass_id = %s
        """, (pass_id,))
        rows = cursor.fetchall()

    try:
        with db.get_session() as session:
            result = session.run("""
                MATCH (t)
                WHERE 'Target' IN labels(t)
                WITH t, properties(t) AS props
                WHERE props.latitude IS NOT NULL
                  AND props.longitude IS NOT NULL
                RETURN elementId(t) AS element_id, props.id AS stable_id, props.name AS name,
                       props.latitude AS lat, props.longitude AS lon, props
            """)
            targets = [dict(record) for record in result]
    except Exception as exc:
        logger.warning("Unable to read targets for candidate links: %s", exc)
        targets = []

    # No targets → no candidates possible; skip the per-detection loop entirely.
    if not targets:
        return 0

    created = 0
    graph_edges: list[dict] = []
    with postgis_db.get_cursor(commit=True) as cursor:
        # history_anchor depends only on target_id, so resolve it once per target
        # instead of re-querying it inside the per-detection ranking loop (a
        # dense pass is thousands of detections × every target).
        history_by_target = {
            tid: _target_history_anchor(cursor, tid)
            for tid in {
                str(t.get("stable_id") or t.get("element_id") or "")
                for t in targets
            }
            if tid
        }
        for det in rows:
            ranked = rank_candidate_links(
                dict(det),
                targets,
                max_distance_m=distance_threshold_meters,
                max_candidates_per_detection=max_candidates_per_detection,
                history_lookup=lambda target_id: history_by_target.get(target_id, 0.0),
            )
            for item in ranked:
                target_id = item["target_id"]
                evidence = {
                    "distance_m": round(item["distance_m"], 2),
                    "compatibility_reason": item["compatibility_reason"],
                    "compatibility_score": round(item["compatibility_score"], 3),
                    "history_anchor": round(item["history_anchor"], 3),
                    "score_weights": item["score_weights"],
                    "detection_class": det["class"],
                    "detection_confidence": item["detection_confidence"],
                }
                cursor.execute("""
                    INSERT INTO detection_target_candidates (detection_id, target_id, target_name, score, reason, status, evidence)
                    VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                    ON CONFLICT (detection_id, target_id) DO UPDATE SET
                        target_name = EXCLUDED.target_name,
                        score = EXCLUDED.score,
                        reason = EXCLUDED.reason,
                        evidence = EXCLUDED.evidence,
                        updated_at = NOW()
                    RETURNING id
                """, (
                    det["id"],
                    target_id,
                    item["target_name"],
                    item["score"],
                    item["reason"],
                    json.dumps(evidence, default=str),
                ))
                row = cursor.fetchone()
                if row:
                    created += 1
                    # Mirror the edge into Neo4j after the PostGIS commit, in one
                    # batched UNWIND rather than a session per candidate.
                    graph_edges.append({
                        "det_id": det["id"],
                        "det_class": det.get("class"),
                        "confidence": det.get("confidence"),
                        "lat": det.get("lat"),
                        "lon": det.get("lon"),
                        "target_id": target_id,
                        "candidate_id": int(row["id"] if isinstance(row, dict) else row[0]),
                        "score": item["score"],
                        "reason": item["reason"],
                    })

    # PostGIS rows are committed; mirror all candidate edges into Neo4j in
    # chunked UNWIND batches (the graph is a non-authoritative mirror).
    if graph_edges:
        from graph_writes import merge_candidate_detected_as_batch
        for start in range(0, len(graph_edges), 1000):
            merge_candidate_detected_as_batch(graph_edges[start:start + 1000])
    return created


def detection_class_summary(pass_id: int) -> list[dict]:
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT class,
                   COUNT(*)::int AS count,
                   COALESCE(AVG(confidence), 0)::float AS avg_confidence
            FROM detections
            WHERE pass_id = %s
            GROUP BY class
            ORDER BY COUNT(*) DESC, class ASC
        """, (pass_id,))
        return [dict(row) for row in cursor.fetchall()]


@celery_app.task(name="worker.process_satellite_imagery", queue="imagery", bind=True)
def process_satellite_imagery(
    self,
    image_url: str,
    sensor_type: str = "Optical",
    acquisition_time: str = None,
    upload_id: str = None,
    enabled_layers: Optional[list[str]] = None,
):
    """
    Full pipeline: download/validate -> COG conversion -> catalog -> inference -> store.

    enabled_layers: optional list of inference layer names to forward to
        /detect (e.g. ["sam3", "dota_obb", "dinov3_sat"]).
        When None the inference service runs all loaded layers.
    """
    try:
        logger.info("[WORKER] Processing satellite image: %s", image_url)
        publish_event("imagery", {"type": "ingest_started", "image_url": image_url, "upload_id": upload_id})
        publish_event("ops", {"type": "imagery_ingest_started", "image_url": image_url, "upload_id": upload_id})

        # 1. Determine local path
        ensure_worker_imagery_schema()
        input_path = resolve_input_path(image_url)
        filename = os.path.basename(input_path)
        upload_job = get_upload_job(upload_id)
        original_filename = upload_job.get("filename") or filename
        upload_meta = upload_job.get("metadata") or {}
        if isinstance(upload_meta, str):
            try:
                upload_meta = json.loads(upload_meta)
            except (TypeError, ValueError):
                upload_meta = {}
        try:
            provider_lifecycle.ensure_running()
        except Exception as exc:
            logger.warning("[WORKER] provider_lifecycle.ensure_running failed: %s", exc)
        report_progress(self, upload_id, input_path, "metadata", 8, "Reading raster metadata and computing file hash.")
        raster_metadata = extract_raster_metadata(input_path)
        source_hash = raster_metadata.get("source_hash")
        source_filename = original_filename
        ingest_mode_metadata = {
            key: upload_meta.get(key)
            for key in ("model", "prompt_mode", "enabled_layers")
            if upload_meta.get(key) is not None
        }
        update_upload_job(
            upload_id=upload_id,
            file_path=input_path,
            status="processing",
            metadata={"task_id": self.request.id, "raster_metadata": raster_metadata, "source_hash": source_hash},
        )
        report_progress(self, upload_id, input_path, "processing", 10, "Resolved imagery input and metadata.")

        # 2. Convert to COG
        cog_name = f"{os.path.splitext(filename)[0]}_cog.tif"
        cog_path = os.path.join(IMAGERY_PATH, "processed", cog_name)
        os.makedirs(os.path.dirname(cog_path), exist_ok=True)

        report_progress(self, upload_id, input_path, "conversion", 20, "Converting raster to Cloud Optimized GeoTIFF.")
        ensure_cog(input_path, cog_path)
        logger.info("[WORKER] COG created: %s", cog_path)
        report_progress(self, upload_id, input_path, "conversion", 35, "COG conversion complete.", {"cog_path": cog_path})

        # 3. Extract footprint and catalog in PostGIS
        report_progress(self, upload_id, input_path, "cataloging", 45, "Extracting footprint and cataloging imagery.")
        footprint, min_lon, min_lat, max_lon, max_lat = get_raster_footprint(cog_path)
        footprint_wkt = footprint.wkt

        acq_time = acquisition_time or raster_metadata.get("acquisition_time") or datetime.now(timezone.utc).isoformat()

        with postgis_db.get_cursor(commit=True) as cursor:
            # Dedup is content-identity ONLY. A byte-identical re-upload shares
            # its SHA-256 (source_hash) and must collapse onto the existing pass
            # so re-processing the same raster stays idempotent (see
            # docs/backend/imagery-metadata-hashing.md). The previous query also
            # matched on acquisition_time + footprint + source_filename/name,
            # which collapsed *distinct* uploads that merely shared a timestamp
            # and footprint (e.g. two scenes from one satellite pass, or two
            # crops of a mosaic) into a single row — silently dropping the second
            # image. A SHA-256 match means the files are identical, so neither
            # acquisition_time nor footprint is needed to disambiguate. See
            # docs/decisions/why-imagery-dedup-is-hash-only.md.
            cursor.execute("""
                SELECT id
                FROM satellite_passes
                WHERE %s IS NOT NULL AND source_hash = %s
                ORDER BY updated_at DESC NULLS LAST, created_at DESC
                LIMIT 1
            """, (source_hash, source_hash))
            existing = cursor.fetchone()
            if existing:
                pass_id = existing["id"]
                cursor.execute("""
                    UPDATE satellite_passes
                    SET name = %s,
                        file_path = %s,
                        sensor_type = %s,
                        acquisition_time = %s,
                        footprint = ST_GeomFromText(%s, 4326),
                        crs = %s,
                        metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb,
                        source_hash = %s,
                        source_filename = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                """, (
                    original_filename,
                    cog_path,
                    sensor_type,
                    acq_time,
                    footprint_wkt,
                    "EPSG:4326",
                    json.dumps({**raster_metadata, **ingest_mode_metadata, "upload_id": upload_id, "replacement": True}, default=str),
                    source_hash,
                    source_filename,
                    pass_id,
                ))
                cursor.fetchone()
                replacement = True
            else:
                cursor.execute("""
                    INSERT INTO satellite_passes (
                        name, file_path, sensor_type, acquisition_time, footprint, crs,
                        metadata, source_hash, source_filename, updated_at
                    )
                    VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s, %s, %s, NOW())
                    RETURNING id
                """, (
                    original_filename,
                    cog_path,
                    sensor_type,
                    acq_time,
                    footprint_wkt,
                    "EPSG:4326",
                    json.dumps({**raster_metadata, **ingest_mode_metadata, "upload_id": upload_id, "replacement": False}, default=str),
                    source_hash,
                    source_filename,
                ))
                pass_id = cursor.fetchone()["id"]
                replacement = False

        logger.info("[WORKER] Cataloged in PostGIS with id=%s replacement=%s", pass_id, replacement)
        report_progress(
            self,
            upload_id,
            input_path,
            "classification",
            50,
            "Imagery cataloged; replacing matching timestamp detections." if replacement else "Imagery cataloged; preparing classification graph.",
            {"pass_id": pass_id, "acquisition_time": acq_time, "replacement": replacement},
        )
        record_timeline_event(
            "GEOINT",
            "imagery_replaced" if replacement else "imagery_cataloged",
            original_filename,
            {"pass_id": pass_id, "upload_id": upload_id, "source_hash": source_hash, "replacement": replacement},
            occurred_at=acq_time,
        )

        # 4. Create SatellitePass node in Neo4j
        with db.get_session() as session:
            session.run("""
                MERGE (sp:SatellitePass {postgis_id: $pass_id})
                ON CREATE SET sp.created_at = datetime()
                SET sp.name = $name,
                    sp.sensor_type = $sensor_type,
                    sp.acquisition_time = $acq_time,
                    sp.file_path = $file_path,
                    sp.min_lon = $min_lon,
                    sp.min_lat = $min_lat,
                    sp.max_lon = $max_lon,
                    sp.max_lat = $max_lat,
                    sp.updated_at = datetime()
            """, {
                "pass_id": pass_id,
                "name": original_filename,
                "sensor_type": sensor_type,
                "acq_time": acq_time,
                "file_path": cog_path,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat
            })

        # 5. Tiling inference
        report_progress(self, upload_id, input_path, "inference", 55, "Starting chip inference.", {"pass_id": pass_id})
        clear_existing_detections(pass_id)
        logger.info("[WORKER] Starting tiling inference...")
        inference_metadata = {}
        prompt_override = _parse_prompt_override(upload_meta.get("text_prompts"))
        if prompt_override:
            inference_metadata["text_prompts"] = prompt_override
        # Scene scope: an ontology branch id from the upload narrows the
        # ontology-mode vocabulary to that branch's prompts — the false-positive
        # lever. Ignored when explicit text_prompts are supplied.
        ontology_branch = upload_meta.get("ontology_branch")
        if isinstance(ontology_branch, str) and ontology_branch.strip():
            inference_metadata["ontology_branch"] = ontology_branch.strip()
        for mode_key in ("model", "prompt_mode"):
            mode_value = upload_meta.get(mode_key)
            if isinstance(mode_value, str) and mode_value.strip():
                inference_metadata[mode_key] = mode_value.strip().lower()
        # Honor enabled_layers from the upload form. Two channels: explicit
        # task arg (already parsed) takes precedence; otherwise read from the
        # stored upload_meta which may carry a JSON-encoded list.
        layers_to_use = enabled_layers
        if not layers_to_use:
            raw_layers = upload_meta.get("enabled_layers")
            if isinstance(raw_layers, str):
                try:
                    layers_to_use = json.loads(raw_layers)
                except json.JSONDecodeError:
                    layers_to_use = None
            elif isinstance(raw_layers, list):
                layers_to_use = raw_layers
        if layers_to_use:
            inference_metadata["enabled_layers"] = list(layers_to_use)
            logger.info("[WORKER] Forwarding enabled_layers=%s to /detect", layers_to_use)

        # Phase 5.20: SAM3 is optical-pretrained; running it on TerraMind's
        # SAR pseudo-RGB injects optical-domain priors into a synthetic
        # 3-channel view of a SAR scene, which generates spurious detections.
        # By default we skip SAM3 grounding on SAR rasters and rely on the
        # TerraMind embedding pass only. Operators can opt back in via the
        # ``SAM3_ALLOW_ON_SAR=1`` env or the upload form's
        # ``allow_sam3_on_sar=true`` metadata key.
        sensor_lower = (sensor_type or "").strip().lower()
        if sensor_lower == "sar":
            allow_sam3_on_sar = (
                upload_meta.get("allow_sam3_on_sar") in {True, "true", "1", 1}
                or (os.getenv("SAM3_ALLOW_ON_SAR", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
            )
            inference_metadata["modality"] = "sar"
            inference_metadata["sensor_type"] = "sar"
            if not allow_sam3_on_sar:
                # Express to the inference service: skip SAM3 image grounding;
                # rely on the SAR-specific layers (TerraMind embedding + any
                # future CFAR detector). The inference container interprets
                # an empty layer list with explicit ``sam3=false`` as
                # "embedding-only".
                inference_metadata["skip_sam3_image"] = True
                logger.info(
                    "[WORKER] SAR pass %s: SAM3-on-SAR disabled by default; "
                    "set allow_sam3_on_sar=true or SAM3_ALLOW_ON_SAR=1 to re-enable.",
                    pass_id,
                )
            else:
                logger.info(
                    "[WORKER] SAR pass %s: SAM3-on-SAR enabled by operator opt-in.",
                    pass_id,
                )

        # Streaming detection storage: each chip's surviving detections are
        # written to PostGIS as soon as the chip finishes inference, and a
        # `detections_partial` WS event lets the frontend pick them up. The
        # ontology cache memoises the deterministic ontology so every new
        # class hits detection_ontology() once and reuses the result.
        ontology_cache: dict[str, dict] = {}
        streaming_total = {"stored": 0}

        def _store_chip(kept_dets: list[dict], chip_index: int) -> None:
            for det in kept_dets:
                cls = det.get("class", "Unknown")
                if cls not in ontology_cache:
                    ontology_cache[cls] = {
                        **detection_ontology(cls),
                        "status": "deterministic",
                    }
            stored = store_detections(kept_dets, pass_id, ontology_cache)
            streaming_total["stored"] += stored
            event = {
                "type": "detections_partial",
                "pass_id": pass_id,
                "chip_index": chip_index,
                "stored": stored,
                "stored_total": streaming_total["stored"],
            }
            # Embed map-ready features so the frontend renders this chip's
            # detections live (store_detections back-filled det["id"]). Skip the
            # embed for an over-cap chip — the end-of-pass load still reconciles.
            if LIVE_DETECTIONS_STREAM and 0 < len(kept_dets) <= LIVE_DETECTIONS_MAX_FEATURES:
                feats = [f for f in (_det_to_live_feature(d) for d in kept_dets) if f]
                if feats:
                    event["features"] = feats
            publish_event("detections", event)

        inference_result = slice_and_infer(
            cog_path,
            pass_id,
            inference_metadata=inference_metadata,
            progress_callback=lambda stage, progress, message, extra=None: report_progress(
                self,
                upload_id,
                input_path,
                stage,
                progress,
                message,
                {"pass_id": pass_id, **(extra or {})},
            ),
            on_chip_store=_store_chip,
        )
        inference_summary = inference_result["summary"]
        # Guard: if too many attempted chips failed inference, the pass has
        # near-zero detections not because the scene is empty but because
        # inference never ran on most of it. Causes: the inference service OOMs
        # on every SAM3 forward (over-committed GPU profile), or a CUDA-poison
        # self-heal restart that outlasted the chip-POST retry budget
        # (INFERENCE_RESTART_RETRY_MAX × INFERENCE_RESTART_WAIT_S). A transient
        # restart is now absorbed by the per-chip retry, so a high failure
        # fraction here means a *persistent* fault — fail loudly instead of
        # finalizing `ready` with a misleading empty result; the task's except
        # handler records the error on the upload job. `processed_chips`
        # excludes skipped (nodata) chips, so >0 means inference was genuinely
        # attempted. `inference_success_fraction` is surfaced for honest
        # coverage reporting alongside the existing chip-sampling
        # `coverage_fraction`.
        _processed = int(inference_summary.get("processed_chips") or 0)
        _failed = int(inference_summary.get("failed_chips") or 0)
        if _processed > 0:
            _fail_fraction = _failed / _processed
            inference_summary["inference_success_fraction"] = round(1.0 - _fail_fraction, 4)
            _over_tolerance = (
                INFERENCE_MAX_FAILED_CHIP_FRACTION > 0.0
                and _fail_fraction > INFERENCE_MAX_FAILED_CHIP_FRACTION
            )
            if _failed == _processed or _over_tolerance:
                raise RuntimeError(
                    f"{_failed}/{_processed} inference chips failed for pass {pass_id} "
                    f"({_fail_fraction:.1%} > {INFERENCE_MAX_FAILED_CHIP_FRACTION:.0%} tolerance; "
                    "see inference-sam3 logs — commonly a GPU OOM or a CUDA self-heal "
                    "restart that outlasted the worker's retry budget). Marking upload "
                    "failed rather than ready-with-misleading-zero-detections."
                )
        # Phase 5.20b: for SAR rasters, run the local CFAR detector after
        # the SAM3 / TerraMind chip pass. Always-on for SAR — operators who
        # want CFAR off explicitly can set ``SAR_CFAR_ENABLED=0``. Routes
        # detections through the same ``_store_chip`` callback so they go
        # into PostGIS + fire ``detections_partial`` WS events just like
        # the SAM3 path.
        if sensor_lower == "sar" and (
            os.getenv("SAR_CFAR_ENABLED", "1") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"}:
            try:
                report_progress(
                    self, upload_id, input_path, "inference", 88,
                    "Running SAR CFAR ship detector.", {"pass_id": pass_id},
                )
                cfar_result = run_sar_cfar_for_pass(
                    cog_path, pass_id,
                    threshold_sigma=float(os.getenv("SAR_CFAR_THRESHOLD_SIGMA", "2.5")),
                    guard_px=int(os.getenv("SAR_CFAR_GUARD_PX", "4")),
                    background_px=int(os.getenv("SAR_CFAR_BACKGROUND_PX", "20")),
                    min_pixels=int(os.getenv("SAR_CFAR_MIN_PIXELS", "4")),
                    on_chip_store=_store_chip,
                )
                inference_summary["sar_cfar"] = cfar_result.get("summary") or {}
                logger.info(
                    "[WORKER] SAR CFAR pass %s: %s",
                    pass_id, inference_summary["sar_cfar"],
                )
            except Exception as exc:
                logger.exception("[WORKER] SAR CFAR pass failed for pass %s: %s", pass_id, exc)
                inference_summary["sar_cfar_error"] = str(exc)

        stored_count = streaming_total["stored"]
        logger.info("[WORKER] Total detections after dedupe: %s", stored_count)

        # 6. Finalise: detections were stored progressively per chip, so only
        # candidate links + tracker need the post-inference pass.
        report_progress(
            self,
            upload_id,
            input_path,
            "storage",
            95,
            "Generating candidate links.",
            {"pass_id": pass_id, "detections_count": stored_count, "inference_summary": inference_summary},
        )
        candidate_count = generate_candidate_links_for_pass(pass_id)
        logger.info("[WORKER] Stored %s detections and generated %s candidate links.", stored_count, candidate_count)

        # Invoke detection tracker — failure must not poison detection ingest
        try:
            try:
                from tracker import update_tracks_for_pass
            except ImportError:
                from .tracker import update_tracks_for_pass
            tracker_stats = update_tracks_for_pass(pass_id, postgis_db=postgis_db)
            logger.info("[WORKER] Tracker updated for pass %s: %s", pass_id, tracker_stats)
        except Exception as exc:
            logger.exception("[WORKER] Tracker update failed for pass %s: %s", pass_id, exc)

        payload = {
            "pass_id": pass_id,
            "cog_path": cog_path,
            "upload_id": upload_id,
            "detections_count": stored_count,
            "candidate_links_count": candidate_count,
            "acquisition_time": acq_time,
            "replacement": replacement,
            "inference_summary": inference_summary,
            "processed_chips": inference_summary.get("processed_chips"),
            "total_chips": inference_summary.get("planned_chips"),
            "planned_chips": inference_summary.get("planned_chips"),
            "source_total_chips": inference_summary.get("source_total_chips"),
        }
        update_upload_job(
            upload_id=upload_id,
            file_path=input_path,
            status="ready",
            metadata={
                **payload,
                "stage": "ready",
                "progress": 100,
                "message": "Imagery processing complete.",
            },
            clear_metadata_keys=("error",),
        )
        # Bust the detection vector-tile cache: this pass added/changed rows, so
        # the frontend must re-fetch tiles with the new version token.
        try:
            from platform_schema import bump_tile_version
            payload["tile_version"] = bump_tile_version()
        except Exception:
            logger.debug("bump_tile_version failed after ingest", exc_info=True)
        publish_event("detections", {"type": "detections_updated", **payload})
        publish_event("imagery", {"type": "ingest_succeeded", "stage": "ready", "progress": 100, **payload})
        publish_event("ops", {"type": "imagery_ready", "stage": "ready", "progress": 100, **payload})

        return payload
    except Exception as e:
        logger.exception("[WORKER] Imagery ingest failed: %s", e)
        failed_path = locals().get("input_path") or image_url
        update_upload_job(
            upload_id=upload_id,
            file_path=failed_path,
            status="failed",
            metadata={
                "error": str(e),
                "task_id": self.request.id,
                "stage": "failed",
                "message": f"Imagery processing failed: {e}",
            },
        )
        publish_event("imagery", {"type": "ingest_failed", "image_url": image_url, "upload_id": upload_id, "error": str(e)})
        publish_event("ops", {"type": "imagery_failed", "image_url": image_url, "upload_id": upload_id, "error": str(e)})
        raise


# ============================================================================
# Audio transcription — runs faster-whisper on a worker host. Opt-in via
# WHISPER_ENABLED=1; on hosts without faster-whisper installed the task marks
# ============================================================================
# Audio transcription — runs faster-whisper on a worker host. Opt-in via
# WHISPER_ENABLED=1; on hosts without faster-whisper installed the task marks
# the transcript row as "failed" with a clear error instead of pretending.
# ============================================================================




__all__ = [n for n in dir() if not n.startswith("__")]
