"""Unit tests for STIX 2.1 export (R3) — offline, pure builder, no DB/network."""

from __future__ import annotations

from stix_export import build_bundle, entity_to_stix, relation_to_stix


def test_entity_to_stix_infrastructure():
    sdo = entity_to_stix({"id": "v1", "kind": "vessel", "name": "MV Test", "hull": "IMO123"})
    assert sdo["type"] == "infrastructure"
    assert sdo["spec_version"] == "2.1"
    assert sdo["id"].startswith("infrastructure--")
    assert sdo["name"] == "MV Test"
    assert sdo["infrastructure_types"] == ["vessel"]
    assert sdo["x_sentinel_id"] == "v1"
    assert sdo["x_sentinel_hull"] == "IMO123"


def test_entity_to_stix_identity_for_unit_and_facility():
    unit = entity_to_stix({"id": "u1", "kind": "unit", "name": "3rd Fleet"})
    assert unit["type"] == "identity"
    assert unit["identity_class"] == "organization"
    facility = entity_to_stix({"id": "f1", "kind": "facility", "name": "Naval Base"})
    assert facility["type"] == "identity"
    assert facility["identity_class"] == "system"


def test_relation_type_normalised():
    rel = relation_to_stix({"relation_type": "operates_from"}, "infrastructure--a", "identity--b")
    assert rel["type"] == "relationship"
    assert rel["relationship_type"] == "operates-from"  # underscore → hyphen
    assert rel["source_ref"] == "infrastructure--a"
    assert rel["target_ref"] == "identity--b"


def test_build_bundle_shape_and_resolved_refs():
    entities = [
        {"id": "v1", "kind": "vessel", "name": "MV Test", "operates_from_base_id": "f1"},
        {"id": "f1", "kind": "facility", "name": "Naval Base"},
    ]
    relations = [{"source_id": "v1", "target_id": "f1", "relation_type": "operates-from"}]
    bundle = build_bundle(entities, relations)

    assert bundle["type"] == "bundle"
    assert bundle["spec_version"] == "2.1"
    assert bundle["id"].startswith("bundle--")

    obj_ids = {o["id"] for o in bundle["objects"]}
    rels = [o for o in bundle["objects"] if o["type"] == "relationship"]
    assert len(rels) == 1
    # Every relationship endpoint resolves to an object actually in the bundle.
    assert rels[0]["source_ref"] in obj_ids
    assert rels[0]["target_ref"] in obj_ids


def test_build_bundle_drops_dangling_relationship():
    entities = [{"id": "v1", "kind": "vessel", "name": "Solo"}]
    relations = [{"source_id": "v1", "target_id": "missing", "relation_type": "near"}]
    bundle = build_bundle(entities, relations)
    # The edge to a non-exported entity must not appear as a half-resolved SRO.
    assert [o for o in bundle["objects"] if o["type"] == "relationship"] == []
    assert len([o for o in bundle["objects"] if o["type"] == "infrastructure"]) == 1
