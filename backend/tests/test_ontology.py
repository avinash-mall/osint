"""Tests for backend/ontology.py — Step 2 of the ontology refactor plan.

Run with:
    POSTGIS_URI=postgresql://sentinel:sentinel@172.18.0.4:5432/sentinel \
      python -m pytest backend/tests/test_ontology.py -v

These tests touch the live PostGIS DB. They clean up after themselves by
deleting rows whose ids start with ``test_``.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import ontology  # noqa: E402
from database import postgis_db  # noqa: E402

TEST_BRANCH_ID = "test_zxqkk_branch"
TEST_OBJECT_ID = "test_zxqkk_object"
TEST_UNKNOWN_LABELS = (
    "zxqkk_unicorn_battalion_3000",
    "zxqkk_test_repeat_label",
    "zxqkk_threading_a",
    "zxqkk_threading_b",
    "zxqkk_threading_c",
    "zxqkk_new_branch_match_label",
)


def _delete_test_rows() -> None:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM ontology_unknown_labels WHERE label = ANY(%s) "
            "OR label LIKE 'zxqkk%%' OR label LIKE 'test_%%'",
            (list(TEST_UNKNOWN_LABELS),),
        )
        cur.execute(
            "DELETE FROM ontology_objects WHERE id LIKE 'test_%%' "
            "OR branch_id LIKE 'test_%%'"
        )
        cur.execute("DELETE FROM ontology_branches WHERE id LIKE 'test_%%'")


@pytest.fixture(autouse=True)
def _reset_state():
    """Clean test rows before/after every test and invalidate the cache."""
    _delete_test_rows()
    ontology.invalidate_cache()
    # Make sure the version is bumped so the cache reload doesn't keep
    # stale rows from a previous test.
    ontology.bump_version()
    yield
    _delete_test_rows()
    ontology.bump_version()
    ontology.invalidate_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _insert_test_branch(
    bid: str = TEST_BRANCH_ID,
    *,
    matchers: list[str] | None = None,
    icon_key: str = "circle_help",
    label: str | None = None,
) -> None:
    import json
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO ontology_branches "
            "(id, parent_id, label, color, short, icon_key, matchers, sensors, order_index) "
            "VALUES (%s, NULL, %s, '#abcdef', 'TST', %s, %s::jsonb, %s::jsonb, 1) "
            "ON CONFLICT (id) DO UPDATE SET matchers=EXCLUDED.matchers, "
            "icon_key=EXCLUDED.icon_key, label=EXCLUDED.label",
            (
                bid,
                label or bid,
                icon_key,
                json.dumps(matchers or []),
                json.dumps(["optical", "sar"]),
            ),
        )


def _insert_test_object(
    oid: str = TEST_OBJECT_ID,
    *,
    branch_id: str = TEST_BRANCH_ID,
    label: str = "Test Zxqkk Object",
    prompt: str = "test zxqkk widget",
    icon_key: str = "tank",
    sensors: list[str] | None = None,
) -> None:
    import json
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO ontology_objects "
            "(id, branch_id, label, prompt, sensors, icon_key, order_index) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s, 1) "
            "ON CONFLICT (id) DO UPDATE SET label=EXCLUDED.label, "
            "prompt=EXCLUDED.prompt, icon_key=EXCLUDED.icon_key, sensors=EXCLUDED.sensors",
            (
                oid,
                branch_id,
                label,
                prompt,
                json.dumps(sensors or ["optical"]),
                icon_key,
            ),
        )


def _unknown_count(label: str) -> int:
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT count FROM ontology_unknown_labels WHERE label=%s",
            (label,),
        )
        row = cur.fetchone()
        return int(row["count"]) if row else 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_exact_label_match():
    _insert_test_branch()
    _insert_test_object(label="Test Zxqkk Object", prompt="test zxqkk widget")
    ontology.bump_version()
    ontology.invalidate_cache()

    r = ontology.normalize("Test Zxqkk Object")
    assert r.was_unknown is False
    assert r.branch_id == TEST_BRANCH_ID
    assert r.ontology_object_id == TEST_OBJECT_ID
    assert r.icon_key == "tank"


def test_exact_prompt_match():
    _insert_test_branch()
    _insert_test_object(prompt="test zxqkk widget")
    ontology.bump_version()
    ontology.invalidate_cache()

    r = ontology.normalize("test zxqkk widget")
    assert r.was_unknown is False
    assert r.branch_id == TEST_BRANCH_ID
    assert r.ontology_object_id == TEST_OBJECT_ID


def test_branch_matcher_regex():
    _insert_test_branch(
        matchers=[r"\bzxqkkvehicle\b"],
        icon_key="car",
    )
    # No object that matches by label/prompt — only the regex hits.
    ontology.bump_version()
    ontology.invalidate_cache()

    r = ontology.normalize("military_zxqkkvehicle")
    assert r.was_unknown is False
    assert r.branch_id == TEST_BRANCH_ID
    assert r.icon_key == "car"
    assert r.ontology_object_id is None


def test_unknown_label_logged():
    label = "zxqkk_unicorn_battalion_3000"
    r = ontology.normalize(label)
    assert r.was_unknown is True
    assert r.branch_id == "Other"
    assert r.icon_key == "circle_help"
    # Phase 6.24: unknown labels keep their canonical form so the UI can still
    # render a meaningful pill instead of collapsing every novel detection to
    # "unknown". was_unknown=True still flags it for ontology curation.
    assert r.parent_class == "zxqkk_unicorn_battalion_3000"
    assert _unknown_count(label) >= 1


def test_unknown_upsert_counter():
    label = "zxqkk_test_repeat_label"
    ontology.normalize(label)
    ontology.normalize(label)
    assert _unknown_count(label) == 2


def test_sam3_case_mismatch():
    """'Military_Facility' (TitleCase) and 'military_facility' (lower)
    should both resolve to the same branch via the seeded ontology."""
    r1 = ontology.normalize("Military_Facility")
    r2 = ontology.normalize("military_facility")
    assert r1.branch_id == r2.branch_id
    # Should not fall through to Other — military facility is seeded.
    assert r1.branch_id != "Other"


def test_empty_input():
    for v in ("", None):
        r = ontology.normalize(v)
        assert r.branch_id == "Other"
        assert r.was_unknown is True
        assert r.icon_key == "circle_help"
    # Empty inputs must NOT create a row in the unknown table.
    with postgis_db.get_cursor() as cur:
        cur.execute("SELECT count(*) AS c FROM ontology_unknown_labels WHERE label=''")
        row = cur.fetchone()
        assert int(row["c"]) == 0


def test_cache_invalidation_on_version_bump():
    new_branch_id = "test_zxqkk_new_branch_after_bump"
    label_to_match = "zxqkk_new_branch_match_label"

    # First normalize — branch doesn't exist yet, falls to Other.
    r0 = ontology.normalize(label_to_match)
    assert r0.was_unknown is True

    # Insert new branch with a regex matcher and bump version.
    _insert_test_branch(
        bid=new_branch_id,
        matchers=[r"zxqkk_new_branch_match_label"],
        icon_key="rocket",
    )
    ontology.bump_version()

    # Next normalize — must reload cache and pick up the new branch.
    r1 = ontology.normalize(label_to_match)
    assert r1.branch_id == new_branch_id
    assert r1.was_unknown is False

    # Cleanup of this extra branch.
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM ontology_branches WHERE id=%s", (new_branch_id,))


def test_default_prompts():
    _insert_test_branch()
    _insert_test_object(
        prompt="test zxqkk sar prompt",
        sensors=["sar"],
    )
    _insert_test_object(
        oid="test_zxqkk_object_optical",
        prompt="test zxqkk optical prompt",
        sensors=["optical"],
    )
    ontology.bump_version()
    ontology.invalidate_cache()

    sar = ontology.default_prompts("sar")
    optical = ontology.default_prompts("optical")
    all_p = ontology.default_prompts(None)
    none_filter = ontology.default_prompts("")

    assert "test zxqkk sar prompt" in sar
    assert "test zxqkk optical prompt" not in sar
    assert "test zxqkk optical prompt" in optical
    assert "test zxqkk sar prompt" not in optical
    # all_prompts() returns everything regardless of sensor
    assert "test zxqkk sar prompt" in all_p
    assert "test zxqkk optical prompt" in all_p
    # Empty string sensor is treated as "no filter" (returns all)
    assert "test zxqkk sar prompt" in none_filter


def test_default_prompts_branch_scope():
    """``branch=`` scopes to the branch plus all its descendant branches."""
    import json
    _insert_test_branch()  # TEST_BRANCH_ID, a root branch
    child_id = "test_zxqkk_child_branch"
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO ontology_branches "
            "(id, parent_id, label, color, short, icon_key, matchers, sensors, order_index) "
            "VALUES (%s, %s, 'Child', '#abcdef', 'TSC', 'circle_help', %s::jsonb, %s::jsonb, 2) "
            "ON CONFLICT (id) DO NOTHING",
            (child_id, TEST_BRANCH_ID, json.dumps([]), json.dumps(["optical"])),
        )
    _insert_test_object(prompt="test zxqkk root prompt")
    _insert_test_object(
        oid="test_zxqkk_child_object",
        branch_id=child_id,
        prompt="test zxqkk child prompt",
    )
    ontology.bump_version()
    ontology.invalidate_cache()

    # Root branch rolls up its own object plus the child branch's object.
    root_scope = ontology.default_prompts(branch=TEST_BRANCH_ID)
    assert set(root_scope) == {"test zxqkk root prompt", "test zxqkk child prompt"}
    # Child branch yields only its own.
    child_scope = ontology.default_prompts(branch=child_id)
    assert set(child_scope) == {"test zxqkk child prompt"}

    # Unscoped all_prompts() is the superset — it contains both branch prompts
    # (plus everything else in the ontology). (This line previously referenced an
    # `all_p` left over from another test, which raised NameError.)
    assert {"test zxqkk root prompt", "test zxqkk child prompt"}.issubset(set(ontology.all_prompts()))


def test_threading_concurrent_normalize():
    """Many threads hammering normalize() must not crash or produce mismatched
    branch_ids for a deterministic input."""
    labels = [
        "military_facility",
        "zxqkk_threading_a",
        "zxqkk_threading_b",
        "zxqkk_threading_c",
        "tank",
        "",
    ]
    errors: list[BaseException] = []
    results: list[str] = []
    lock = threading.Lock()

    def _worker():
        try:
            for lbl in labels * 10:
                r = ontology.normalize(lbl)
                with lock:
                    results.append(f"{lbl}->{r.branch_id}")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"thread failures: {errors}"
    assert results
    # Determinism: military_facility must always map to the same branch_id.
    mf = {r for r in results if r.startswith("military_facility->")}
    assert len(mf) == 1, f"non-deterministic mapping: {mf}"
