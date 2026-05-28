**Decision:** Add a lightweight `POST /embed` endpoint to inference-sam3 instead of computing embeddings inside the backend container or reusing `POST /detect`.

## Why
- **Reuse the loaded model.** DINOv3-SAT is preloaded into GPU VRAM at inference-sam3 startup (eager lifespan). The bake script gets to amortise that startup cost across thousands of chips without paying it per-call.
- **Don't pay the full detection cost.** `POST /detect` runs SAM3 + DOTA-OBB + GDINO + fusion + RemoteCLIP verification — 100–500× the compute the bake actually needs. Calling `/detect` to harvest one embedding field would burn GPU time and add latency for no gain.
- **Avoid putting psycopg2 / DB credentials inside inference-sam3.** The inference container is GPU-heavy and pinned to specific PyTorch/CUDA versions. Adding the backend's DB dep there couples lifecycles unnecessarily. A thin HTTP boundary keeps each container focused on what it owns.
- **Reusable beyond Plan B.** Plan D's analyst-side "what is this object?" lookup needs ad-hoc embeddings on operator-uploaded reference photos. Same endpoint serves both flows.

## What we rejected
- **Bake script inside `inference-sam3`**: would require giving the inference container access to the backend DB. Cross-cuts service boundaries.
- **Calling `/detect` and reading the `embedding` field**: 100×+ unnecessary compute per chip.
- **A new dedicated embedding service**: adds an extra container, an extra port, an extra Docker image, and another piece of state to manage. Wrong scale of solution for a side door that's logically the same model anyway.

## Consequences
- inference-sam3 gains one tiny route, ~25 lines.
- The bake script lives in `backend/scripts/`, alongside its peers.
- The `dinov3_sat` layer must be loaded for `/embed` to return 200 — handled automatically by the route's `_ensure_profile("imagery")` first-line call (matches the pattern of every other route in the file).
