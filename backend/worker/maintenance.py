"""Periodic + admin tasks: audio transcribe, training, scheduler/feed ticks,
observation cleanup, reference-DB bake."""

from worker.config import *  # noqa: F401,F403
from worker.app import celery_app  # noqa: F401
from events import publish_event

@celery_app.task(name="worker.transcribe_audio", queue="default")
def transcribe_audio(document_id: int, audio_path: str) -> dict:
    if os.getenv("WHISPER_ENABLED", "0") != "1":
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE transcripts SET status='skipped', text=%s WHERE document_id=%s",
                ("Transcription disabled: set WHISPER_ENABLED=1.", document_id),
            )
        return {"status": "skipped"}
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE transcripts SET status='failed', text=%s WHERE document_id=%s",
                (f"faster-whisper not installed: {exc}", document_id),
            )
        return {"status": "failed", "error": str(exc)}

    model_size = os.getenv("WHISPER_MODEL", "base")
    device = os.getenv("WHISPER_DEVICE", "auto")
    try:
        model = WhisperModel(model_size, device=device, compute_type="int8")
        segments_iter, info = model.transcribe(audio_path)
        segments = []
        full_text_parts = []
        for seg in segments_iter:
            segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
            full_text_parts.append(seg.text)
        full_text = "".join(full_text_parts).strip() or "(empty audio)"
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE transcripts SET
                  text=%s, status='ready', confidence=%s, language=%s, segments=%s
                WHERE document_id=%s
                """,
                (full_text, 1.0, info.language or "unknown", json.dumps(segments), document_id),
            )
        publish_event(
            "ops",
            {"type": "transcript_ready", "document_id": document_id, "language": info.language},
        )
        return {"status": "ready", "language": info.language, "segments": len(segments)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("transcription failed for document %s", document_id)
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE transcripts SET status='failed', text=%s WHERE document_id=%s",
                (f"Transcription failed: {exc}", document_id),
            )
        return {"status": "failed", "error": str(exc)}


# ============================================================================
# Training — invokes a real training entrypoint at backend/scripts/train.py.
# If no GPU/profile is detected, the task fails the job rather than silently
# pretending it succeeded.
# ============================================================================


@celery_app.task(name="worker.train_model", queue="default")
def train_model(job_id: int) -> dict:
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT id, name, dataset_path, epochs, status, metrics FROM training_jobs WHERE id=%s",
            (job_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"status": "missing"}
    job = dict(row)
    gpu = os.getenv("SAM3_GPU_PROFILE") or os.getenv("CUDA_VISIBLE_DEVICES")
    if not gpu:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE training_jobs SET status='failed', metrics = metrics || %s::jsonb WHERE id=%s",
                (json.dumps({"error": "no GPU profile"}), job_id),
            )
        return {"status": "failed", "error": "no GPU profile"}

    train_script = Path(__file__).resolve().parent / "scripts" / "train.py"
    if not train_script.exists():
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE training_jobs SET status='failed', metrics = metrics || %s::jsonb WHERE id=%s",
                (json.dumps({"error": "scripts/train.py not present"}), job_id),
            )
        return {"status": "failed", "error": "scripts/train.py missing"}

    cmd = [
        "python", str(train_script),
        "--job", str(job_id),
        "--dataset", str(job.get("dataset_path") or ""),
        "--epochs", str(int(job.get("epochs") or 1)),
        "--out", str(Path(os.getenv("MODEL_OUT_DIR", "/data/models")) / f"job-{job_id}"),
    ]
    # Opt-in chip-aligned tiling: cut training tiles with the same planner
    # inference uses so train/inference pixel distributions match. Off unless
    # the job was queued with metrics.tile truthy — default behaviour unchanged.
    job_metrics = job.get("metrics") or {}
    if isinstance(job_metrics, str):
        try:
            job_metrics = json.loads(job_metrics)
        except (ValueError, TypeError):
            job_metrics = {}
    if job_metrics.get("tile"):
        cmd.append("--tile")
        if job_metrics.get("chip_size"):
            cmd += ["--chip-size", str(int(job_metrics["chip_size"]))]
        if job_metrics.get("overlap"):
            cmd += ["--overlap", str(int(job_metrics["overlap"]))]
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("UPDATE training_jobs SET status='running' WHERE id=%s", (job_id,))
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        ok = completed.returncode == 0
        metrics = {
            "stdout_tail": (completed.stdout or "")[-2000:],
            "stderr_tail": (completed.stderr or "")[-2000:],
            "return_code": completed.returncode,
        }
        status = "done" if ok else "failed"
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE training_jobs SET status=%s, metrics = metrics || %s::jsonb WHERE id=%s",
                (status, json.dumps(metrics), job_id),
            )
        publish_event("ops", {"type": "training_finished", "job_id": job_id, "status": status})
        return {"status": status, **metrics}
    except Exception as exc:  # noqa: BLE001
        logger.exception("train_model failed for job %s", job_id)
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE training_jobs SET status='failed', metrics = metrics || %s::jsonb WHERE id=%s",
                (json.dumps({"error": str(exc)}), job_id),
            )
        return {"status": "failed", "error": str(exc)}


# ============================================================================
# Beat-driven housekeeping: collection-task scheduler and feed pollers.
# ============================================================================


COLLECTION_TASK_TTL_HOURS = env_int("COLLECTION_TASK_TTL_HOURS", 72)


@celery_app.task(name="worker.tick_collection_scheduler", queue="default")
def tick_collection_scheduler() -> dict:
    """Transition proposed→scheduled and scheduled→expired based on age + priority."""
    from platform_schema import ensure_collection_tables
    ensure_collection_tables()
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE collection_tasks
            SET status = 'scheduled',
                scheduled_for = NOW() + (
                    CASE lower(coalesce(priority, ''))
                        WHEN 'high'   THEN INTERVAL '1 hour'
                        WHEN 'medium' THEN INTERVAL '6 hours'
                        WHEN 'low'    THEN INTERVAL '24 hours'
                        ELSE               INTERVAL '6 hours'
                    END
                ),
                updated_at = NOW()
            WHERE status = 'proposed'
            RETURNING id
            """
        )
        scheduled_ids = [int(r["id"]) for r in cur.fetchall()]

        cur.execute(
            """
            UPDATE collection_tasks
            SET status = 'expired', updated_at = NOW()
            WHERE status = 'scheduled'
              AND created_at < NOW() - (%s || ' hours')::interval
            RETURNING id
            """,
            (COLLECTION_TASK_TTL_HOURS,),
        )
        expired_ids = [int(r["id"]) for r in cur.fetchall()]

    if scheduled_ids or expired_ids:
        publish_event("ops", {
            "type": "collection_tasks_ticked",
            "scheduled": scheduled_ids,
            "expired": expired_ids,
        })
    return {"scheduled": len(scheduled_ids), "expired": len(expired_ids)}


@celery_app.task(name="worker.tick_feed_poll", queue="default")
def tick_feed_poll() -> dict:
    """Poll all enabled HTTP/HTTPS feed_sources whose poll interval has elapsed."""
    from feed_collectors import poll_http_feed
    from platform_schema import ensure_feed_tables
    ensure_feed_tables()
    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, feed_type, protocol, endpoint, parser, metadata,
                   poll_interval_seconds, last_seen
            FROM feed_sources
            WHERE enabled = TRUE
              AND lower(protocol) IN ('http', 'https')
              AND (
                last_seen IS NULL
                OR last_seen < NOW() - (coalesce(poll_interval_seconds, 60) || ' seconds')::interval
              )
            ORDER BY coalesce(last_seen, '1970-01-01'::timestamptz) ASC
            LIMIT 20
            """
        )
        due = [dict(r) for r in cur.fetchall()]

    polled = 0
    total_events = 0
    for source in due:
        try:
            events = poll_http_feed(source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("feed poll failed for %s: %s", source.get("name"), exc)
            with postgis_db.get_cursor(commit=True) as cur:
                cur.execute(
                    """
                    UPDATE feed_sources
                    SET status = 'error', last_error = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (str(exc)[:1000], source["id"]),
                )
            continue
        if events:
            with postgis_db.get_cursor(commit=True) as cur:
                for evt in events:
                    lat = evt.get("latitude")
                    lon = evt.get("longitude")
                    if lat is not None and lon is not None:
                        cur.execute(
                            """
                            INSERT INTO feed_events (source_id, event_type, payload, geom, observed_at)
                            VALUES (%s, %s, %s::jsonb, ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                                    COALESCE(%s::timestamptz, NOW()))
                            """,
                            (
                                source["id"],
                                evt.get("event_type", "observation"),
                                json.dumps(evt.get("payload") or {}, default=str),
                                lon, lat,
                                evt.get("observed_at"),
                            ),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO feed_events (source_id, event_type, payload, observed_at)
                            VALUES (%s, %s, %s::jsonb, COALESCE(%s::timestamptz, NOW()))
                            """,
                            (
                                source["id"],
                                evt.get("event_type", "observation"),
                                json.dumps(evt.get("payload") or {}, default=str),
                                evt.get("observed_at"),
                            ),
                        )
                total_events += len(events)
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE feed_sources
                SET status = 'connected', last_seen = NOW(), last_error = NULL, updated_at = NOW()
                WHERE id = %s
                """,
                (source["id"],),
            )
        polled += 1

    if total_events:
        publish_event("feeds", {"type": "feed_events_collected", "polled": polled, "events": total_events})
    return {"polled": polled, "events": total_events, "due": len(due)}


OBSERVATION_RETENTION_DAYS = max(1, env_int("OBSERVATION_RETENTION_DAYS", 30))


@celery_app.task(name="worker.cleanup_old_observations", queue="default")
def cleanup_old_observations() -> dict:
    """Hourly beat task: prune ``observations`` and ``timeline_events`` rows
    older than ``OBSERVATION_RETENTION_DAYS`` (default 30).

    Both tables are append-only (feed pollers, ingest pipelines, timeline
    recorder) and were documented as pruned hourly, but the task never
    existed — on an always-on deployment they grow unbounded. Retention is
    keyed on each table's event-time column (``observed_at`` /
    ``occurred_at``) so late-ingested rows age out by when they happened.
    """
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM observations WHERE observed_at < NOW() - (%s || ' days')::interval",
            (OBSERVATION_RETENTION_DAYS,),
        )
        observations_deleted = cur.rowcount or 0
        cur.execute(
            "DELETE FROM timeline_events WHERE occurred_at < NOW() - (%s || ' days')::interval",
            (OBSERVATION_RETENTION_DAYS,),
        )
        timeline_deleted = cur.rowcount or 0
    if observations_deleted or timeline_deleted:
        logger.info(
            "cleanup_old_observations: pruned %d observations + %d timeline_events older than %d days",
            observations_deleted, timeline_deleted, OBSERVATION_RETENTION_DAYS,
        )
    return {
        "observations_deleted": observations_deleted,
        "timeline_events_deleted": timeline_deleted,
        "retention_days": OBSERVATION_RETENTION_DAYS,
    }


# ---------------------------------------------------------------------------
# Reference Embedding DB — seed from baked corpora
# ---------------------------------------------------------------------------
#
# Triggered:
#   - Automatically by backend lifespan when `reference_platforms` is empty
#     (see `auto_enqueue_reference_seed_if_empty` in platform_schema.py).
#   - Manually via POST /api/admin/reference/seed.
#
# Reads from /opt/reference-corpora/ (volume populated by the assets image)
# and runs the existing bake pipeline (backend/scripts/bake_reference_index.py)
# per dataset. Idempotent — the bake's (platform_id, chip_path) unique index
# handles re-runs.

_REFERENCE_CORPORA_ROOT = Path(os.getenv("REFERENCE_CORPORA_ROOT", "/opt/reference-corpora"))
_REFERENCE_CHIPS_RUNTIME_ROOT = Path(
    os.getenv("REFERENCE_CHIPS_RUNTIME_ROOT", "/data/datasets/reference-chips")
)
_REFERENCE_SEED_PATH = Path(
    os.getenv("REFERENCE_SEED_PATH", "/app/scripts/seeds/reference_platforms.seed.json")
)


def _list_baked_datasets() -> list[Path]:
    """Discover which dataset trees exist under /opt/reference-corpora/."""
    if not _REFERENCE_CORPORA_ROOT.is_dir():
        return []
    out = []
    for d in sorted(_REFERENCE_CORPORA_ROOT.iterdir()):
        if not d.is_dir():
            continue
        # Skip control files / marker files at the root.
        if d.name.startswith(".") or d.name.startswith("_"):
            continue
        if (d / "MANIFEST.json").is_file():
            out.append(d)
    return out


def _read_dataset_manifest(dataset_dir: Path) -> dict:
    try:
        return json.loads((dataset_dir / "MANIFEST.json").read_text())
    except Exception:
        return {}


def _rsync_dataset(src: Path, dst: Path) -> int:
    """Mirror src → dst at file level (no `rsync` binary dep). Returns chip count."""
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for cls_dir in src.iterdir():
        if not cls_dir.is_dir() or cls_dir.name.startswith("."):
            continue
        out_cls = dst / cls_dir.name
        out_cls.mkdir(parents=True, exist_ok=True)
        for chip in cls_dir.iterdir():
            if chip.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            target = out_cls / chip.name
            # Skip if target already up-to-date (size + mtime heuristic — same
            # check rsync uses by default). The bake's unique index makes
            # over-copying safe; this just avoids redundant disk I/O.
            try:
                if target.exists() and target.stat().st_size == chip.stat().st_size:
                    count += 1
                    continue
            except OSError:
                pass
            try:
                import shutil
                shutil.copy2(chip, target)
                count += 1
            except OSError as exc:
                logger.warning("seed_reference_db: copy %s failed: %s", chip, exc)
    return count


@celery_app.task(name="worker.seed_reference_db", queue="default", bind=True)
def seed_reference_db(self, force: bool = False, only: Optional[list] = None) -> dict:
    """Bake reference_platforms + reference_chips from /opt/reference-corpora/.

    Iterates every dataset present in the baked corpora tree, copies its
    chips into the writable /data/datasets/reference-chips/ volume, then
    invokes ``bake_reference_index.run()`` per dataset. WS progress events
    fire to the ``reference-seed`` topic.

    Args:
        force: If False (default) and reference_platforms already has rows,
            short-circuits to a no-op. Pass force=True from the admin
            re-seed endpoint to force a re-bake (existing rows get UPSERTed,
            new chips inserted, missing chips left alone).
        only: Optional list[str] of dataset names to limit. None = all.

    Returns the per-dataset totals dict.
    """
    # Idempotency guard.
    if not force:
        with postgis_db.get_cursor() as cur:
            cur.execute("SELECT count(*) FROM reference_platforms")
            row = cur.fetchone()
            n_existing = int(row["count"] if isinstance(row, dict) else row[0])
        if n_existing > 0:
            logger.info("seed_reference_db: %d platforms present, force=false — no-op", n_existing)
            # The admin UI's Seed button listens on this topic; without a
            # terminal event the skipped path left it waiting forever.
            publish_event("reference-seed", {
                "type": "done",
                "skipped": True,
                "platforms_present": n_existing,
                "totals": {"platforms": 0, "chips": 0},
                "task_id": self.request.id,
            })
            return {"status": "skipped", "platforms_present": n_existing}

    only_set = set(only or [])
    datasets = _list_baked_datasets()
    if only_set:
        datasets = [d for d in datasets if d.name in only_set]
    dataset_names = [d.name for d in datasets]

    publish_event("reference-seed", {
        "type": "started",
        "datasets": dataset_names,
        "force": bool(force),
        "task_id": self.request.id,
    })

    if not datasets:
        publish_event("reference-seed", {
            "type": "done",
            "datasets": [],
            "totals": {"platforms": 0, "chips": 0},
            "detail": "no baked corpora present at /opt/reference-corpora/",
        })
        return {"status": "empty", "datasets": []}

    # Late import — keeps the worker startup light and lets tests stub.
    sys.path.insert(0, "/app/scripts")
    try:
        from bake_reference_index import run as bake_run  # type: ignore
    finally:
        sys.path.pop(0)

    totals = {"platforms": 0, "chips": 0, "datasets": []}
    for dataset_dir in datasets:
        ds = dataset_dir.name
        manifest = _read_dataset_manifest(dataset_dir)
        # The license_spdx for the bake comes from the MANIFEST default — per-chip
        # overrides land in reference_chips.license_spdx during the bake's INSERT.
        license_spdx = "see-source-terms"
        if manifest.get("chips"):
            license_spdx = manifest["chips"][0].get("license_spdx") or license_spdx

        # Step 1: rsync into the writable runtime volume.
        runtime_dataset = _REFERENCE_CHIPS_RUNTIME_ROOT / ds
        try:
            copied = _rsync_dataset(dataset_dir, runtime_dataset)
        except Exception as exc:
            logger.exception("seed_reference_db: rsync %s failed", ds)
            publish_event("reference-seed", {
                "type": "error", "dataset": ds, "detail": f"rsync failed: {exc}",
            })
            continue

        # Step 2: run the bake.
        try:
            result = bake_run(
                seed_path=str(_REFERENCE_SEED_PATH),
                dataset=ds,
                dataset_root=str(runtime_dataset),
                license_spdx=license_spdx,
                max_chips_per_class=int(os.getenv("REFERENCE_MAX_CHIPS_PER_CLASS", "50")),
            )
        except Exception as exc:
            logger.exception("seed_reference_db: bake %s failed", ds)
            publish_event("reference-seed", {
                "type": "error", "dataset": ds, "detail": f"bake failed: {exc}",
            })
            continue

        platforms = int(result.get("platforms", 0))
        chips = int(result.get("chips", 0))
        totals["platforms"] += platforms
        totals["chips"] += chips
        totals["datasets"].append({
            "dataset": ds, "platforms": platforms, "chips": chips,
            "chips_copied": copied, "license_spdx": license_spdx,
        })

        publish_event("reference-seed", {
            "type": "dataset_progress",
            "dataset": ds,
            "platforms": platforms,
            "chips": chips,
            "chips_copied": copied,
        })

    publish_event("reference-seed", {
        "type": "done",
        "totals": totals,
        "task_id": self.request.id,
    })
    return {"status": "ok", **totals}


__all__ = [n for n in dir() if not n.startswith("__")]
