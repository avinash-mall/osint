# Removed `Battle_Damage` and `Auxiliary` ("Aux Layers") ontology branches

**Path:** N/A (removal record)
**Lines:** N/A
**Depends on:** [backend/scripts/seeds/defenceOntology.seed.json](../../backend/scripts/seeds/defenceOntology.seed.json), live PostGIS `ontology_branches` / `ontology_objects` / `ontology_version`, [backend/threat_assessment.py](../../backend/threat_assessment.py), [frontend/src/components/map/_helpers.ts](../../frontend/src/components/map/_helpers.ts), [frontend/src/utils/defenceOntology.ts](../../frontend/src/utils/defenceOntology.ts), [scripts/eval_metrics/label_normalizer.py](../../scripts/eval_metrics/label_normalizer.py)

## Purpose

Records the permanent removal of two ontology branches and all their objects:

- **`Battle_Damage`** — objects `Damaged_Building`, `Vehicle`, `Ship`, each a plain SAM3 open-vocab prompt (`"damaged building"` / `"vehicle"` / `"ship"`).
- **`Auxiliary`** (UI label "Aux Layers") — objects `Burn_Scar`, `Flood`, and 12 `Crop_*` objects, all using `__prithvi_*__` sentinel prompts that only the now-removed Prithvi-EO-2.0 heads ever emitted.

## Why this design

Both branches were tied to the battle-damage / damage-assessment concept that lived on the Prithvi capability, which was already removed (see [removed-prithvi-battle-damage.md](removed-prithvi-battle-damage.md)).

- **`Auxiliary` was literally dead post-Prithvi.** Its objects carried `__prithvi_*__` sentinel prompts that no live detector emits anymore — the only producer was the deleted Prithvi flood/burn/multitemporal-crop heads. The branch could never light up; it was carrying UI surface and a normalize() target for nothing.
- **`Battle_Damage` was dropped by explicit decision.** Rather than re-home its three generic open-vocab prompts, the user-approved call was to drop the whole battle-damage concept from the UI and ontology, since it was bound up with the Prithvi capability and an open-vocabulary GEOINT platform should not advertise a damage-assessment surface it no longer stands behind. This is consistent with the earlier removal of the Prithvi heads and of `DEFENCE_YOLO` (see [removed-defence-yolo.md](removed-defence-yolo.md)).

Removed from **both** sources of truth so the seed and the running system stay in sync:

- **Seed JSON** ([defenceOntology.seed.json](../../backend/scripts/seeds/defenceOntology.seed.json)) — both branches and all their objects deleted; the file's top-level `description` no longer mentions `__prithvi_*` sentinels. The seed now carries **12 top-level branches** in its `branches` array (18 branch nodes counting nested children).
- **Live PostGIS ontology** — the `ontology_branches` and `ontology_objects` rows for both branches deleted and `ontology_version` bumped (so the read-through cache in [backend/ontology.py](../../backend/ontology.py) rebuilds and any cached prompt sets in inference refresh). The live DB now has **19 total branches**.

## Key symbols

Code references cleaned up alongside the data removal:

- [backend/threat_assessment.py](../../backend/threat_assessment.py) — dropped the `"auxiliary": "nature"` entry from `_BRANCH_CATEGORIES` and the `Battle_Damage` parent-string-fallback comment note.
- [frontend/src/components/map/_helpers.ts](../../frontend/src/components/map/_helpers.ts) — removed `'Battle_Damage'` from `HEAVY_OUTLINE_CATEGORIES`.
- [frontend/src/utils/defenceOntology.ts](../../frontend/src/utils/defenceOntology.ts) — genericised the sentinel-prompt comment (no longer names `__prithvi_*`).
- [backend/tests/test_threat_category.py](../../backend/tests/test_threat_category.py) — dropped the `Battle_Damage` / `corn_field` / `Auxiliary` test cases.
- [scripts/eval_metrics/label_normalizer.py](../../scripts/eval_metrics/label_normalizer.py) — removed the `Battle_Damage` and `Auxiliary` label maps.

## Inputs / Outputs

No runtime contract change. Detections whose labels would have normalized into either branch now fall through to `branch_id="unknown"` (and the unknown-label triage queue) like any other unmatched label; `category_for_class` returns `"object"` for them instead of `"nature"`. No detector emits the removed prompts, so in practice nothing routes there.

## Failure modes

None introduced. The branches had no live producer (`Auxiliary`) or only generic redundant prompts (`Battle_Damage`); their removal cannot lose a working detection path.

## Cross-references

- [removed-prithvi-battle-damage.md](removed-prithvi-battle-damage.md) — the Prithvi removal these branches were tied to
- [removed-defence-yolo.md](removed-defence-yolo.md) — earlier battle-damage detector removal (shape precedent)
- [backend/threat-assessment.md](../backend/threat-assessment.md) — `_BRANCH_CATEGORIES` no longer maps `auxiliary`
- [backend/ontology-system.md](../backend/ontology-system.md) — DB-canonical branches/objects + version bump
- [conventions/adding-a-new-ontology-object.md](../conventions/adding-a-new-ontology-object.md) — the seed + DB + version-bump flow these removals followed in reverse
