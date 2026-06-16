"""Cross-chip NMS / dedupe / weighted-box fusion + geo re-derivation."""

from worker.config import *  # noqa: F401,F403
from geometry import iou_xyxy as bbox_iou

def polygon_iou(a: list[float], b: list[float]) -> float:
    if len(a) != 8 or len(b) != 8:
        return 0.0
    try:
        poly_a = Polygon(list(zip(a[0::2], a[1::2]))).buffer(0)
        poly_b = Polygon(list(zip(b[0::2], b[1::2]))).buffer(0)
        if poly_a.is_empty or poly_b.is_empty or not poly_a.is_valid or not poly_b.is_valid:
            return 0.0
        inter_area = poly_a.intersection(poly_b).area
        if inter_area <= 0:
            return 0.0
        union_area = poly_a.union(poly_b).area
        return float(inter_area / union_area) if union_area else 0.0
    except Exception:
        return 0.0


def detection_overlap(a: dict, b: dict) -> float:
    obb_iou = polygon_iou(a.get("pixel_obb", []), b.get("pixel_obb", []))
    if obb_iou > 0:
        return obb_iou
    return bbox_iou(a.get("pixel_bbox", []), b.get("pixel_bbox", []))


def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _load_per_class_iou_thresholds() -> dict[str, float]:
    """Phase 2.7: per-class NMS IoU floors.

    A single global 0.45 threshold over-suppresses dense small objects (dense
    truck convoys) and under-suppresses overlapping large structures
    (hangars, terminals). This map lets the operator set tighter / looser
    thresholds per parent_class via env (``PER_CLASS_NMS_IOU_OVERRIDES``,
    JSON dict) or via the DB ``inference_config`` row. Falls back to the
    global default when no class-specific value exists.
    """
    raw_env = (os.getenv("PER_CLASS_NMS_IOU_OVERRIDES") or "").strip()
    out: dict[str, float] = {}
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    try:
                        out[str(key).strip().lower()] = max(0.0, min(1.0, float(value)))
                    except (TypeError, ValueError):
                        continue
        except json.JSONDecodeError:
            logger.warning("PER_CLASS_NMS_IOU_OVERRIDES is not valid JSON; ignoring")
    return out


_PER_CLASS_IOU_THRESHOLDS: dict[str, float] = _load_per_class_iou_thresholds()


def _load_per_model_trust_weights() -> dict[str, float]:
    """Phase 2.8: per-model trust weights.

    Multiplies the detection's confidence at NMS-comparison time so a tuned
    DOTA-OBB output isn't drowned out by an over-confident SAM3 mask score.
    Env: ``PER_MODEL_TRUST_WEIGHTS`` JSON dict keyed by ``source_layer`` /
    ``model_version`` substring (case-insensitive). Unrecognised models keep
    weight 1.0.
    """
    raw_env = (os.getenv("PER_MODEL_TRUST_WEIGHTS") or "").strip()
    out: dict[str, float] = {}
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    try:
                        out[str(key).strip().lower()] = max(0.0, float(value))
                    except (TypeError, ValueError):
                        continue
        except json.JSONDecodeError:
            logger.warning("PER_MODEL_TRUST_WEIGHTS is not valid JSON; ignoring")
    return out


_PER_MODEL_TRUST_WEIGHTS: dict[str, float] = _load_per_model_trust_weights()


def _trust_weight_for(det: dict) -> float:
    if not _PER_MODEL_TRUST_WEIGHTS:
        return 1.0
    for tag in (det.get("source_layer"), det.get("model_version"), det.get("parent_class"), det.get("class")):
        if not tag:
            continue
        key = str(tag).strip().lower()
        if key in _PER_MODEL_TRUST_WEIGHTS:
            return _PER_MODEL_TRUST_WEIGHTS[key]
        # Allow substring match (e.g. "dota_obb" in "dota_obb:v1.2")
        for src_key, weight in _PER_MODEL_TRUST_WEIGHTS.items():
            if src_key and src_key in key:
                return weight
    return 1.0


def _calibration_tag_for_detection(det: dict) -> str:
    """Use detector provenance for calibration, not the broad model bundle id."""
    return str(det.get("source_layer") or "")


class _DetectionDedupeIndex:
    """Incremental NMS with the same IoU+bucket algorithm as the old
    deduplicate_detections, but with state that persists across chip
    boundaries — so slice_and_infer can dedupe and store survivors as each
    chip completes (instead of one giant batch at the very end of inference).

    Phase 2.7/2.8: IoU thresholds are now per-class, and the sort key
    incorporates per-model trust weights so a tuned specialist isn't
    drowned out by a loud generalist."""

    BUCKET_SIZE = 512

    def __init__(self, iou_threshold: float = 0.45) -> None:
        self.iou_threshold = iou_threshold
        self.buckets: dict[tuple[str, int, int], list[dict]] = {}
        self.raw_seen = 0
        self.kept_count = 0

    def _iou_for_class(self, det_class: str | None, modality: str | None = None) -> float:
        """Per-class IoU floor. Phase 5.22: SAR detections are point-like and
        speckle-driven, so a tighter default (0.25 vs 0.45 optical) suppresses
        the long tail of weak overlapping detections that flood the SAR
        output. The per-class override map still wins when a class is listed.
        """
        if det_class:
            override = _PER_CLASS_IOU_THRESHOLDS.get(str(det_class).strip().lower())
            if override is not None:
                return override
        if (modality or "").strip().lower() == "sar":
            try:
                return float(os.getenv("SAR_NMS_IOU_DEFAULT", "0.25"))
            except ValueError:
                return 0.25
        return self.iou_threshold

    def add(self, detections: list) -> list:
        """Run the new batch through NMS against the running index.

        Returns the list of survivors (mutated state). The batch is sorted by
        ``trust_weight * confidence`` so a high-trust specialist suppresses a
        lower-trust generalist when they overlap — matching the principle
        WBF will eventually replace this with, while preserving the simple
        NMS contract for now."""
        if not detections:
            return []

        def _sort_key(item: dict) -> float:
            try:
                conf = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            return _trust_weight_for(item) * conf

        survivors: list[dict] = []
        for det in sorted(detections, key=_sort_key, reverse=True):
            self.raw_seen += 1
            if not det.get("pixel_bbox"):
                det.setdefault("dedupe_method", "obb_nms")
                survivors.append(det)
                self.kept_count += 1
                continue

            x1, y1, x2, y2 = det["pixel_bbox"]
            cx = int(((x1 + x2) / 2) // self.BUCKET_SIZE)
            cy = int(((y1 + y2) / 2) // self.BUCKET_SIZE)
            det_class = det.get("parent_class") or det.get("class")
            iou_for_class = self._iou_for_class(det_class, det.get("modality"))
            suppressed = False
            for dx in (-1, 0, 1):
                if suppressed:
                    break
                for dy in (-1, 0, 1):
                    for existing in self.buckets.get((det_class, cx + dx, cy + dy), ()):
                        if detection_overlap(det, existing) >= iou_for_class:
                            suppressed = True
                            break
                    if suppressed:
                        break
            if suppressed:
                continue

            det.setdefault("dedupe_method", "obb_nms")
            self.buckets.setdefault((det_class, cx, cy), []).append(det)
            survivors.append(det)
            self.kept_count += 1
        return survivors

    def reconcile_edge_truncated(self, survivors: list[dict]) -> tuple[list[dict], int]:
        """Phase 3.12: cross-chip edge reconciliation.

        After every chip has finished and the global NMS has run, some
        ``edge_truncated`` detections still survive because their per-chip
        bbox didn't IoU-overlap the matching detection from the adjacent
        chip — each saw a different half of the object. This second pass
        scans each edge_truncated survivor against neighbours in the same
        class within 1 spatial bucket (so cross-chip pairs land in the same
        comparison window). When a pair is found whose pixel-bbox union
        forms a meaningful continuation, we keep the higher-confidence
        survivor as a ``reconciled`` detection with the union bbox and
        drop the lower-confidence half. The merged detection is flagged
        ``dedupe_method="edge_reconciled"`` so provenance can show that
        an edge stitching happened.

        Returns ``(reconciled_survivors, merge_count)``.
        """
        if not survivors:
            return survivors, 0
        truncated = [det for det in survivors if det.get("edge_truncated")]
        if len(truncated) < 2:
            return survivors, 0
        # Pre-bucket by (class, bucket_cx, bucket_cy) for cheap neighbour lookup.
        buckets: dict[tuple[str, int, int], list[dict]] = {}
        for det in truncated:
            bb = det.get("pixel_bbox") or []
            if len(bb) < 4:
                continue
            x1, y1, x2, y2 = bb[:4]
            cx = int(((x1 + x2) / 2) // self.BUCKET_SIZE)
            cy = int(((y1 + y2) / 2) // self.BUCKET_SIZE)
            det_class = det.get("parent_class") or det.get("class")
            buckets.setdefault((det_class, cx, cy), []).append(det)
        suppressed_ids: set[int] = set()
        merges = 0
        for det in truncated:
            if id(det) in suppressed_ids:
                continue
            bb = det.get("pixel_bbox") or []
            if len(bb) < 4:
                continue
            x1, y1, x2, y2 = bb[:4]
            cx = int(((x1 + x2) / 2) // self.BUCKET_SIZE)
            cy = int(((y1 + y2) / 2) // self.BUCKET_SIZE)
            det_class = det.get("parent_class") or det.get("class")
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other in buckets.get((det_class, cx + dx, cy + dy), ()):
                        if other is det or id(other) in suppressed_ids:
                            continue
                        obb = other.get("pixel_bbox") or []
                        if len(obb) < 4:
                            continue
                        ox1, oy1, ox2, oy2 = obb[:4]
                        # Centroids close OR bboxes adjacent / overlapping.
                        det_cx = (x1 + x2) / 2
                        det_cy = (y1 + y2) / 2
                        other_cx = (ox1 + ox2) / 2
                        other_cy = (oy1 + oy2) / 2
                        d = math.hypot(det_cx - other_cx, det_cy - other_cy)
                        if d > max((x2 - x1), (y2 - y1)) + max((ox2 - ox1), (oy2 - oy1)):
                            continue  # too far apart
                        # Pick the higher-confidence detection as the
                        # survivor; expand its bbox to the union of both.
                        det_conf = float(det.get("confidence") or 0.0)
                        other_conf = float(other.get("confidence") or 0.0)
                        winner, loser = (det, other) if det_conf >= other_conf else (other, det)
                        winner["pixel_bbox"] = [
                            min(x1, ox1), min(y1, oy1),
                            max(x2, ox2), max(y2, oy2),
                        ]
                        winner["dedupe_method"] = "edge_reconciled"
                        winner["edge_truncated"] = False  # union is no longer partial
                        suppressed_ids.add(id(loser))
                        merges += 1
                        break
                    if id(det) in suppressed_ids:
                        break
                if id(det) in suppressed_ids:
                    break
        if not suppressed_ids:
            return survivors, 0
        reconciled = [det for det in survivors if id(det) not in suppressed_ids]
        self.kept_count = max(0, self.kept_count - len(suppressed_ids))
        return reconciled, merges


def deduplicate_detections(
    detections: list,
    iou_threshold: float = 0.45,
) -> list:
    """Stateless dedup wrapper preserved for callers that batch up detections
    themselves (tests, FMV pipeline)."""
    if not detections:
        return []
    return _DetectionDedupeIndex(iou_threshold=iou_threshold).add(detections)


# ---------------------------------------------------------------------------
# Phase 2.6: Weighted Boxes Fusion (Solovyev et al. 2019).
#
# Where NMS picks one survivor per overlapping cluster and drops the rest,
# WBF averages every box in the cluster, weighted by (trust_weight × calibrated
# confidence), to produce a single fused box whose confidence is the cluster's
# average rather than the max. This rewards multi-detector agreement instead
# of letting the loudest single model dominate, and is the recommended
# post-calibration ensembling step in the modern aerial object-detection
# literature.
#
# Implemented here as a stateful index with the same ``.add(batch) -> list``
# contract as ``_DetectionDedupeIndex`` so it can be swapped in by env flag
# (``DEDUPE_METHOD=wbf``). Default remains the existing NMS path; WBF is
# opt-in until we have the larger evaluation harness from Phase 9 in place
# to validate it doesn't regress per-class recall.
# ---------------------------------------------------------------------------


class _WeightedBoxFusionIndex:
    """Stateful WBF clusterer. Same contract as ``_DetectionDedupeIndex``.

    Maintains per-class clusters in spatial buckets. When a new detection
    overlaps an existing cluster, the cluster's fused bbox is updated to
    the (weight-weighted) average of every member box, and the new
    detection is added to the cluster's member list. When no cluster
    overlaps, a new single-member cluster is started.

    ``.add(batch)`` returns only newly created or changed cluster heads. That
    makes the streaming path safe: callers do not re-store every historical
    cluster after each chip. ``heads()`` exposes the final full set when a
    deferred flush is needed.
    """

    BUCKET_SIZE = 512

    def __init__(
        self,
        iou_threshold: float = 0.55,
        expected_models: int = 2,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.expected_models = max(1, int(expected_models))
        # bucket → list[cluster]; cluster is a dict with the fused detection
        # plus a parallel ``_members`` list of contributing weights/boxes.
        self.buckets: dict[tuple[str, int, int], list[dict]] = {}
        # Order-preserving list of cluster heads, in insertion order.
        self.clusters: list[dict] = []
        self.raw_seen = 0
        self.kept_count = 0

    def _iou_for_class(self, det_class: str | None) -> float:
        if det_class:
            override = _PER_CLASS_IOU_THRESHOLDS.get(str(det_class).strip().lower())
            if override is not None:
                return override
        return self.iou_threshold

    @staticmethod
    def _bucket_of(bbox: list[float]) -> tuple[int, int]:
        cx = int(((bbox[0] + bbox[2]) / 2) // _WeightedBoxFusionIndex.BUCKET_SIZE)
        cy = int(((bbox[1] + bbox[3]) / 2) // _WeightedBoxFusionIndex.BUCKET_SIZE)
        return cx, cy

    @staticmethod
    def _weighted_average(members: list[dict]) -> tuple[list[float], float]:
        """Return (fused_bbox_xyxy, weight_sum) for the cluster's members."""
        total = sum(m["weight"] for m in members)
        if total <= 0:
            return members[0]["bbox"], 0.0
        fused = [0.0, 0.0, 0.0, 0.0]
        for m in members:
            w = m["weight"] / total
            bb = m["bbox"]
            for i in range(4):
                fused[i] += w * bb[i]
        return fused, total

    def add(self, detections: list) -> list:
        if not detections:
            return []
        changed_heads: list[dict] = []
        for det in sorted(detections, key=lambda d: _trust_weight_for(d) * float(d.get("confidence") or 0.0), reverse=True):
            self.raw_seen += 1
            bbox = det.get("pixel_bbox")
            if not bbox or len(bbox) < 4:
                # Pass-through for detections without a bbox; treat as its
                # own cluster so it survives.
                det.setdefault("dedupe_method", "wbf")
                cluster = {"head": det, "_members": [], "_class": det.get("parent_class") or det.get("class")}
                self.clusters.append(cluster)
                self.kept_count += 1
                changed_heads.append(det)
                continue
            try:
                conf = float(det.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            weight = _trust_weight_for(det) * conf
            det_class = det.get("parent_class") or det.get("class")
            iou_for_class = self._iou_for_class(det_class)
            cx, cy = self._bucket_of(bbox)
            best_cluster: dict | None = None
            best_iou = 0.0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for cand in self.buckets.get((det_class, cx + dx, cy + dy), ()):
                        head = cand["head"]
                        overlap = detection_overlap(det, head)
                        if overlap >= iou_for_class and overlap > best_iou:
                            best_iou = overlap
                            best_cluster = cand
            if best_cluster is not None:
                # Append to existing cluster, recompute fused bbox.
                best_cluster["_members"].append({"bbox": list(bbox[:4]), "weight": weight, "raw_conf": conf, "source": det.get("source_layer")})
                fused_bbox, _ = self._weighted_average(best_cluster["_members"])
                head = best_cluster["head"]
                head["pixel_bbox"] = fused_bbox
                # Cluster confidence = mean(member raw_conf) × min(N, expected) / expected
                # — the second factor rewards multi-detector agreement.
                n = len(best_cluster["_members"])
                mean_conf = sum(m["raw_conf"] for m in best_cluster["_members"]) / n
                agreement_factor = min(n, self.expected_models) / self.expected_models
                head["confidence"] = max(0.0, min(1.0, mean_conf * (0.5 + 0.5 * agreement_factor)))
                head["dedupe_method"] = "wbf"
                head["wbf_member_count"] = n
                head["wbf_member_sources"] = sorted({
                    m["source"] or "unknown" for m in best_cluster["_members"]
                })
                changed_heads.append(head)
            else:
                det.setdefault("dedupe_method", "wbf")
                det["wbf_member_count"] = 1
                det["wbf_member_sources"] = [det.get("source_layer") or "unknown"]
                cluster = {
                    "head": det,
                    "_class": det_class,
                    "_members": [{"bbox": list(bbox[:4]), "weight": weight, "raw_conf": conf, "source": det.get("source_layer")}],
                }
                self.buckets.setdefault((det_class, cx, cy), []).append(cluster)
                self.clusters.append(cluster)
                self.kept_count += 1
                changed_heads.append(det)
        return changed_heads

    def heads(self) -> list[dict]:
        return [c["head"] for c in self.clusters]

    def reconcile_edge_truncated(self, survivors: list[dict]) -> tuple[list[dict], int]:
        """No-op for WBF. The fusion step already handles cross-chip
        contributions to the same object — adding a second pass of
        edge-truncated reconciliation would double-merge. Returns the
        input survivor list unchanged + zero merges so the
        non-streaming caller's contract still works.
        """
        return list(survivors), 0


def _rederive_geo_from_pixel_bbox(det: dict, transform, crs) -> None:
    """Recompute ``pixel_obb`` / ``geo_polygon`` / ``geo_bbox`` from a mutated
    ``pixel_bbox``.

    WBF fusion (``_WeightedBoxFusionIndex.add``) and edge reconciliation
    (``reconcile_edge_truncated``) rewrite ``pixel_bbox`` only; without this
    refresh the persisted geometry keeps the pre-merge box and the DB geom
    diverges from the fused detection. Uses the same pixel→WGS84 transform
    as ``_apply_chip_response``.
    """
    bb = det.get("pixel_bbox") or []
    if len(bb) < 4:
        return
    x1, y1, x2, y2 = [float(v) for v in bb[:4]]
    pixel_obb = [x1, y1, x2, y1, x2, y2, x1, y2]
    lons, lats = [], []
    for px, py in zip(pixel_obb[0::2], pixel_obb[1::2]):
        lon, lat = transform * (px, py)
        lons.append(lon)
        lats.append(lat)
    if crs and crs.to_string() != "EPSG:4326":
        from rasterio.warp import transform as rasterio_transform
        lons, lats = rasterio_transform(crs, "EPSG:4326", lons, lats)
    det["pixel_obb"] = pixel_obb
    det["geo_polygon"] = [coord for point in zip(lons, lats) for coord in point]
    det["geo_bbox"] = [min(lons), min(lats), max(lons), max(lats)]


def _geo_stale_after_merge(det: dict) -> bool:
    """True when a dedupe step rewrote this detection's pixel_bbox."""
    if det.get("dedupe_method") == "edge_reconciled":
        return True
    try:
        return int(det.get("wbf_member_count") or 1) > 1
    except (TypeError, ValueError):
        return False




__all__ = [n for n in dir() if not n.startswith("__")]
