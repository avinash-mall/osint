"""Pydantic request/response models for the Sentinel API.

Extracted from ``backend/main.py`` so router modules can import the schemas they
need without re-declaring them. Grouped by domain to make navigation easy:

* Detections / review / threat
* FMV
* Ontology + prompt profiles
* Inference + confidence overrides
* Tracks
* Feeds, ingest, collection
* Analytics + training
* AI
* Auth
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthTestRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# Detections
# ---------------------------------------------------------------------------


class DetectionTagUpdate(BaseModel):
    allegiance: str


class DetectionQuery(BaseModel):
    bbox: Optional[List[float]] = None  # [min_lon, min_lat, max_lon, max_lat]
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    det_class: Optional[str] = None


class ObjectDetailsBody(BaseModel):
    designation: Optional[str] = None
    object_class: Optional[str] = None
    military_classification: Optional[str] = None
    threat_level: Optional[str] = None
    affiliation: Optional[str] = None
    confidence_override: Optional[float] = None
    notes: Optional[str] = None
    platform_name: Optional[str] = None
    platform_family: Optional[str] = None
    platform_confidence: Optional[float] = None
    platform_source: Optional[str] = None  # 'auto' | 'analyst' | 'manual'


class ManualDetectionBody(BaseModel):
    pass_id: Optional[int] = None
    geometry: dict = Field(..., description="GeoJSON Polygon in EPSG:4326")
    object_class: str = Field("unknown", description="Free-form class label")
    designation: Optional[str] = None
    military_classification: Optional[str] = None
    threat_level: Optional[str] = "medium"
    affiliation: Optional[str] = "unknown"
    notes: Optional[str] = None
    confidence: Optional[float] = 1.0


class ReviewUpdate(BaseModel):
    status: str
    note: Optional[str] = None


class CandidateLinkDecision(BaseModel):
    analyst: Optional[str] = "analyst"


# ---------------------------------------------------------------------------
# Reference Embedding DB — Plan D HTTP request/response models.
# Routes are mounted at backend/routers/reference_platforms.py.
# ---------------------------------------------------------------------------


class ReferenceChipRef(BaseModel):
    id: str
    chip_path: str
    source_dataset: str
    source_url: Optional[str] = None
    license_spdx: str
    attribution: Optional[str] = None


class ReferencePlatformSummary(BaseModel):
    """List-view shape — chips omitted for payload size."""
    id: str
    platform_name: str
    platform_family: str
    ontology_object_id: Optional[str] = None
    country_of_origin: Optional[str] = None
    role: Optional[str] = None
    view_domains: List[str]
    attributes: dict = {}


class ReferencePlatformDetail(ReferencePlatformSummary):
    """Detail-view shape — includes a sample of chips."""
    chips: List[ReferenceChipRef] = []


class ReferencePlatformsList(BaseModel):
    platforms: List[ReferencePlatformSummary]
    count: int


class IdentifyRequest(BaseModel):
    """Body for POST /api/detections/{id}/identify."""
    view_domain: Literal["overhead", "ground"] = "overhead"
    top_k: int = 3


class IdentificationCandidate(BaseModel):
    id: str
    detection_id: int
    platform_id: str
    platform_name: str
    platform_family: str
    score: float
    rank: int
    matched_chip_ids: List[str] = []
    status: str  # 'pending' | 'approved' | 'rejected' | 'auto_applied'
    applied_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    created_at: str


class IdentifyResponse(BaseModel):
    """POST /api/detections/{id}/identify — what the route returns."""
    detection_id: int
    candidates_written: int
    candidates: List[IdentificationCandidate]


class IdentificationCandidatesList(BaseModel):
    """GET /api/detections/{id}/identification-candidates."""
    detection_id: int
    candidates: List[IdentificationCandidate]
    count: int


class ApproveRejectResponse(BaseModel):
    """POST .../approve and .../reject."""
    candidate_id: str
    status: str  # 'approved' or 'rejected'
    detection_id: int
    platform_id: str
    reviewed_by: str
    reviewed_at: str


# ---------------------------------------------------------------------------
# Tracks
# ---------------------------------------------------------------------------


class PinRequest(BaseModel):
    detection_id: int


class ReprocessRequest(BaseModel):
    since: Optional[str] = None


# ---------------------------------------------------------------------------
# Ontology + prompt profiles
# ---------------------------------------------------------------------------


class OntologyUpdateRequest(BaseModel):
    source_type: str = "ava_chat"
    source_id: Optional[str] = None
    text: str
    domain: str = "OSINT"


class OntologyBranchIn(BaseModel):
    id: str
    parent_id: Optional[str] = None
    label: str
    color: Optional[str] = None
    short: Optional[str] = None
    icon_key: Optional[str] = None
    matchers: Optional[List[str]] = None
    sensors: Optional[List[str]] = None
    order_index: Optional[int] = 0


class OntologyBranchPatch(BaseModel):
    parent_id: Optional[str] = None
    label: Optional[str] = None
    color: Optional[str] = None
    short: Optional[str] = None
    icon_key: Optional[str] = None
    matchers: Optional[List[str]] = None
    sensors: Optional[List[str]] = None
    order_index: Optional[int] = None


class OntologyObjectIn(BaseModel):
    id: str
    branch_id: str
    label: str
    prompt: str
    sensors: Optional[List[str]] = None
    min_gsd_meters: Optional[float] = None
    icon_key: Optional[str] = None
    order_index: Optional[int] = 0


class OntologyObjectPatch(BaseModel):
    branch_id: Optional[str] = None
    label: Optional[str] = None
    prompt: Optional[str] = None
    sensors: Optional[List[str]] = None
    min_gsd_meters: Optional[float] = None
    icon_key: Optional[str] = None
    order_index: Optional[int] = None


class OntologyCreateObject(BaseModel):
    label: str
    prompt: str
    icon_key: Optional[str] = None
    sensors: Optional[List[str]] = None
    min_gsd_meters: Optional[float] = None
    order_index: Optional[int] = 0
    id: Optional[str] = None


class OntologyAssignBody(BaseModel):
    branch_id: str
    object_id: Optional[str] = None
    create_object: Optional[OntologyCreateObject] = None


class PromptProfileBody(BaseModel):
    sensor: str
    name: str
    version: str
    prompts: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    make_current: bool = False


# ---------------------------------------------------------------------------
# Inference + confidence overrides
# ---------------------------------------------------------------------------


class ConfidenceConfig(BaseModel):
    per_class_confidence_overrides: dict[str, float] = Field(default_factory=dict)
    global_floor: Optional[float] = None
    high_confidence_threshold: Optional[float] = None


# ---------------------------------------------------------------------------
# Feeds, ingest, collection
# ---------------------------------------------------------------------------


class FeedEventCreate(BaseModel):
    source_id: Optional[int] = None
    event_type: str = "observation"
    payload: dict = Field(default_factory=dict)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    observed_at: Optional[str] = None


class FeedConnectRequest(BaseModel):
    name: str
    feed_type: str
    endpoint: str
    protocol: str = "tcp"
    topic: Optional[str] = "feeds"
    parser: Optional[str] = None
    enabled: bool = True


class IngestRequest(BaseModel):
    image_url: str
    sensor_type: Optional[str] = "Optical"
    acquisition_time: Optional[str] = None


class IngestUrlRequest(BaseModel):
    url: str
    domain: str = "OSINT"
    source_type: str = "url"
    title: Optional[str] = None
    auto_process: bool = True


class CollectionTaskCreate(BaseModel):
    target_id: str
    target_name: Optional[str] = None
    asset_type: str = "ISR"
    priority: Optional[str] = None
    queue: Optional[str] = None
    notes: Optional[str] = None
    aipoints: Optional[List[dict]] = None


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


class GraphActionRequest(BaseModel):
    node_id: str


class GraphPathRequest(BaseModel):
    """Body for ``POST /api/graph/path`` — shortest-path between two graph nodes."""

    from_id: str
    to_id: str
    max_depth: int = Field(default=4, ge=1, le=8)


class GraphPromoteRequest(BaseModel):
    """Body for ``POST /api/graph/candidate-edges/{id}/promote`` — analyst name carried for audit."""

    analyst: Optional[str] = None


class GraphContradictRequest(BaseModel):
    """Body for ``POST /api/graph/contradict`` — analyst flags evidence-against."""

    actor_id: str = Field(..., description="elementId of the OntologyCandidate or Target being contradicted")
    detection_postgis_id: int = Field(..., description="PostGIS detection_id that contradicts the actor")
    reason: Optional[str] = None
    analyst: Optional[str] = None


# ---------------------------------------------------------------------------
# Analytics + training
# ---------------------------------------------------------------------------


class AnalyticsRequest(BaseModel):
    target_id: Optional[str] = None
    aoi: Optional[dict] = None
    observer: Optional[dict] = None
    destination: Optional[dict] = None
    radius_m: Optional[float] = 5000
    minutes: Optional[int] = 15
    observer_height_m: Optional[float] = 1.8
    target_height_m: Optional[float] = 0.0
    # Routes-only: strategy in {"shortest", "least_exposure", "balanced"}.
    strategy: Optional[str] = None
    # Change-detection: both IDs are required for real raster differencing.
    before_pass_id: Optional[int] = None
    after_pass_id: Optional[int] = None


class TrainingJobCreate(BaseModel):
    name: str
    dataset_path: Optional[str] = None
    epochs: int = 1


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------


class AIAnalysisRequest(BaseModel):
    prompt: str
    domain: Optional[str] = None
    entity_id: Optional[str] = None
    context: dict = Field(default_factory=dict)


class AIActionProposalRequest(BaseModel):
    prompt: str
    domain: Optional[str] = None
    action_type: str = "generate_report"
    target_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)
    risk_level: str = "low"
