# Why RemoteCLIP verifier is default-on for the imagery profile

**Date:** 2026-05-28
**Status:** SUPERSEDED 2026-05-31 — the RemoteCLIP verifier was removed.

RemoteCLIP only re-ranked existing detections (never proposed boxes); its weights were often
absent and its `semantic_margin` contribution measured at ~0 on validated runs. It was removed
permanently. The generic `semantic_margin` / `semantic_verifier` evidence-ranking plumbing in
the backend is retained (it degrades gracefully and can be fed by a future verifier).

See **[removed-fair1m-and-remoteclip.md](removed-fair1m-and-remoteclip.md)** for the rationale
and the full list of code/config/doc changes.
