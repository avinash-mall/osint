# Calibration shipping — bake-and-rsync per-detector temperatures

## Purpose

Ship per-detector temperature scalars (SAM3, DOTA-OBB, LAE-DINO via the
`grounding_dino` layer, YOLOE, SAR-CFAR, MVRSD) so [backend/calibration.py](../../backend/calibration.py)
can rescale raw confidence scores onto a common probability axis before NMS
sorts and the per-class floor filters. Without calibration, SAM3's loud
wide-tailed score distribution systematically out-votes DOTA-OBB's tighter
one and the ensemble underperforms each detector alone — see
[../benchmarks/calibration-ece.md](../benchmarks/calibration-ece.md) and
[../decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md).

## How the volume flow works

```
  assets/static/calibration/         host (checked in)
        |
        | (COPY assets/static/ /seed/)
        v
  /seed/calibration/                 fetcher stage
        |
        v
  /work/static/calibration/          fetcher stage (validated)
        |
        | (COPY --from=fetcher … /opt/baked-calibration/)
        v
  /opt/baked-calibration/            final stage (un-mounted)
        |
        | rsync on container start (entrypoint.sh), digest-gated
        v
  /usr/share/nginx/html/calibration/ assets runtime (RW, on calibration_data)
                                     |  also served via nginx for healthcheck
                                     v
  /data/calibration/                 backend + worker (RO, calibration_data)
                                     |
                                     v
  backend/calibration.py             reads model_temperatures.json on import,
                                     reload_temperatures() on hot bust
```

The named volume `calibration_data` is declared once in
[docker-compose.yml](../../docker-compose.yml) and mounted RW on `assets`,
RO on `backend` and `worker`. The image holds the canonical copy; the
volume is a transport. When the file is absent the backend silently uses
the identity transform (T=1.0 for every detector) — this is the safe
default for fresh installs and dev hosts that haven't measured anything yet.

## Measurement workflow

Run this end-to-end against a live stack on the GPU host you intend to
deploy from. Output gets committed back to the repo and rebuilt into the
image.

1. **Bring up the stack.** `docker compose up -d` and wait for
   `inference-sam3` to report healthy (`docker compose ps`).

2. **Build a triage set:**
   ```bash
   python scripts/build_triage_set.py --out bench/triage/$(date +%Y%m%d)
   ```

3. **Annotate the triage YAML.** Walk each candidate detection and mark
   true / false. The script lays out the YAML inline; the format is
   self-explanatory.

4. **Measure ECE and suggest temperatures:**
   ```bash
   python scripts/measure_calibration_ece.py \
     --inference-url http://localhost:8001 \
     --slice triage \
     --triage-set bench/triage/$(date +%Y%m%d) \
     --output docs/calibration_ece.md
   ```
   This writes both `docs/calibration_ece.md` (human-readable table) and
   `docs/calibration_ece.json` (machine-readable, contains a
   `temperatures` block).

5. **Copy temperatures into the shipped JSON.** Open
   [`assets/static/calibration/model_temperatures.json`](../../assets/static/calibration/model_temperatures.json),
   replace the values under `temperatures`, and update the metadata:
   - `_measured_at`: ISO date (e.g. `"2026-05-28"`).
   - `_measured_against`: one-line slice description
     (e.g. `"triage 2026-05-28 + DOTA val (1284 chips, 6 detectors)"`).

6. **Regenerate the manifest:**
   ```bash
   cd assets/static/calibration
   sha256sum model_temperatures.json | cut -d' ' -f1 > MANIFEST.sha256
   ```

7. **Rebuild assets:**
   ```bash
   docker compose build assets && docker compose up -d assets
   ```
   The entrypoint detects the new MANIFEST.sha256, rsyncs the file onto
   the `calibration_data` volume, and logs
   `[entrypoint] calibration: rsync done`.

8. **Restart backend + worker** so `backend/calibration.py` re-reads on
   module import:
   ```bash
   docker compose restart backend worker
   ```
   Confirm via `/api/inference/dashboard` (the `calibration` block shows
   `model_count` > 0, `models: [...]`, plus `measured_at` / `measured_against` /
   `source` fields drawn from the wrapper metadata).

## Cross-references

- [../backend/calibration-temperature.md](../backend/calibration-temperature.md)
- [../benchmarks/calibration-ece.md](../benchmarks/calibration-ece.md)
- [../decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [reference-corpora-bake.md](reference-corpora-bake.md) — the bake/rsync pattern this mirrors.
