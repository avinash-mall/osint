"""FMV windowed tracking pipeline: process_fmv / consolidate_fmv + helpers."""

from worker.config import *  # noqa: F401,F403
from worker.app import celery_app  # noqa: F401
from worker.dispatch import *  # noqa: F401,F403
from worker.graph import project_fmv_to_graph
from events import publish_event

def _xyxy_to_normalized_cxcywh(box: list[float], width: float | None = None, height: float | None = None) -> list[float]:
    x1, y1, x2, y2 = [float(v) for v in box[:4]]
    if width and height and width > 0 and height > 0:
        return [
            max(0.0, min(1.0, ((x1 + x2) / 2.0) / width)),
            max(0.0, min(1.0, ((y1 + y2) / 2.0) / height)),
            max(0.0, min(1.0, (x2 - x1) / width)),
            max(0.0, min(1.0, (y2 - y1) / height)),
        ]
    return [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)]


FMV_TRACK_FPS = float(os.getenv("FMV_TRACK_FPS", "4"))
# 540p prep clip: at 10 km slant range a person ≈22 px on the prep clip (vs
# ~6 px at 270p), which puts the target inside SAM3's small-object band.
FMV_TRACK_HEIGHT = int(os.getenv("FMV_TRACK_HEIGHT", "540"))
# SAM3's video predictor pins every decoded frame as a single CUDA tensor at
# session start (~80 MiB/frame at 540p once letterboxed to 1024² on a 16 GiB
# GPU also holding the SAM3 image+video weights). 48 frames keeps peak VRAM
# under ~5 GiB and lets a 12 s window run at 4 fps in a single session.
FMV_TRACK_FRAMES_PER_WINDOW = int(os.getenv("FMV_TRACK_FRAMES_PER_WINDOW", "48"))
# Window slicing of the source clip. Each window is its own SAM3 video
# session, so the tracker gets a fresh re-detection every WINDOW_SECONDS of
# source content, avoiding the "tracker loses target after 7 s" behaviour
# observed on Day Flight.mpg.
FMV_TRACK_WINDOW_SECONDS = float(os.getenv("FMV_TRACK_WINDOW_SECONDS", "12"))
FMV_TRACK_WINDOW_OVERLAP_SECONDS = float(os.getenv("FMV_TRACK_WINDOW_OVERLAP_SECONDS", "2"))
# In-flight cap for /detect_video fan-out. Defaults to the inference-sam3
# pool size discovered at /health (i.e. one slot per GPU), so each parallel
# task lands on a distinct multiplex predictor replica. The env override
# is a hard ceiling — if /health reports a smaller pool we use that.
FMV_INFLIGHT_REQUESTS = max(1, int(os.getenv("FMV_INFLIGHT_REQUESTS", "4")))
# Mirror of INFERENCE_MAX_FAILED_CHIP_FRACTION for the FMV (window, prompt)
# fan-out: a single failed task no longer discards the whole clip; the clip
# fails only when more than this fraction of tasks failed.
FMV_MAX_FAILED_TASK_FRACTION = max(0.0, min(1.0, env_float("FMV_MAX_FAILED_TASK_FRACTION", 0.05)))


def _probe_source(src_path: str) -> tuple[float, float]:
    """Return `(source_fps, duration_s)` via ffprobe (best-effort)."""
    fps = 30.0
    duration = 0.0
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate,duration:format=duration",
             "-of", "default=nw=1", src_path],
            capture_output=True, text=True, check=False, timeout=10,
        )
        for line in (proc.stdout or "").splitlines():
            key, _, val = line.partition("=")
            val = val.strip()
            if key == "r_frame_rate" and val:
                if "/" in val:
                    num, den = val.split("/", 1)
                    den_f = float(den)
                    if den_f > 0:
                        fps = float(num) / den_f
                else:
                    fps = float(val)
            elif key == "duration" and val and val != "N/A":
                try:
                    duration = float(val)
                except ValueError:
                    pass
    except Exception:
        pass
    return fps or 30.0, duration


# Smallest plausible 540p libx264 single-frame mp4 is several KiB. A 261-byte
# ftyp-only stub (the failure mode we're guarding against — see the
# Truck.win01 incident 2026-05-12) is well below this. The threshold is
# conservative: well above any legitimate output, well below the smallest
# real clip we'd produce.
_FMV_WINDOW_MIN_BYTES = 4 * 1024


def _window_output_is_valid(path: Path) -> bool:
    """True iff `path` is a non-empty mp4 with at least one video stream.

    Guards against ffmpeg's "exit 0 with no streams" failure mode where
    `-ss` lands past the last keyframe or the filter graph emits zero
    frames. Cheap size check first, then ffprobe stream-type check."""
    try:
        if not path.is_file() or path.stat().st_size < _FMV_WINDOW_MIN_BYTES:
            return False
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        return bool((proc.stdout or "").strip())
    except Exception:
        return False


def _prepare_tracking_window(src_path: str, window_idx: int, start_s: float, duration_s: float) -> Path | None:
    """Extract a single sliding-window track clip from the source.

    Returns the output Path, or None on ffmpeg failure or zero-stream
    output. Each window is a short low-fps low-res mp4 sized so the
    entire decoded frame stack fits in GPU memory at SAM3 video
    session-init time."""
    src = Path(src_path)
    out = src.with_name(f"{src.stem}.win{window_idx:02d}.track.mp4")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        if _window_output_is_valid(out):
            return out
        # Cached file exists but is a stub (e.g. produced by an older
        # build that didn't validate). Unlink so we re-extract below.
        logger.warning("FMV window %d cached output at %s is invalid; re-extracting", window_idx, out)
        out.unlink(missing_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start_s:.3f}",
        "-i", str(src),
        "-t", f"{duration_s:.3f}",
        "-an",
        "-vf", f"fps={FMV_TRACK_FPS},scale=-2:{FMV_TRACK_HEIGHT}",
        "-frames:v", str(FMV_TRACK_FRAMES_PER_WINDOW),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        # Force mp4 container explicitly: the tmp filename ends in
        # `.mp4.tmp`, which defeats ffmpeg's extension-based format
        # auto-detection. The final `os.replace(tmp, out)` lands at
        # `.mp4`, so the container we write here must be mp4.
        "-f", "mp4",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.warning("FMV window %d prep failed for %s: %s", window_idx, src, proc.stderr[:300])
        tmp.unlink(missing_ok=True)
        return None
    if not _window_output_is_valid(tmp):
        logger.warning(
            "FMV window %d produced zero-stream output for %s (start=%.3fs len=%.3fs) — deleting and skipping",
            window_idx, src, start_s, duration_s,
        )
        tmp.unlink(missing_ok=True)
        return None
    os.replace(tmp, out)
    return out


def _slice_windows(duration_s: float) -> list[tuple[float, float]]:
    """Compute `[(start_s, length_s)]` slices that overlap by
    FMV_TRACK_WINDOW_OVERLAP_SECONDS. The final window may be shorter if
    the duration doesn't divide evenly. Returns at least one window."""
    if duration_s <= 0:
        return [(0.0, FMV_TRACK_WINDOW_SECONDS)]
    step = max(0.5, FMV_TRACK_WINDOW_SECONDS - FMV_TRACK_WINDOW_OVERLAP_SECONDS)
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_s:
        length = min(FMV_TRACK_WINDOW_SECONDS, duration_s - start)
        windows.append((start, length))
        if start + length >= duration_s:
            break
        start += step
    return windows or [(0.0, duration_s)]


def _ensure_fmv_profile(session: requests.Session, clip_id: int, max_wait_s: float = 600.0) -> dict:
    """Load the FMV inference profile, retrying on 409 (other request in flight)
    so two consecutive FMV tasks don't fight over the same swap. Returns the
    /health JSON so the caller knows which video backend is active."""
    deadline = time.time() + max_wait_s
    last_err: str | None = None
    while time.time() < deadline:
        try:
            resp = session.post(
                f"{INFERENCE_SAM3_URL}/load",
                params={"profile": "fmv"},
                timeout=600,
            )
            if resp.status_code == 200:
                break
            if resp.status_code == 409:
                publish_event(
                    f"fmv:{clip_id}",
                    {"type": "fmv_detections_progress", "clip_id": clip_id,
                     "stage": "waiting_for_inference", "detail": "another request in flight"},
                )
                last_err = "inference busy (409)"
                time.sleep(2)
                continue
            last_err = f"{resp.status_code}: {resp.text[:200]}"
            time.sleep(2)
        except requests.RequestException as exc:
            last_err = f"connection error: {exc}"
            time.sleep(2)
    else:
        raise RuntimeError(f"could not load FMV inference profile: {last_err or 'timeout'}")
    # Read /health to learn whether the multiplex or base predictor came up.
    health = session.get(f"{INFERENCE_SAM3_URL}/health", timeout=10).json()
    return health


def _revert_inference_profile(session: requests.Session, profile: str = "imagery_rgb",
                              max_wait_s: float = 30.0) -> None:
    """Best-effort: switch the inference service back to ``profile`` after FMV work.

    FMV processing leaves the single GPU pool on the ``fmv`` profile, which has no
    sam3_image, so the COP's imagery detection degrades until something reloads it.
    Reverting here returns the resting state to the (lightest) imagery profile —
    ``imagery_rgb`` so tight-VRAM cards don't reload the full imagery union (which
    would OOM); the next MSI/SAR /detect auto-heals to its own modality profile.
    Best-effort by design:
    a 409 means another FMV session is still in flight and correctly keeps ``fmv``,
    so we just give up quietly rather than fight it. Never raises — a failed revert
    must not fail the FMV task itself."""
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        try:
            resp = session.post(
                f"{INFERENCE_SAM3_URL}/load", params={"profile": profile}, timeout=600,
            )
            if resp.status_code == 200:
                return
            if resp.status_code == 409:
                # Another FMV session is busy; leaving it on fmv is correct.
                return
            time.sleep(2)
        except requests.RequestException:
            time.sleep(2)


def _update_clip_tracking(clip_id: int, **fields) -> None:
    """Merge tracking_* fields into fmv_clips.metadata jsonb. Best-effort —
    failures here shouldn't kill the tracking task."""
    if not fields:
        return
    try:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE fmv_clips
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb,
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (json.dumps(fields), clip_id),
            )
    except Exception:
        logger.exception("failed to update fmv_clips.metadata for clip %s", clip_id)


def _drain_response_entries(resp) -> list[dict]:
    """Drain one /detect_video streaming response to a list of parsed
    JSON dicts. Pure I/O — safe to run outside any DB / dedup lock so
    parallel fan-out tasks don't serialize on each other while their
    GPU sessions are still streaming. The caller passes the resulting
    list to `_insert_detection_rows` under a lock to do the DB inserts
    and dedup updates."""
    entries: list[dict] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # A truncated trailing fragment (stream cut mid-line) must not
            # discard the window's already-parsed entries.
            logger.warning(
                "skipping unparseable /detect_video NDJSON line (%d chars)", len(line)
            )
    return entries


# Session prompts that are bookkeeping sentinels, not real concept labels.
# YOLOE mode fans out one session per window with a placeholder prompt;
# the runner emits the per-detection class inside the NDJSON.
_SENTINEL_PROMPTS = frozenset({"_yoloe"})


def _insert_detection_rows(cur, clip_id: int, source_fps: float, window_idx: int, window_start_frame: int,
                            session_prompt: str, entries: list[dict], next_track_id: int) -> tuple[int, int]:
    """Insert one (window, prompt) session's parsed entries into fmv_detections.

    Each call corresponds to exactly one (window, prompt) session — the
    upstream SAM3 API (`sam3_video_inference.py:656` / `sam3_multiplex_tracking.py:1934`)
    resets state on every text `add_prompt`, so one session can only track
    one concept. `session_prompt` is the prompt this session was launched
    with; we trust it over any prompt_text the runner emits.

    `entries` is the result of `_drain_response_entries(resp)` — split so
    the HTTP drain can happen unlocked while this function runs under the
    worker's shared lock to mutate `next_track_id` without races.

    Rows are inserted raw — including window-seam and cross-prompt
    duplicates. The post-inference consolidation pass (`worker.consolidate_fmv`
    / backend/fmv_tracker.py) stitches identity across windows and prompts
    and collapses those duplicates; doing it here with no full-clip view
    only ever produced a partial same-`(frame, class)` fix.

    Returns (rows_inserted, new_next_track_id).
    """
    multiplier = source_fps / FMV_TRACK_FPS if FMV_TRACK_FPS > 0 else 1.0
    inserted = 0
    local_to_global: dict[tuple, int] = {}
    fallback_prompt = session_prompt or "track"
    for entry in entries:
        prep_idx = int(entry["frame_index"])
        source_frame = window_start_frame + int(round(prep_idx * multiplier))
        # SAM3 now emits ``bbox_xyxy_norm`` (already in [0,1] relative to
        # the prep clip). Convert directly to cxcywh-normalised without
        # re-normalising — the previous _xyxy_to_normalized_cxcywh path
        # treated normalised values as pixel xywh and produced offset
        # boxes. Empty/heartbeat frames carry None — store as [] so the
        # frontend's normalizeBbox falls through to the OBB (if any) or
        # skips drawing for that frame.
        bbox_norm = entry.get("bbox_xyxy_norm")
        if bbox_norm and len(bbox_norm) == 4:
            x1n, y1n, x2n, y2n = (float(v) for v in bbox_norm)
            wn = max(0.0, x2n - x1n)
            hn = max(0.0, y2n - y1n)
            cxcywh_norm = [(x1n + x2n) / 2.0, (y1n + y2n) / 2.0, wn, hn]
            bbox_json = json.dumps(cxcywh_norm)
        else:
            # Heartbeat / lost-track frame.
            bbox_json = json.dumps([])
        # Class resolution: PCS mode runs one session per concept, so the
        # session prompt IS the class — trust it over runner output. AMG
        # and YOLOE modes use a single sentinel session prompt
        # ("_amg" / "_yoloe") and the runner assigns a real class per
        # detection (AMG via Grounding-DINO labels; YOLOE from its
        # built-in vocab or text-prompt set_classes). For those modes,
        # honour entry["class"] whenever it's set and not itself a
        # sentinel; fall back only when the runner couldn't label it.
        entry_class = entry.get("class")
        if fallback_prompt in _SENTINEL_PROMPTS:
            if entry_class and entry_class not in _SENTINEL_PROMPTS:
                cls = str(entry_class)
            else:
                cls = fallback_prompt
        else:
            cls = fallback_prompt
        prompt_text = fallback_prompt
        local_tid = entry.get("track_id")
        if local_tid is not None:
            try:
                ltid = int(local_tid)
                key = (window_idx, prompt_text, ltid)
                if key not in local_to_global:
                    local_to_global[key] = next_track_id
                    next_track_id += 1
                global_tid = local_to_global[key]
            except (TypeError, ValueError):
                global_tid = local_tid
        else:
            global_tid = None

        meta_json = json.dumps({
            "track_id": global_tid,
            "mask_rle": entry.get("mask_rle"),
            "obb": entry.get("obb"),
            "obb_format": entry.get("obb_format"),
            "obb_source": entry.get("obb_source"),
            "obb_angle_deg": entry.get("obb_angle_deg"),
            "edge_truncated": entry.get("edge_truncated"),
            "embedding": entry.get("embedding"),
            "prompt_text": prompt_text,
            "window_index": window_idx,
            "provider": "sam3",
            "source_layer": entry.get("source_layer"),
        })
        conf = float(entry.get("score") or 0.0)
        cur.execute(
            """
            INSERT INTO fmv_detections (clip_id, frame_index, class, confidence, bbox, metadata)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (clip_id, source_frame, cls, conf, bbox_json, meta_json),
        )
        inserted += 1
    return inserted, next_track_id


@celery_app.task(name="worker.process_fmv", queue="imagery")
def process_fmv(clip_id: int, video_path: str, text_prompts: list[str] | None = None,
                frame_stride: int | None = None, max_frames: int | None = None,
                prompt_mode: str = "pcs") -> int:
    """Run FMV tracking over the full clip via sliding-window sessions.

    ``prompt_mode``:
      * ``"pcs"`` (default) — SAM 3.1 Promptable Concept Segmentation.
        ``text_prompts`` defaults to ``["object"]``. One inference session
        per (window, prompt).
      * ``"yoloe"`` — YOLOE-26x-seg standalone tracker. ``text_prompts``
        non-empty → ``-seg`` checkpoint with those classes;
        ``text_prompts`` empty → ``-pf`` prompt-free checkpoint. Single
        inference session per window.

    Per-window flow:
      1. Slice source into overlapping windows (so SAM3's tracker is
         re-seeded every WINDOW_SECONDS; gives full-clip coverage on top
         of a predictor that loses targets within ~30 frames).
      2. For each window, extract a low-fps/low-res working clip with
         ffmpeg (caps VRAM at SAM3 session-init time).
      3. Call inference. For PCS, iterate prompts one-per-session
         (multiplex resets state on each text add_prompt). For YOLOE, one
         call per window covering all classes.
      4. Commit detections to PostGIS *per window*, then publish progress
         so the FmvPlayer sees boxes appear within seconds of the first
         window finishing — not 4 minutes after the whole clip processes.
    """
    provider_lifecycle.ensure_running()
    mode = (prompt_mode or "pcs").strip().lower()
    if mode not in {"pcs", "yoloe"}:
        raise ValueError(f"unknown prompt_mode {prompt_mode!r}")
    source_fps, duration_s = _probe_source(video_path)
    if duration_s <= 0:
        duration_s = FMV_TRACK_WINDOW_SECONDS
    windows = _slice_windows(duration_s)
    # `prompts` drives the per-window task fan-out. PCS fans out one
    # /detect_video session per prompt because SAM 3.1 multiplex resets
    # state on every text prompt. YOLOE handles all classes in one forward
    # pass per frame, so it collapses to a single sentinel-prompt task per
    # window and the real prompt list is forwarded via closure below.
    yoloe_prompts: list[str] = list(text_prompts or [])
    if mode == "yoloe":
        prompts = ["_yoloe"]
    else:
        prompts = list(text_prompts or [])
        if not prompts:
            prompts = list(FMV_DEFAULT_PROMPTS)

    _update_clip_tracking(
        clip_id,
        tracking_status="running",
        tracking_started_at=datetime.now(timezone.utc).isoformat(),
        tracking_windows=len(windows),
        tracking_prompts=prompts,
        tracking_count=0,
        tracking_error=None,
    )
    publish_event(
        f"fmv:{clip_id}",
        {"type": "fmv_detections_progress", "clip_id": clip_id,
         "window": 0, "windows": len(windows), "stage": "starting"},
    )

    session = requests.Session()
    try:
        health = _ensure_fmv_profile(session, clip_id)
        video_backend = (health.get("model_versions") or {}).get("sam3_video", "")
        multiplex = "multiplex" in str(video_backend).lower()
        # Bound concurrency by the inference-sam3 pool size. Each multiplex
        # replica accepts one in-flight session at a time (enforced by its
        # per-bundle lock on the server); going beyond pool_size just
        # bounces with 503 and forces us to wait, so right-size it here.
        pool_size = int(health.get("pool_size") or 1)
        inflight_cap = max(1, min(FMV_INFLIGHT_REQUESTS, pool_size))
        logger.info(
            "FMV tracking clip=%s windows=%d backend=%s multiplex=%s mode=%s "
            "pool_size=%d inflight_cap=%d",
            clip_id, len(windows), video_backend, multiplex, mode, pool_size, inflight_cap,
        )

        # Build the (window, prompt) task list. ffmpeg slicing runs
        # sequentially up front because it's cheap (~500 ms/window) and
        # parallel ffmpeg processes would just contend for disk anyway.
        # Each window appears in the task list as its (win_path,
        # window_start_frame); inference fan-out happens across the
        # cartesian product with prompts.
        sliced: list[tuple[int, int, Any]] = []  # (window_idx, window_start_frame, win_path)
        for window_idx, (start_s, length_s) in enumerate(windows):
            win_path = _prepare_tracking_window(video_path, window_idx, start_s, length_s)
            if win_path is None:
                continue
            sliced.append((window_idx, int(round(start_s * source_fps)), win_path))

        # Every window failing extraction means the source is corrupt or
        # unreadable — raise so the except path below marks the clip
        # tracking_status="failed" instead of "complete" with 0 detections.
        failed_prep_windows = len(windows) - len(sliced)
        if windows and not sliced:
            raise RuntimeError(
                f"all {len(windows)} FMV windows failed extraction for clip {clip_id} "
                "(corrupt or unreadable video?)"
            )
        if failed_prep_windows:
            _update_clip_tracking(clip_id, tracking_windows_failed=failed_prep_windows)

        tasks = [(win_idx, win_start_frame, win_path, prompt)
                 for (win_idx, win_start_frame, win_path) in sliced
                 for prompt in prompts]
        total_tasks = len(tasks)

        # Shared state guarded by `shared_lock`. `next_track_id` is the
        # rolling allocator for global track IDs, touched per-row in
        # `_insert_detection_rows`, so the lock also wraps the row
        # insertion itself (otherwise two threads race the allocator).
        shared_lock = threading.Lock()
        state = {"next_track_id": 0, "inserted": 0, "completed": 0}

        def _run_one_attempt(args: tuple[int, int, Any, str]) -> int:
            win_idx, win_start_frame, win_path, prompt = args
            if mode == "yoloe":
                # YOLOE runs one inference per window covering all classes.
                # Empty text_prompts → service uses yoloe-26x-seg-pf
                # (prompt-free); non-empty → yoloe-26x-seg with prompts.
                payload = json.dumps({
                    "video_path": str(win_path),
                    "prompt_mode": "yoloe",
                    "text_prompts": list(yoloe_prompts),
                    "frame_stride": 1,
                    "max_frames": FMV_TRACK_FRAMES_PER_WINDOW,
                    "modality": "fmv",
                })
            else:
                payload = json.dumps({
                    "video_path": str(win_path),
                    "text_prompts": [prompt],
                    "frame_stride": 1,
                    "max_frames": FMV_TRACK_FRAMES_PER_WINDOW,
                    "modality": "fmv",
                })
            # Retry transient 503s — the server returns 503 when every
            # GPU bundle is busy, so this is the natural backpressure
            # signal under fan-out. Linear backoff capped at 30 s; the
            # full session timeout still bounds the wait.
            attempt = 0
            while True:
                attempt += 1
                try:
                    resp = session.post(
                        f"{INFERENCE_SAM3_URL}/detect_video",
                        data={"metadata": payload},
                        stream=True,
                        timeout=INFERENCE_CHIP_TIMEOUT_S * 60,
                    )
                    if resp.status_code == 503 and attempt < 20:
                        resp.close()
                        time.sleep(min(0.5 + 0.5 * attempt, 5.0))
                        continue
                    resp.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt >= 5:
                        raise
                    time.sleep(min(1.0 * attempt, 5.0))
            try:
                # Drain the HTTP stream OUTSIDE the lock — this is the
                # long-running part (mirrors the GPU's per-frame emit
                # cadence). Holding the lock here would serialize every
                # task on the slowest GPU, killing the fan-out.
                entries = _drain_response_entries(resp)
            finally:
                resp.close()
            with shared_lock:
                with postgis_db.get_cursor(commit=True) as cur:
                    n, new_next = _insert_detection_rows(
                        cur, clip_id, source_fps, win_idx, win_start_frame,
                        prompt, entries, state["next_track_id"],
                    )
                    state["next_track_id"] = new_next
                    state["inserted"] += n
                    state["completed"] += 1
                    completed = state["completed"]
                    running_total = state["inserted"]
            publish_event(
                f"fmv:{clip_id}",
                {"type": "fmv_detections_progress", "clip_id": clip_id,
                 "window": win_idx + 1, "windows": len(windows),
                 "inserted": n, "total_inserted": running_total,
                 "completed_tasks": completed, "total_tasks": total_tasks,
                 "prompt": prompt},
            )
            _update_clip_tracking(clip_id, tracking_count=running_total)
            return n

        def _run_one(args: tuple[int, int, Any, str]) -> int:
            # One retry across an inference self-heal restart, mirroring the
            # imagery chip path (see decisions/why-retry-chips-across-
            # inference-restart.md): the POST retry loop above only covers
            # the request itself, not a mid-stream connection reset while
            # draining the NDJSON response.
            try:
                return _run_one_attempt(args)
            except Exception as exc:
                if not _inference_unavailable(exc):
                    raise
                logger.warning(
                    "FMV window %s prompt %r hit inference unavailability (%s); "
                    "waiting for recovery and retrying once",
                    args[0], args[3], exc,
                )
                _wait_for_inference_healthy()
                return _run_one_attempt(args)

        failed_tasks: list[tuple[int, str]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=inflight_cap) as pool:
            futures = {pool.submit(_run_one, t): t for t in tasks}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    fut.result()
                except Exception:
                    win_idx, _, _, prompt = futures[fut]
                    failed_tasks.append((win_idx, prompt))
                    logger.exception(
                        "FMV task failed for clip %s window %s prompt %r",
                        clip_id, win_idx, prompt,
                    )

        # Completed windows are already committed per window — fail the clip
        # only when the failed fraction exceeds tolerance (mirrors the imagery
        # failed-chip gate); below it, record the partial failure and let
        # consolidate_fmv run on what landed.
        if failed_tasks:
            fail_fraction = len(failed_tasks) / max(1, total_tasks)
            _update_clip_tracking(
                clip_id,
                tracking_windows_failed=failed_prep_windows + len({w for w, _ in failed_tasks}),
            )
            if fail_fraction > FMV_MAX_FAILED_TASK_FRACTION:
                raise RuntimeError(
                    f"{len(failed_tasks)}/{total_tasks} FMV inference tasks failed for "
                    f"clip {clip_id} ({fail_fraction:.1%} > "
                    f"{FMV_MAX_FAILED_TASK_FRACTION:.0%} tolerance)"
                )

        inserted = state["inserted"]

        # Consolidate the per-(window, prompt) sessions into stable
        # clip-global tracks. Runs as a separate task on the `default`
        # queue so it stays off the GPU-bound `imagery` queue and clips
        # consolidate in parallel. Dispatch failure must not fail tracking
        # — the raw detections are still usable, just fragmented.
        try:
            consolidate_fmv.delay(clip_id)
        except Exception:
            logger.exception("failed to queue worker.consolidate_fmv for clip %s", clip_id)

        _update_clip_tracking(
            clip_id,
            tracking_status="complete",
            tracking_completed_at=datetime.now(timezone.utc).isoformat(),
            tracking_count=inserted,
        )
        publish_event(
            f"fmv:{clip_id}",
            {"type": "fmv_detections_complete", "clip_id": clip_id, "count": inserted},
        )
        publish_event(
            "ops",
            {"type": "fmv_detections_complete", "clip_id": clip_id, "count": inserted},
        )
        return inserted
    except Exception as exc:
        logger.exception("FMV processing failed for clip %s", clip_id)
        message = str(exc)[:500] or exc.__class__.__name__
        _update_clip_tracking(
            clip_id,
            tracking_status="failed",
            tracking_completed_at=datetime.now(timezone.utc).isoformat(),
            tracking_error=message,
        )
        publish_event(
            f"fmv:{clip_id}",
            {"type": "fmv_detections_failed", "clip_id": clip_id, "error": message},
        )
        publish_event(
            "ops",
            {"type": "fmv_detections_failed", "clip_id": clip_id, "error": message},
        )
        raise
    finally:
        # FMV left the GPU pool on the fmv profile; return the resting state to
        # the helper's imagery_rgb default — the full imagery union OOMs
        # tight-VRAM cards (see decisions/why-revert-inference-after-fmv.md).
        _revert_inference_profile(session)
        session.close()


@celery_app.task(name="worker.consolidate_fmv", queue="default")
def consolidate_fmv(clip_id: int) -> dict:
    """Post-inference FMV track consolidation — see backend/fmv_tracker.py.

    Re-associates every ``fmv_detections`` row of a clip into stable,
    clip-global tracks, votes one canonical class per track, and
    soft-deletes cross-prompt per-frame duplicates. Idempotent, so safe to
    re-dispatch. Pure DB + numpy work — runs on the ``default`` queue.
    """
    from fmv_tracker import consolidate_fmv_tracks
    try:
        result = consolidate_fmv_tracks(clip_id, postgis_db=postgis_db)
    except Exception:
        logger.exception("FMV consolidation failed for clip %s (detections left raw)", clip_id)
        return {"clip_id": clip_id, "error": "consolidation_failed"}
    event = {"type": "fmv_detections_complete", "clip_id": clip_id,
             "count": result.get("rows_rewritten", 0), "consolidated": True}
    publish_event(f"fmv:{clip_id}", event)
    publish_event("ops", event)
    # Phase 2.B: queue the Neo4j projector so the clip + per-track nodes show
    # up in Evidence mode without operator action. Errors here log + swallow:
    # the consolidation result is already saved.
    try:
        project_fmv_to_graph.delay(clip_id)
    except Exception:
        logger.exception("failed to queue worker.project_fmv_to_graph for clip %s", clip_id)
    return result




__all__ = [n for n in dir() if not n.startswith("__")]
