"""Single source of truth label normalizer for the OSINT ontology.

Step 2 of the ontology refactor plan
(/home/avinash/.claude/plans/the-inference-system-has-piped-nest.md).

Public API:

    normalize(label, layer="") -> NormalizedLabel
    default_prompts(sensor=None) -> list[str]
    all_prompts() -> list[str]
    invalidate_cache() -> None
    get_version() -> int
    bump_version() -> int

Read-through cache: the full ontology tree is loaded on first call and
refreshed whenever ontology_version.version_id changes in the DB. Each
normalize() call does a single cheap ``SELECT version_id`` to detect
staleness, then matches against the in-memory tree.

The module never raises for normalization: unknown labels fall back to
branch_id='Other' / icon_key='circle_help' and are UPSERTed into
``ontology_unknown_labels`` for later admin review.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

from database import postgis_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------
@dataclass
class NormalizedLabel:
    branch_id: str
    parent_class: str
    canonical_label: str
    ontology_object_id: str | None
    icon_key: str
    was_unknown: bool


# ---------------------------------------------------------------------------
# Internal cache
# ---------------------------------------------------------------------------
_FALLBACK_BRANCH_ID = "Other"
_FALLBACK_ICON = "circle_help"

_CACHE_LOCK = threading.Lock()
_TREE_CACHE: dict[str, Any] = {
    "version_id": None,            # int | None
    "branches": {},                # branch_id -> branch dict
    "objects_by_id": {},           # object_id -> object dict
    "objects_by_label": {},        # canonical(label) -> object dict
    "objects_by_prompt": {},       # canonical(prompt) -> object dict
    "branch_matchers": [],         # [(order_index, branch_dict, [compiled regex])]
    "prompts_by_sensor": {},       # sensor -> [prompt, ...]  (lower)
    "all_prompts": [],             # [prompt, ...]
}


def _canonical(text: Any) -> str:
    """Lowercase + underscore-fold + collapse whitespace.

    Used uniformly on input labels, ``ontology_objects.label``, and
    ``ontology_objects.prompt`` so the SAM3 case mismatch
    ('Military_Facility' vs 'military_facility' vs 'military facility')
    collapses into a single key.
    """
    if text is None:
        return ""
    s = str(text).strip().lower()
    if not s:
        return ""
    # Convert any whitespace and dashes to underscores, then collapse runs.
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def _strip_source_prefix(text: str) -> str:
    """Drop a single ``layer:`` prefix if present (mirrors detection_policy)."""
    if ":" in text:
        head, tail = text.split(":", 1)
        if head and tail and re.fullmatch(r"[a-z0-9_]+", head):
            return tail
    return text


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _read_db_version() -> int | None:
    try:
        with postgis_db.get_cursor() as cur:
            cur.execute("SELECT version_id FROM ontology_version LIMIT 1")
            row = cur.fetchone()
            if not row:
                return None
            return int(row["version_id"])
    except Exception:
        logger.exception("ontology: failed to read version")
        return None


def _build_tree() -> dict[str, Any]:
    """Load the entire branch+object tree in two SELECTs."""
    branches: dict[str, dict[str, Any]] = {}
    objects_by_id: dict[str, dict[str, Any]] = {}
    objects_by_label: dict[str, dict[str, Any]] = {}
    objects_by_prompt: dict[str, dict[str, Any]] = {}
    branch_matchers: list[tuple[int, dict[str, Any], list[re.Pattern[str]]]] = []
    prompts_by_sensor: dict[str, list[str]] = {}
    seen_prompts: set[str] = set()
    all_prompts: list[str] = []
    version_id: int | None = None

    with postgis_db.get_cursor() as cur:
        cur.execute("SELECT version_id FROM ontology_version LIMIT 1")
        row = cur.fetchone()
        version_id = int(row["version_id"]) if row else None

        cur.execute(
            "SELECT id, parent_id, label, color, short, icon_key, "
            "       matchers, sensors, order_index "
            "FROM ontology_branches "
            "ORDER BY order_index ASC, id ASC"
        )
        for r in cur.fetchall():
            b = dict(r)
            branches[b["id"]] = b
            raw_matchers = b.get("matchers") or []
            patterns: list[re.Pattern[str]] = []
            if isinstance(raw_matchers, list):
                for raw in raw_matchers:
                    if not raw:
                        continue
                    try:
                        patterns.append(re.compile(str(raw), re.IGNORECASE))
                    except re.error:
                        logger.warning(
                            "ontology: branch %s has invalid regex %r",
                            b["id"], raw,
                        )
            branch_matchers.append((int(b.get("order_index") or 0), b, patterns))

        cur.execute(
            "SELECT id, branch_id, label, prompt, sensors, min_gsd_meters, "
            "       icon_key, order_index "
            "FROM ontology_objects "
            "ORDER BY order_index ASC, id ASC"
        )
        for r in cur.fetchall():
            o = dict(r)
            objects_by_id[o["id"]] = o

            label_key = _canonical(o.get("label"))
            if label_key and label_key not in objects_by_label:
                objects_by_label[label_key] = o
            # also key by id (often a TitleCase identifier mirroring label)
            id_key = _canonical(o.get("id"))
            if id_key and id_key not in objects_by_label:
                objects_by_label[id_key] = o

            prompt_key = _canonical(o.get("prompt"))
            if prompt_key and prompt_key not in objects_by_prompt:
                objects_by_prompt[prompt_key] = o

            prompt = (o.get("prompt") or "").strip()
            if prompt:
                if prompt not in seen_prompts:
                    seen_prompts.add(prompt)
                    all_prompts.append(prompt)
                sensors = o.get("sensors") or []
                if isinstance(sensors, list):
                    for s in sensors:
                        if not s:
                            continue
                        key = str(s).lower()
                        prompts_by_sensor.setdefault(key, [])
                        if prompt not in prompts_by_sensor[key]:
                            prompts_by_sensor[key].append(prompt)

    branch_matchers.sort(key=lambda t: (t[0], t[1].get("id") or ""))

    new_cache: dict[str, Any] = {
        "version_id": version_id,
        "branches": branches,
        "objects_by_id": objects_by_id,
        "objects_by_label": objects_by_label,
        "objects_by_prompt": objects_by_prompt,
        "branch_matchers": branch_matchers,
        "prompts_by_sensor": prompts_by_sensor,
        "all_prompts": all_prompts,
    }
    logger.info(
        "ontology: cache loaded version_id=%s branches=%d objects=%d prompts=%d",
        version_id, len(branches), len(objects_by_id), len(all_prompts),
    )
    return new_cache


def _get_tree() -> dict[str, Any]:
    """Return the cached tree, refreshing if the DB version has changed.

    If ``_read_db_version()`` returns None (transient DB error) and we
    already have a populated cache, keep serving the cache instead of
    forcing a rebuild on every call. On startup with no cache we still
    attempt the reload so the first failing call surfaces the problem.
    """
    db_version = _read_db_version()
    cache_present = bool(_TREE_CACHE.get("branches"))
    if cache_present and (db_version is None or db_version == _TREE_CACHE.get("version_id")):
        return _TREE_CACHE

    with _CACHE_LOCK:
        # Re-check after acquiring the lock.
        cache_present = bool(_TREE_CACHE.get("branches"))
        if cache_present and (db_version is None or db_version == _TREE_CACHE.get("version_id")):
            return _TREE_CACHE
        new_cache = _build_tree()
        # Atomic-ish swap of dict contents.
        _TREE_CACHE.clear()
        _TREE_CACHE.update(new_cache)
        return _TREE_CACHE


def _invalidate_cache() -> None:
    """Force the next call to reload the tree from the DB. Test hook."""
    with _CACHE_LOCK:
        _TREE_CACHE["version_id"] = None
        _TREE_CACHE["branches"] = {}


# Public alias
def invalidate_cache() -> None:
    _invalidate_cache()


# ---------------------------------------------------------------------------
# Unknown-label logging
# ---------------------------------------------------------------------------
def _log_unknown(label: str, layer: str) -> None:
    if not label:
        return
    try:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO ontology_unknown_labels (label, layer) "
                "VALUES (%s, %s) "
                "ON CONFLICT (label) DO UPDATE SET "
                "  count = ontology_unknown_labels.count + 1, "
                "  last_seen = now(), "
                "  layer = COALESCE(NULLIF(EXCLUDED.layer, ''), ontology_unknown_labels.layer)",
                (label, layer or None),
            )
    except Exception:
        logger.exception("ontology: failed to upsert unknown label %s", label)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def normalize(label: Any, layer: str = "") -> NormalizedLabel:
    """Return a :class:`NormalizedLabel` for ``label``. Never raises."""
    raw = "" if label is None else str(label)
    canon = _canonical(raw)
    canon_no_prefix = _canonical(_strip_source_prefix(canon))

    tree = _get_tree()
    objects_by_label = tree["objects_by_label"]
    objects_by_prompt = tree["objects_by_prompt"]
    branches = tree["branches"]
    branch_matchers = tree["branch_matchers"]

    if canon:
        # 1) Exact match on object label/id.
        for key in (canon, canon_no_prefix):
            if not key:
                continue
            obj = objects_by_label.get(key)
            if obj is not None:
                pc = _canonical(obj.get("label")) or key
                icon = obj.get("icon_key") or _branch_icon(branches, obj.get("branch_id"))
                logger.debug(
                    "ontology.normalize: label=%r matched object=%s branch=%s",
                    raw, obj.get("id"), obj.get("branch_id"),
                )
                return NormalizedLabel(
                    branch_id=obj.get("branch_id") or _FALLBACK_BRANCH_ID,
                    parent_class=pc,
                    canonical_label=str(obj.get("label") or raw),
                    ontology_object_id=obj.get("id"),
                    icon_key=icon or _FALLBACK_ICON,
                    was_unknown=False,
                )

        # 2) Exact match on object prompt.
        for key in (canon, canon_no_prefix):
            if not key:
                continue
            obj = objects_by_prompt.get(key)
            if obj is not None:
                pc = _canonical(obj.get("label")) or key
                icon = obj.get("icon_key") or _branch_icon(branches, obj.get("branch_id"))
                logger.debug(
                    "ontology.normalize: label=%r matched prompt object=%s branch=%s",
                    raw, obj.get("id"), obj.get("branch_id"),
                )
                return NormalizedLabel(
                    branch_id=obj.get("branch_id") or _FALLBACK_BRANCH_ID,
                    parent_class=pc,
                    canonical_label=str(obj.get("label") or raw),
                    ontology_object_id=obj.get("id"),
                    icon_key=icon or _FALLBACK_ICON,
                    was_unknown=False,
                )

        # 3) Branch matcher regex (in order_index ASC).
        # Run against the "human-readable" form: underscores -> spaces.
        candidates = {
            canon.replace("_", " "),
            canon_no_prefix.replace("_", " "),
            canon,
            canon_no_prefix,
        }
        for _order, branch, patterns in branch_matchers:
            if not patterns:
                continue
            for pat in patterns:
                if any(c and pat.search(c) for c in candidates):
                    icon = branch.get("icon_key") or _FALLBACK_ICON
                    pc = _canonical(branch.get("label")) or _canonical(branch.get("id"))
                    logger.debug(
                        "ontology.normalize: label=%r matched branch matcher=%s",
                        raw, branch.get("id"),
                    )
                    return NormalizedLabel(
                        branch_id=branch.get("id") or _FALLBACK_BRANCH_ID,
                        parent_class=pc or "unknown",
                        canonical_label=raw or branch.get("label") or "",
                        ontology_object_id=None,
                        icon_key=icon,
                        was_unknown=False,
                    )

    # 4) Fallback. Log only non-empty inputs.
    if canon:
        logger.warning(
            "ontology.normalize: unknown label=%r layer=%r -> Other",
            raw, layer,
        )
        _log_unknown(canon, layer or "")
    # Phase 6.24: keep semantic richness for unknown-but-novel labels.
    # Previously parent_class collapsed to "unknown" and canonical_label
    # echoed the raw input; the UI then rendered every novel detection as
    # a generic "unknown" pill, hiding the actual SAM3 prompt the model
    # found. Now parent_class falls back to the cleaned canonical form so
    # an "armored personnel carrier" detection that doesn't yet exist in
    # the DB ontology still renders with its meaningful label in the UI.
    # ``was_unknown`` stays True so the suppression banner / admin
    # taxonomy queue still flag it for ontology curation.
    fallback_parent = canon_no_prefix or canon or "unknown"
    fallback_canonical = (raw or fallback_parent).strip() or fallback_parent
    return NormalizedLabel(
        branch_id=_FALLBACK_BRANCH_ID,
        parent_class=fallback_parent,
        canonical_label=fallback_canonical,
        ontology_object_id=None,
        icon_key=_FALLBACK_ICON,
        was_unknown=True,
    )


def _branch_icon(branches: dict[str, dict[str, Any]], branch_id: Any) -> str | None:
    if not branch_id:
        return None
    b = branches.get(branch_id)
    if not b:
        return None
    return b.get("icon_key")


def default_prompts(sensor: str | None = None) -> list[str]:
    """Return distinct prompts for objects whose ``sensors`` array contains ``sensor``.

    If ``sensor`` is None or empty, return ALL distinct prompts.
    """
    tree = _get_tree()
    if not sensor:
        return list(tree.get("all_prompts") or [])
    by_sensor = tree.get("prompts_by_sensor") or {}
    return list(by_sensor.get(str(sensor).lower(), []))


def all_prompts() -> list[str]:
    """Convenience alias for ``default_prompts(None)``."""
    return default_prompts(None)


# ─── Branch / object row helpers (hoisted from main.py) ───────────────────

def _branch_row_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "parent_id": row.get("parent_id"),
        "label": row.get("label"),
        "color": row.get("color"),
        "short": row.get("short"),
        "icon_key": row.get("icon_key"),
        "matchers": row.get("matchers") or [],
        "sensors": row.get("sensors") or [],
        "order_index": int(row.get("order_index") or 0),
    }


def _object_row_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "branch_id": row.get("branch_id"),
        "label": row.get("label"),
        "prompt": row.get("prompt"),
        "sensors": row.get("sensors") or [],
        "min_gsd_meters": (float(row["min_gsd_meters"]) if row.get("min_gsd_meters") is not None else None),
        "icon_key": row.get("icon_key"),
        "order_index": int(row.get("order_index") or 0),
    }


def _filter_object_by_sensor(obj: dict, sensor: Any) -> bool:
    if not sensor:
        return True
    sensors = obj.get("sensors") or []
    if not isinstance(sensors, list):
        return False
    return str(sensor).lower() in {str(s).lower() for s in sensors if s}


def _filter_branch_by_sensor(branch: dict, sensor: Any) -> bool:
    if not sensor:
        return True
    sensors = branch.get("sensors") or []
    if not isinstance(sensors, list) or not sensors:
        return True  # sensor-agnostic branch
    return str(sensor).lower() in {str(s).lower() for s in sensors if s}


def get_version() -> int:
    """Return the current ``ontology_version.version_id`` (0 if missing)."""
    v = _read_db_version()
    return int(v) if v is not None else 0


def bump_version(summary: str | None = None, changes: dict | None = None, by: str | None = None) -> int:
    """Atomically increment ``ontology_version.version_id`` and return the new value.

    When ``summary`` or ``changes`` is provided, append a row to
    ``ontology_version_history`` so the Admin · Taxonomy version-history tab
    has an audit trail. The history table is intentionally append-only and is
    written best-effort — a failure to log shouldn't abort the bump itself.
    """
    import json as _json
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO ontology_version (singleton, version_id, updated_at) "
            "VALUES (TRUE, 1, now()) "
            "ON CONFLICT (singleton) DO UPDATE "
            "SET version_id = ontology_version.version_id + 1, updated_at = now() "
            "RETURNING version_id"
        )
        row = cur.fetchone()
        new_id = int(row["version_id"])
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ontology_version_history (
                    id                 BIGSERIAL PRIMARY KEY,
                    version_id         BIGINT NOT NULL,
                    summary            TEXT,
                    changes            JSONB NOT NULL DEFAULT '{}'::jsonb,
                    detections_at_cut  BIGINT,
                    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_by         TEXT
                )
                """
            )
            cur.execute(
                "SELECT count(*) AS c FROM detections WHERE deleted_at IS NULL"
            )
            cnt_row = cur.fetchone()
            detections_at_cut = int(cnt_row["c"]) if cnt_row else 0
            cur.execute(
                """
                INSERT INTO ontology_version_history
                  (version_id, summary, changes, detections_at_cut, created_by)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                """,
                (
                    new_id,
                    summary or "",
                    _json.dumps(changes or {}, default=str),
                    detections_at_cut,
                    by or "system",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ontology version history write failed: %s", exc)
    invalidate_cache()
    logger.info("ontology: version bumped to %d", new_id)
    return new_id


# ─── Aliases for callers that prefer the prefixed name ────────────────────
ontology_bump_version = bump_version
ontology_default_prompts = default_prompts
ontology_get_version = get_version
