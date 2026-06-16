"""Chip encode/validation, grid planning, and the SAM3 HTTP client (caps
negotiation, raw/multipart POST, inference-restart retry)."""

from worker.config import *  # noqa: F401,F403

def chip_to_uint8_rgb(chip: np.ndarray) -> np.ndarray:
    chip_rgb = chip[:3] if chip.shape[0] >= 3 else np.repeat(chip[:1], 3, axis=0)
    chip_rgb = np.nan_to_num(chip_rgb.astype("float32"), nan=0.0, posinf=0.0, neginf=0.0)
    if chip_rgb.dtype != np.uint8:
        low, high = np.percentile(chip_rgb, [2, 98])
        if high > low:
            chip_rgb = np.clip((chip_rgb - low) / (high - low) * 255, 0, 255).astype(np.uint8)
        else:
            chip_rgb = np.zeros_like(chip_rgb, dtype=np.uint8)
    return np.moveaxis(chip_rgb, 0, -1)


def valid_data_mask(
    src: rasterio.io.DatasetReader,
    window: Window,
    out_shape: tuple[int, int] | None = None,
) -> np.ndarray | None:
    """Return a boolean valid-data mask for a raster window, or None when the
    dataset does not expose no-data/alpha masking information.

    ``out_shape`` (h, w) decimates the mask so it lines up with a downsampled
    chip read (used by the full-scene pass). Normal callers omit it and get a
    native-resolution mask."""
    try:
        mask = src.dataset_mask(window=window, out_shape=out_shape) if out_shape else src.dataset_mask(window=window)
    except Exception:
        return None
    if mask is None:
        return None
    valid = np.asarray(mask) > 0
    if valid.size == 0:
        return None
    if np.all(valid):
        return None
    return valid


def clip_box_to_valid_mask(
    valid_mask: np.ndarray | None,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    min_valid_fraction: float | None = None,
) -> tuple[float, float, float, float] | None:
    """Clip a bbox to its valid-pixel envelope.

    Phase 3.10: ``min_valid_fraction`` is overridable per call so callers
    (which know the detection's parent_class) can apply a class-specific
    floor — water-edge ships keep at 0.05, large infrastructure at 0.30.
    Falls back to the global ``INFERENCE_MIN_VALID_DETECTION_FRACTION``
    when no override is passed.
    """
    threshold = (
        INFERENCE_MIN_VALID_DETECTION_FRACTION if min_valid_fraction is None
        else max(0.0, min(1.0, float(min_valid_fraction)))
    )
    if valid_mask is None:
        return x1, y1, x2, y2
    height, width = valid_mask.shape[:2]
    if width <= 0 or height <= 0:
        return None

    center_x = int(min(width - 1, max(0, round((x1 + x2) / 2.0))))
    center_y = int(min(height - 1, max(0, round((y1 + y2) / 2.0))))
    if not bool(valid_mask[center_y, center_x]):
        return None

    ix1 = max(0, min(width, int(math.floor(x1))))
    iy1 = max(0, min(height, int(math.floor(y1))))
    ix2 = max(0, min(width, int(math.ceil(x2))))
    iy2 = max(0, min(height, int(math.ceil(y2))))
    if ix2 <= ix1 or iy2 <= iy1:
        return None

    box_mask = valid_mask[iy1:iy2, ix1:ix2]
    valid_count = int(np.count_nonzero(box_mask))
    if valid_count <= 0:
        return None
    valid_fraction = valid_count / max(1, box_mask.size)
    if valid_fraction < threshold:
        return None

    valid_y, valid_x = np.nonzero(box_mask)
    clipped_x1 = float(ix1 + int(valid_x.min()))
    clipped_y1 = float(iy1 + int(valid_y.min()))
    clipped_x2 = float(ix1 + int(valid_x.max()) + 1)
    clipped_y2 = float(iy1 + int(valid_y.max()) + 1)
    if clipped_x2 <= clipped_x1 or clipped_y2 <= clipped_y1:
        return None
    return clipped_x1, clipped_y1, clipped_x2, clipped_y2

def sample_axis_indices(count: int, sample_count: int) -> list[int]:
    if count <= 0:
        return [0]
    if sample_count >= count:
        return list(range(count))
    if sample_count <= 1:
        return [count // 2]
    return sorted({round(index * (count - 1) / (sample_count - 1)) for index in range(sample_count)})


def plan_inference_grid(
    width: int,
    height: int,
    chip_size: int,
    overlap: int,
    max_chips: int,
    block_size: tuple[int, int] | None = None,
) -> dict:
    """Plan the sliding-window grid of chips over a raster.

    When ``block_size=(block_x, block_y)`` is provided (Phase 2), each chip
    origin is snapped down to the nearest multiple of the source's internal
    tile size. Misaligned reads cost up to 4× the bytes per chip because the
    output window drags in adjacent source tiles; aligning origins to the
    block grid is the dominant lever in the Microsoft pytorch-cloud-geotiff
    optimization paper. Aligning the *origin* — not the *step* — keeps the
    grid count unchanged so downstream consumers (progress %, sampling
    ratio) don't see a shape change.

    Returns the legacy ``x_indices`` / ``y_indices`` (integer logical indices
    into the regular grid) plus ``x_offsets`` / ``y_offsets`` — the actual
    pixel offsets after block snapping — and ``x_window_sizes`` /
    ``y_window_sizes``, the per-chip window extents. Snapping an origin DOWN
    moves the chip's far edge down by the same delta; with chip 1008 /
    overlap 252 / block 512 the snapped step alternates 512 and 1024 px,
    which exceeds the chip size and left recurring ~16 px never-analyzed
    strips. Each window is therefore extended by its snap delta
    (``chip_size + (raw - snapped)``, clipped to the raster edge) so every
    chip still ends where the un-snapped chip would have — full coverage is
    preserved while origins stay block-aligned.
    """
    step = max(1, chip_size - overlap)

    def axis_count(size: int) -> int:
        if size <= chip_size:
            return 1
        return max(1, math.ceil((size - chip_size) / step) + 1)

    x_count = axis_count(width)
    y_count = axis_count(height)
    source_total = x_count * y_count

    if max_chips <= 0 or source_total <= max_chips:
        x_indices = list(range(x_count))
        y_indices = list(range(y_count))
        sampled = False
    else:
        target_y = max(1, min(y_count, int(math.sqrt(max_chips * y_count / max(1, x_count)))))
        target_x = max(1, min(x_count, max_chips // target_y))
        while target_x * target_y > max_chips and target_y > 1:
            target_y -= 1
            target_x = max(1, min(x_count, max_chips // target_y))

        x_indices = sample_axis_indices(x_count, target_x)
        y_indices = sample_axis_indices(y_count, target_y)
        sampled = True

    block_x, block_y = block_size if block_size else (1, 1)

    def _axis_layout(indices: list[int], block: int, dim: int) -> tuple[list[int], list[int]]:
        """Per-index (origin, window_size) after block snapping.

        Origins snap DOWN to the block grid; the window grows by the snap
        delta so the chip's far edge stays at ``raw + chip_size`` (clipped
        to the raster) — coverage is identical to the un-snapped grid.
        """
        offsets: list[int] = []
        sizes: list[int] = []
        for idx in indices:
            raw = min(max(0, idx * step), max(0, dim - 1))
            snapped = (raw // block) * block if block > 1 else raw
            offsets.append(snapped)
            sizes.append(min(chip_size + (raw - snapped), dim - snapped))
        return offsets, sizes

    x_offsets, x_window_sizes = _axis_layout(x_indices, block_x, width)
    y_offsets, y_window_sizes = _axis_layout(y_indices, block_y, height)

    return {
        "step": step,
        "x_indices": x_indices,
        "y_indices": y_indices,
        "x_offsets": x_offsets,
        "y_offsets": y_offsets,
        "x_window_sizes": x_window_sizes,
        "y_window_sizes": y_window_sizes,
        "block_size": [int(block_x), int(block_y)],
        "source_total": source_total,
        "planned_total": max(1, len(x_indices) * len(y_indices)),
        "sampled": sampled,
        "max_chips": max_chips,
    }


_INFERENCE_CAPS_LOCK = threading.Lock()
_INFERENCE_CAPS_CACHE: dict | None = None


def _negotiate_inference_capabilities(session: requests.Session) -> dict:
    """Probe ``/capabilities`` once and cache the response.

    The inference service advertises optional protocol features (Phase 4:
    raw-binary `/detect_raw`). We negotiate once at first use so the
    decision is made before any chip flows, then reuse the cached answer
    across the lifetime of the worker process. A network failure falls
    back to the safe legacy `/detect` multipart path.
    """
    global _INFERENCE_CAPS_CACHE
    if _INFERENCE_CAPS_CACHE is not None:
        return _INFERENCE_CAPS_CACHE
    with _INFERENCE_CAPS_LOCK:
        if _INFERENCE_CAPS_CACHE is not None:
            return _INFERENCE_CAPS_CACHE
        try:
            resp = session.get(f"{INFERENCE_SAM3_URL}/capabilities", timeout=5)
            resp.raise_for_status()
            caps = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if not isinstance(caps, dict):
                caps = {}
        except Exception as exc:
            logger.info(
                "[WORKER] /capabilities probe failed (%s); falling back to /detect multipart only.",
                exc,
            )
            caps = {}
        _INFERENCE_CAPS_CACHE = caps
        return caps


_RETRYABLE_HTTP_STATUS = frozenset({502, 503, 504})


def _inference_unavailable(exc: BaseException) -> bool:
    """True when an exception means inference-sam3 is unavailable — down,
    restarting, or model still preloading — so the chip should wait for recovery
    and be retried rather than scored as a failed (zero-detection) chip.

    A self-heal restart has two faces, both transient:
      * container gone → TCP refused / DNS failure → ``requests.ConnectionError``
        (also covers ``ConnectTimeout``), or a mid-stream ``ChunkedEncodingError``.
      * container back but the SAM3 bundle is still preloading → ``/detect_raw``
        returns HTTP 503 (see decisions/why-503-on-unloaded-component.md); a
        502/504 from any proxy in front is the same "not ready" class.

    A ``ReadTimeout`` (one slow forward) or any other 4xx/5xx (a genuine
    per-chip failure, e.g. a 500 on a single tile) is deliberately NOT retried
    here — only whole-service unavailability is.
    """
    if isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError)
    ):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        return resp is not None and resp.status_code in _RETRYABLE_HTTP_STATUS
    return False


def _wait_for_inference_healthy(timeout_s: float = INFERENCE_RESTART_WAIT_S) -> bool:
    """Poll inference-sam3 /health until the model is loaded or the deadline passes.

    /health *always* returns HTTP 200 (it never touches the GPU) with a
    ``model_loaded`` flag that is False while the SAM3 bundle preloads after a
    restart, so a status-only check would return immediately and the retried
    POST would just hit 503 again. We require ``model_loaded`` truthy (pool
    populated) so the wait actually spans the preload. Uses a fresh connection
    (not the chip session, whose pooled socket points at the dead container) so
    DNS re-resolves to the respawned container. Returns True once the model is
    servable, False on timeout.
    """
    health_url = f"{INFERENCE_SAM3_URL.rstrip('/')}/health"
    deadline = time.time() + max(1.0, timeout_s)
    while time.time() < deadline:
        try:
            resp = requests.get(health_url, timeout=3)
            if resp.status_code == 200 and bool(resp.json().get("model_loaded")):
                return True
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2.0)
    return False


def _post_chip_with_restart_retry(send, chip_label: str) -> dict | None:
    """Run ``send()`` (a thunk returning a ``requests.Response``), parse JSON,
    and retry the whole call across an inference-sam3 self-heal restart.

    On a whole-service unavailability the service is likely mid-restart
    (poisoned CUDA context → ``os._exit(1)`` → compose respawn → SAM3 preload,
    ~100-150 s); wait for /health to report the model loaded and retry up to
    ``INFERENCE_RESTART_RETRY_MAX`` times. Per-chip errors (read timeout, a 500
    on one tile, bad JSON) return None immediately as before, so the caller
    scores just that chip as failed.
    """
    attempt = 0
    while True:
        try:
            resp = send()
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if _inference_unavailable(exc) and attempt < INFERENCE_RESTART_RETRY_MAX:
                attempt += 1
                logger.warning(
                    "[WORKER] inference unavailable on chip %s (attempt %s/%s); "
                    "waiting up to %ss for self-heal restart + model preload, then "
                    "retrying: %s",
                    chip_label, attempt, INFERENCE_RESTART_RETRY_MAX,
                    INFERENCE_RESTART_WAIT_S, exc,
                )
                # Sleep until /health is likely back (a slow SAM3 preload may
                # outlast one wait — that just consumes a retry and we try
                # again). The next send() is the real test, so a wait timeout is
                # not itself terminal; the bounded retry count is.
                _wait_for_inference_healthy()
                continue
            logger.warning("[WORKER] sam3 inference failed on chip %s: %s", chip_label, exc)
            return None


def _post_chip_to_sam3(
    session: requests.Session,
    chip_file,
    chip_meta_payload: str,
    chip_label: str,
) -> dict | None:
    """POST a single chip to SAM3 /detect. Returns response JSON or None on failure."""
    def _send():
        try:
            meta = json.loads(chip_meta_payload) if chip_meta_payload else {}
        except (TypeError, json.JSONDecodeError):
            meta = {}
        filename = meta.get("filename") or "chip.png"
        content_type = meta.get("content_type") or "image/png"
        chip_file.seek(0)
        return session.post(
            f"{INFERENCE_SAM3_URL}/detect",
            files={"image": (filename, chip_file, content_type)},
            data={"metadata": chip_meta_payload},
            timeout=INFERENCE_CHIP_TIMEOUT_S,
        )

    try:
        return _post_chip_with_restart_retry(_send, chip_label)
    finally:
        chip_file.close()


def _post_chip_to_sam3_raw(
    session: requests.Session,
    chip_array: np.ndarray,
    chip_meta_payload: str,
    chip_label: str,
) -> dict | None:
    """POST a raw uint8 RGB chip directly as ``application/octet-stream``.

    Skips PIL PNG encode on the worker and PIL PNG decode on the inference
    service. Metadata is carried in a single base64 header instead of a
    multipart form field; the payload is exactly ``chip_array.tobytes()``
    so the server's ``np.frombuffer`` produces a pixel-identical input
    array to what /detect's ``_decode_rgb`` would have produced.
    """
    def _send():
        meta_b64 = base64.b64encode(chip_meta_payload.encode("utf-8")).decode("ascii")
        h, w = chip_array.shape[:2]
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Chip-Modality": "rgb",
            "X-Chip-Shape": f"{int(h)},{int(w)},3",
            "X-Chip-Dtype": "uint8",
            "X-Chip-Meta-B64": meta_b64,
        }
        return session.post(
            f"{INFERENCE_SAM3_URL}/detect_raw",
            data=chip_array.tobytes(),
            headers=headers,
            timeout=INFERENCE_CHIP_TIMEOUT_S,
        )

    return _post_chip_with_restart_retry(_send, chip_label)


def _png_file(rgb: np.ndarray):
    chip_file = tempfile.SpooledTemporaryFile(max_size=INFERENCE_CHIP_SPOOL_MAX_BYTES)
    with _chip_stage_timer("encode_png"):
        Image.fromarray(rgb, mode="RGB").save(chip_file, format="PNG")
    chip_file.seek(0)
    return chip_file


def _geotiff_window_file(
    src: rasterio.io.DatasetReader,
    window: Window,
    indexes: list[int],
    *,
    preread: np.ndarray | None = None,
):
    """Write a windowed GeoTIFF from ``src`` to a spool file.

    When ``preread`` is supplied it is interpreted as the full-band chip
    already read for this window (Phase 2: callers pass the chip from the
    earlier probe to skip the redundant GDAL roundtrip). Indexes are
    treated as 1-based GDAL band numbers; we slice with ``idx - 1`` into
    the pre-read array.
    """
    with _chip_stage_timer("encode_geotiff_read"):
        if preread is not None:
            data = preread[[i - 1 for i in indexes], :, :].astype("float32", copy=False)
        else:
            data = src.read(indexes=indexes, window=window).astype("float32", copy=False)
    transform = src.window_transform(window)
    profile = {
        "driver": "GTiff",
        "height": data.shape[1],
        "width": data.shape[2],
        "count": data.shape[0],
        "dtype": "float32",
        "transform": transform,
        "crs": src.crs,
    }
    with _chip_stage_timer("encode_geotiff_write"):
        with MemoryFile() as memfile:
            with memfile.open(**profile) as dst:
                dst.write(data)
                descriptions = src.descriptions or ()
                for out_index, src_index in enumerate(indexes, start=1):
                    if src_index - 1 < len(descriptions) and descriptions[src_index - 1]:
                        dst.set_band_description(out_index, descriptions[src_index - 1])
            payload = memfile.read()
        chip_file = tempfile.SpooledTemporaryFile(max_size=INFERENCE_CHIP_SPOOL_MAX_BYTES)
        chip_file.write(payload)
        chip_file.seek(0)
    return chip_file


def _encode_bool_mask(mask: np.ndarray) -> dict:
    """Compact bool mask transport for inference services that need chip validity."""
    mask_bool = np.asarray(mask, dtype=bool)
    packed = np.packbits(mask_bool.reshape(-1).astype(np.uint8), bitorder="little")
    return {
        "shape": [int(mask_bool.shape[0]), int(mask_bool.shape[1])],
        "bitorder": "little",
        "data_b64": base64.b64encode(packed.tobytes()).decode("ascii"),
    }


def _emit_chip_payload(
    window: Window,
    src: rasterio.io.DatasetReader,
    *,
    valid_mask=None,
    chip: np.ndarray | None = None,
    raw_rgb_enabled: bool = False,
    modality_hint: str | None = None,
):
    """Return (fileobj_or_array, metadata) for a SAM3 chip upload.

    Multispectral (≥6-band) and SAR (2-band VV/VH) rasters go out as GeoTIFFs;
    everything else is encoded to a uint8 RGB PNG (or, when ``raw_rgb_enabled``
    is True, a raw uint8 numpy array consumed by ``/detect_raw``).

    ``modality_hint`` is the operator/pipeline modality from the ingest
    metadata and is authoritative: a single-band GRD or a SAR raster with
    stripped band descriptions fails the VV/VH heuristic, and without the
    hint its chips were emitted as ``modality=rgb`` — overriding the
    operator's explicit ``sar`` and routing them down the optical path.

    Phase 2: ``chip`` accepts the full-band pre-read window so we don't
    pay for a second `src.read()` here.

    Phase 4: ``raw_rgb_enabled`` short-circuits the PIL PNG encode for the
    RGB branch — the returned "file" object is actually the numpy array,
    and the metadata carries ``transport=raw`` so the poster knows to use
    ``_post_chip_to_sam3_raw`` instead of multipart ``_post_chip_to_sam3``.
    """
    window_transform = src.window_transform(window)
    geo_meta = {
        "source_crs": src.crs.to_string() if src.crs else None,
        "chip_transform": list(window_transform.to_gdal()),
        "chip_transform_order": "gdal",
        "source_window": [int(window.col_off), int(window.row_off), int(window.width), int(window.height)],
        "source_bounds": list(src.window_bounds(window)),
    }
    valid_mask_meta = _encode_bool_mask(valid_mask) if valid_mask is not None else None
    descriptions = tuple((desc or "").strip().lower() for desc in (src.descriptions or ()))
    has_vv_vh = {"vv", "vh"}.issubset(set(descriptions))

    if (src.count == 2 and has_vv_vh) or (modality_hint or "").strip().lower() == "sar":
        indexes = [1, 2] if src.count >= 2 else [1]
        if has_vv_vh:
            polarizations = ["VV", "VH"]
        else:
            # Single-band / unlabeled SAR selected via the hint: S1 GRD
            # convention is (VV, VH) band order.
            polarizations = ["VV", "VH"][: len(indexes)]
        meta = {
            "modality": "sar",
            "filename": "chip.tif",
            "content_type": "image/tiff",
            "geo": geo_meta,
            "sar_polarizations": polarizations,
        }
        if valid_mask_meta:
            meta["valid_mask"] = valid_mask_meta
        return _geotiff_window_file(src, window, indexes, preread=chip), meta

    if src.count >= 6:
        meta = {
            "modality": "multispectral",
            "filename": "chip.tif",
            "content_type": "image/tiff",
            "geo": geo_meta,
        }
        if valid_mask_meta:
            meta["valid_mask"] = valid_mask_meta
        return _geotiff_window_file(src, window, list(range(1, 7)), preread=chip), meta

    if chip is None:
        chip = src.read(window=window)
    chip_rgb = chip_to_uint8_rgb(chip)
    if valid_mask is not None:
        chip_rgb = chip_rgb.copy()
        chip_rgb[~valid_mask] = 0
    if raw_rgb_enabled:
        # Phase 4: skip PIL.Image.save(format="PNG") — the array goes
        # straight onto the wire. `transport=raw` flags the poster path.
        meta = {
            "modality": "rgb",
            "transport": "raw",
            "geo": geo_meta,
        }
        if valid_mask_meta:
            meta["valid_mask"] = valid_mask_meta
        return chip_rgb, meta
    meta = {
        "modality": "rgb",
        "filename": "chip.png",
        "content_type": "image/png",
        "geo": geo_meta,
    }
    if valid_mask_meta:
        meta["valid_mask"] = valid_mask_meta
    return _png_file(chip_rgb), meta


def _parse_prompt_override(raw: object) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        prompts = [str(item).strip() for item in raw if str(item).strip()]
        return prompts or None
    text = str(raw).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, list):
        prompts = [str(item).strip() for item in payload if str(item).strip()]
    else:
        prompts = [item.strip() for item in text.split(",") if item.strip()]
    return prompts or None




__all__ = [n for n in dir() if not n.startswith("__")]
