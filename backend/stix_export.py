"""STIX 2.1 export of the operational-entity graph (R3) — offline serialization.

Converts Sentinel's operational entities (vessel / aircraft / vehicle / facility
/ unit / asset) and the relationships between them into a STIX 2.1 bundle that
SIEM / CTI platforms (OpenCTI, Splunk ES, MS Sentinel, IBM QRadar) can ingest.

Clean-room implementation from the OASIS STIX 2.1 specification — no ShadowBroker
source copied. Pure serialization on rows passed in; no DB, no network in the
builder itself (the router supplies the rows), so it works air-gapped.

Domain note: Sentinel is GEOINT, not cyber-CTI, so our entities map to STIX
`identity` / `infrastructure` / `location` SDOs (not threat-actor/malware).
Sentinel-specific fields ride along as `x_sentinel_*` custom properties, which
STIX 2.1 explicitly permits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

_SPEC = "2.1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _stix_id(stix_type: str) -> str:
    return f"{stix_type}--{uuid.uuid4()}"


# Map our operational-entity kind → (STIX SDO type, identity_class hint).
# vessel/aircraft/vehicle/asset → infrastructure (a controllable physical asset);
# unit → identity (an organisation/group); facility → location-anchored identity.
_KIND_TO_STIX = {
    "vessel": "infrastructure",
    "aircraft": "infrastructure",
    "vehicle": "infrastructure",
    "asset": "infrastructure",
    "facility": "identity",
    "unit": "identity",
}


def entity_to_stix(entity: dict) -> dict:
    """Convert one operational_entities row (dict) to a STIX 2.1 SDO."""
    kind = str(entity.get("kind") or "asset").lower()
    stix_type = _KIND_TO_STIX.get(kind, "infrastructure")
    obj = {
        "type": stix_type,
        "spec_version": _SPEC,
        "id": _stix_id(stix_type),
        "created": _now(),
        "modified": _now(),
        "name": entity.get("name") or f"{kind}-{entity.get('id', '')}",
        "labels": [kind],
        # Sentinel provenance as custom props (x_ prefix per STIX 2.1).
        "x_sentinel_id": entity.get("id"),
        "x_sentinel_kind": kind,
    }
    if stix_type == "identity":
        obj["identity_class"] = "organization" if kind == "unit" else "system"
    else:
        obj["infrastructure_types"] = [kind]
    for src, dst in (("callsign", "x_sentinel_callsign"),
                     ("hull", "x_sentinel_hull"),
                     ("entity_class", "x_sentinel_class"),
                     ("unit_id", "x_sentinel_unit_id"),
                     ("operates_from_base_id", "x_sentinel_operates_from")):
        val = entity.get(src)
        if val:
            obj[dst] = val
    return obj


def relation_to_stix(relation: dict, src_ref: str, tgt_ref: str) -> dict:
    """Convert a relationship edge to a STIX 2.1 relationship SRO."""
    rel_type = str(relation.get("relation_type") or relation.get("type") or "related-to")
    rel_type = rel_type.lower().replace("_", "-")
    return {
        "type": "relationship",
        "spec_version": _SPEC,
        "id": _stix_id("relationship"),
        "created": _now(),
        "modified": _now(),
        "relationship_type": rel_type,
        "source_ref": src_ref,
        "target_ref": tgt_ref,
    }


def build_bundle(entities: Iterable[dict], relations: Iterable[dict]) -> dict:
    """Assemble a STIX 2.1 bundle from entity rows + relationship edges.

    ``relations`` items use our internal ids in ``source_id`` / ``target_id``;
    they are resolved to STIX object ids and dropped if either endpoint is
    missing (a dangling edge is never emitted as a half-resolved relationship).
    """
    objects: list[dict] = []
    id_map: dict[str, str] = {}  # internal entity id → STIX id

    for entity in entities:
        sdo = entity_to_stix(entity)
        objects.append(sdo)
        internal = entity.get("id")
        if internal is not None:
            id_map[str(internal)] = sdo["id"]

    for rel in relations:
        src = id_map.get(str(rel.get("source_id", "")))
        tgt = id_map.get(str(rel.get("target_id", "")))
        if src and tgt:
            objects.append(relation_to_stix(rel, src, tgt))

    return {
        "type": "bundle",
        "id": _stix_id("bundle"),
        "spec_version": _SPEC,
        "objects": objects,
    }
