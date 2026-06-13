# Adding a Reference Dataset

This is the recipe for landing a new dataset family (e.g. xView2, RarePlanes-real, a new defence-photo source) into the Reference Embedding DB. The full pipeline is already in place; you're adding one new sub-fetcher + a few rows in the seed JSON.

## Prerequisites

- Decide license posture: redistributable (HF/Zenodo mirror, public license) or drop-in (operator-supplied tarball).
- Identify the platform → source-class mapping: which platforms in the dataset map to which canonical `reference_platforms.platform_name`?

## Steps

### 1. Add a sub-fetcher to `scripts/fetch_reference_datasets.py`

Pattern A — **HF mirror** (no token or token-gated):

```python
def _fetch_mynewds(out: Path, max_chips_per_class: int = 50) -> FetchResult:
    dataset = "mynewds"
    token = _load_hf_token()  # if gated
    if not token:
        return FetchResult(dataset, "skipped", "HF_TOKEN not set (gated dataset)")
    dataset_root = out / dataset
    dataset_root.mkdir(parents=True, exist_ok=True)
    # ... fetch + crop + write per-chip MANIFEST.json
```

Pattern B — **Drop-in only** (account-locked source):

```python
def _fetch_mynewds(out: Path, dropin_root: Path) -> FetchResult:
    return _fetch_dropin_only("mynewds", out, dropin_root)
```

Pattern B′ — **Drop-in only, but the source ships bbox labels** (not pre-cropped
per-class chips): write a dedicated adapter that crops the largest labelled
bbox per image into `<out>/<dataset>/<class>/*.png`. `_fetch_mvrsd`
([scripts/fetch_reference_datasets.py](../../scripts/fetch_reference_datasets.py))
is the worked example — MVRSD ships YOLO/VOC bboxes, so `_fetch_dropin_only`
(which expects per-class subdirs or a DOTA `labels.json`) doesn't fit.

Pattern C — **Manifest-driven HTTP** (curated URL list):

Add `scripts/manifests/mynewds.json` with the schema documented in [reference-corpora-bake.md](../operations/reference-corpora-bake.md). The existing `_fetch_from_manifest` handles it — just register the dataset name in `run()`.

### 2. Register the dataset in `run()`

In [`scripts/fetch_reference_datasets.py`](../../scripts/fetch_reference_datasets.py) `run()`:

```python
if _gate("mynewds"):
    results.append(_fetch_mynewds(out, max_chips_per_class))
```

Place it in the same block as similar adapters (HF-gated next to HF-gated, drop-in next to drop-in, manifest next to manifest).

### 3. Add platform entries to the seed JSON

In [`backend/scripts/seeds/reference_platforms.seed.json`](../../backend/scripts/seeds/reference_platforms.seed.json), each canonical platform that has chips in your new dataset needs a `source_terms_per_dataset` entry mapping to the on-disk class subdir name:

```json
{
  "platform_name": "F-35A Lightning II",
  "platform_family": "Fighter Aircraft",
  "source_terms_per_dataset": {
    "dota": ["plane"],
    "mynewds": ["f-35", "f-35a"]
  }
}
```

The bake walks `dataset_root/<source_term>/*.png` for every term listed.

### 4. Add a `MANIFEST.json` license entry per chip

The fetcher writes `<dataset>/MANIFEST.json` for you. Verify the per-chip license_spdx + attribution columns are populated correctly — these flow into `reference_chips.license_spdx`, `source_url`, `attribution`.

### 5. Test with a small subset

Run the fetcher locally before letting it loose at full chip-per-class caps:

```sh
python scripts/fetch_reference_datasets.py \
  --out /tmp/refchips-test \
  --max-chips-per-class 5 \
  --only mynewds \
  --verbose
```

Inspect the output: `find /tmp/refchips-test/mynewds -type f | head` and check `MANIFEST.json` for sensible attribution/license entries.

### 6. Test the full bake against a small DB

```sh
docker compose exec -T backend python -m scripts.bake_reference_index \
  --seed /app/scripts/seeds/reference_platforms.seed.json \
  --dataset mynewds \
  --dataset-root /tmp/refchips-test/mynewds \
  --license <SPDX> \
  --max-chips-per-class 5
```

Verify the row count: `SELECT source_dataset, count(*) FROM reference_chips GROUP BY 1;` shows `mynewds` with the expected number.

### 7. Wire into the assets image

After verifying locally:

```sh
docker compose build assets
docker compose down -v
docker compose up -d
```

The lifespan auto-seed picks up the new dataset automatically because `worker.seed_reference_db` iterates everything under `/opt/reference-corpora/`. No code change needed.

### 8. Update documentation

- Add a row to [reference-corpora-bake.md](../operations/reference-corpora-bake.md)'s "Supported sources" table.
- If the dataset has interesting acquisition friction (registration portal, HF gating, .mil-only API), note it in [offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md).

## Test coverage

A new sub-fetcher should pass [`scripts/tests/test_fetch_reference_datasets.py`](../../scripts/tests/test_fetch_reference_datasets.py)'s test patterns:
- Successful download writes provenance.
- Idempotent re-run skips via the marker file.
- Missing credentials/inputs returns `status="skipped"` without raising.

## Cross-references

- [docs/backend/reference-platform-baker.md](../backend/reference-platform-baker.md) — the bake script.
- [docs/decisions/why-bake-reference-corpora-into-assets.md](../decisions/why-bake-reference-corpora-into-assets.md) — why the bake lives in the assets image.
- [docs/operations/reference-corpora-bake.md](../operations/reference-corpora-bake.md) — operator runbook.
