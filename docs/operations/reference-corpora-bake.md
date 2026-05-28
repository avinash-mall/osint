# Reference Corpora Bake — Operator Runbook

**Path:** [`scripts/fetch_reference_datasets.py`](../../scripts/fetch_reference_datasets.py) + [`assets/Dockerfile`](../../assets/Dockerfile) + [`backend/worker_legacy.py`](../../backend/worker_legacy.py) `seed_reference_db`

## Purpose

The Reference Embedding DB needs chip imagery for every platform it can identify. This runbook documents how to (a) bake those chips into the `assets` image, (b) get the runtime to auto-seed `reference_platforms` + `reference_chips`, and (c) re-seed on demand.

## Supported sources

| Dataset | Adapter | Access | License (default) | Status |
|---|---|---|---|---|
| **DOTA v1.0** | `_fetch_dota_v1` | HF public mirror (Last-Bullet/DOTAv1.0) | CC-BY-4.0 | ✅ working |
| **DOTA v2.0** | `_fetch_dota_v2` | HF gated (needs `HF_TOKEN`) | CC-BY-4.0 | scaffold (mirror not wired) |
| **RarePlanes-synth** | `_fetch_rareplanes` | HF gated | CC-BY-4.0 | scaffold (mirror not wired) |
| **FAIR1M** | `_fetch_fair1m` | Zenodo direct | CC-BY-4.0 | scaffold (URL not pinned) |
| **xView** | `_fetch_dropin_only("xview")` | Maxar MOU portal | CC-BY-NC-SA-4.0 | drop-in only |
| **DIOR** | `_fetch_dropin_only("dior")` | registration portal | research-only | drop-in only |
| **HRSC2016** | `_fetch_dropin_only("hrsc2016")` | research download | research-only | drop-in only |
| **ShipRSImageNet** | `_fetch_dropin_only("shiprsimagenet")` | registration | research-only | drop-in only |
| **Wikimedia** | `_fetch_from_manifest("wikimedia")` | curated JSON of public URLs | CC-BY-SA-4.0 (per-item) | seed entries present |
| **NARA** | `_fetch_from_manifest("nara")` | catalog URLs | PD-USGov | manifest skeleton (empty) |
| **NASA** | `_fetch_from_manifest("nasa")` | EO archive URLs | PD-USGov | manifest skeleton (empty) |
| **DVIDS** | `_fetch_from_manifest("dvids")` | public.dvidshub.net URLs | PD-USGov | manifest skeleton (empty) |

## Building the corpora image

```sh
# Full build with all adapters (HF_TOKEN unlocks the gated ones)
docker compose build assets

# Slim build (skips corpora entirely — for fast smoke iteration)
REFERENCE_CORPORA_ENABLED=0 docker compose build assets

# Cap chips per class lower for a fast test build
REFERENCE_MAX_CHIPS_PER_CLASS=5 docker compose build assets
```

Expected build time: **30–90 minutes** depending on which adapters succeed. BuildKit cache mount at `/cache/reference-corpora` makes re-runs near-instant.

## Verifying the bake

After build:

```sh
# Inspect what's in the image
docker run --rm sentinel-assets:offline ls /opt/baked-reference-chips/
docker run --rm sentinel-assets:offline cat /opt/baked-reference-chips/MANIFEST.sha256

# Per-dataset manifest
docker run --rm sentinel-assets:offline \
    sh -c 'cat /opt/baked-reference-chips/dota/MANIFEST.json | head -50'
```

## How the volume gets populated

The `reference_corpora_data` named volume is RW-mounted into the `assets` container at `/usr/share/nginx/html/reference-chips/` and RO-mounted into `backend` + `worker` at `/opt/reference-corpora/`.

On `assets` startup:
1. Compare `/opt/baked-reference-chips/MANIFEST.sha256` (image-baked) against `/usr/share/nginx/html/reference-chips/MANIFEST.sha256` (volume).
2. If volume is empty or digest differs → `rsync -a --delete --delete-after /opt/baked-reference-chips/ /usr/share/nginx/html/reference-chips/`.
3. nginx starts serving the now-populated tree.

This means a fresh stack (`docker compose down -v && up -d`) populates the volume on first start without operator action.

## Auto-seed at runtime

Backend lifespan calls [`auto_enqueue_reference_seed_if_empty`](../../backend/platform_schema.py). When `reference_platforms` has zero rows AND `REFERENCE_DB_AUTO_SEED` is truthy (default `1`), it enqueues `worker.seed_reference_db`.

The worker task:
1. Reads `/opt/reference-corpora/MANIFEST.sha256` to enumerate present datasets.
2. For each: rsyncs chips into `/data/datasets/reference-chips/<dataset>/` (the writable volume the bake reads from).
3. Calls [`bake_reference_index.run()`](../../backend/scripts/bake_reference_index.py) per dataset.
4. Publishes `started` / `dataset_progress` / `done` / `error` events to the `reference-seed` WS topic.

Frontend subscribes via [`ReferencePlatformsView`](../../frontend/src/components/admin/ReferencePlatformsView.tsx) and renders a progress card. Auto-trigger or manual button: same WS topic.

## Manual seed / re-seed

```sh
# Via the admin UI:
#   Browser → Admin → Reference Platforms → Seed (or Re-seed)

# Via curl:
curl -sS -b /tmp/sess.txt \
    -X POST http://localhost:3000/api/admin/reference/seed \
    -H "Content-Type: application/json" \
    -d '{"force": false}'   # idempotent — short-circuits when rows present
curl ... -d '{"force": true}'                # rebake; UPSERT existing rows
curl ... -d '{"force": true, "only": ["dota"]}'  # one-dataset re-seed
```

The response includes a Celery `task_id`; subscribe to `ws://<host>/ws?topic=reference-seed` for live progress.

## Drop-in restricted datasets

For xView, DIOR, HRSC2016, ShipRSImageNet:

```sh
mkdir -p ./reference-corpora-input/dior
# Acquire dior.zip via the official registration portal, then extract:
unzip dior.zip -d ./reference-corpora-input/dior/

# Layout that the drop-in adapter expects (one of):
#   reference-corpora-input/<dataset>/<class>/<*.png|jpg>            (cropped per-class)
#   reference-corpora-input/<dataset>/labels.json + chips/<*.png>    (DOTA-style)

docker compose build assets   # picks up the drop-in
```

The fetcher silently skips any dataset whose drop-in directory is missing — the build always succeeds with whatever's present.

## Adding manifest entries (Wikimedia / NARA / NASA / DVIDS)

Edit [`scripts/manifests/<source>.json`](../../scripts/manifests/). Schema:

```json
{
  "source_dataset": "wikimedia",
  "default_license_spdx": "CC-BY-SA-4.0",
  "platforms": [
    {
      "platform_name": "F-16 Fighting Falcon",
      "view_domain": "ground",
      "items": [
        {
          "url": "https://upload.wikimedia.org/.../F-16C_Fighting_Falcon.jpg",
          "sha256": "<optional but recommended>",
          "license_spdx": "PD-USGov",
          "attribution": "USAF / Wikimedia Commons"
        }
      ]
    }
  ]
}
```

Per-item `license_spdx` and `attribution` override the dataset defaults — important for Wikimedia where each photo carries its own terms.

Then rebuild assets and `docker compose up -d --force-recreate assets` to push the new digest. Volume rsync picks up the changes; admin "Re-seed" repopulates the DB.

## Failure modes

- **Build hangs on HF auth.** HF_TOKEN absent or invalid — adapter logs "skipped" and continues. Set HF_TOKEN in `.env` and rebuild.
- **Build aborts with "produced 0 chips across all adapters".** Every adapter skipped (no token, no drop-ins, no manifest entries). Either populate at least one source or `REFERENCE_CORPORA_ENABLED=0` for a slim build.
- **Auto-seed never fires.** Check `REFERENCE_DB_AUTO_SEED` (default `1`); inspect backend logs for `reference auto-seed: 0 platforms — enqueueing`; verify worker is healthy (`docker compose ps worker`).
- **Worker task fails per-dataset.** Look for `{"type":"error", "dataset":"...", "detail":"..."}` on the WS topic; the task continues with other datasets. Common causes: inference-sam3 not warm, mis-sized chip (8×8 px crops get skipped silently).
- **Volume stale after rebuild.** Confirm assets entrypoint rsynced: `docker compose logs assets | grep -i rsync`. If digest mismatch wasn't caught, force rebuild and restart: `docker compose up -d --force-recreate assets`.

## Cross-references

- [why-bake-reference-corpora-into-assets.md](../decisions/why-bake-reference-corpora-into-assets.md)
- [why-celery-task-from-lifespan.md](../decisions/why-celery-task-from-lifespan.md)
- [adding-a-reference-dataset.md](../conventions/adding-a-reference-dataset.md)
- [reference-platform-baker.md](../backend/reference-platform-baker.md)
