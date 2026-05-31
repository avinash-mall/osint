# Principal Security Audit — 2026-05-31

Scope: automated + manual review of backend, worker, inference service, frontend, Docker Compose, and deployment templates. Remediation was applied after approval on 2026-05-31.

## Findings

| Severity | File path | Description | Impact |
|---|---|---|---|
| Critical | `.env.example#L5` | A live-looking Hugging Face token was committed in the template. | Remediated in code by removing the token from `.env.example`; rotate/revoke the exposed token outside git. |
| High | `docker-compose.yml#L63-L65` | Backend containers started with known fallback admin credentials and a known session-signing key when `.env` was missing or incomplete. | Remediated by requiring `ADMIN_PASSWORD` and `SESSION_SECRET` at Compose interpolation time; copied `.env.example` values are blank so startup fails until set. |
| High | `backend/routers/ingest.py#L266-L268`, `backend/worker_legacy.py#L493-L508` | `/api/ingest` accepts arbitrary `image_url`; the Celery worker performs worker-side HTTP(S) fetches. | Remediated by disabling remote imagery URLs by default, adding explicit host allowlisting, rejecting private/link-local/multicast/reserved DNS answers, and capping remote response bytes. |
| High | `backend/files.py#L17-L30` | Multipart upload streaming had no byte ceiling across imagery, FMV, documents, audio, vectors, and model datasets. | Remediated with `MAX_UPLOAD_BYTES` and 413 cleanup during streaming writes. |
| High | `backend/main.py#L2036-L2111`, `backend/routers/graph.py#L891-L976`, `backend/routers/operational_entities.py#L189-L660`, `frontend/src/components/GaiaMap.tsx#L1039-L1060` | Detection-target candidate approve/reject/promote, operational-entity review/link/merge actions, and graph contradict trusted client-supplied analyst identity; candidate review rows also updated without `status='pending'`. | Remediated by taking reviewer identity from `SessionUser`, removing reviewer payloads, and returning 409 for already-reviewed detection-target candidate rows. |
| Medium | `backend/main.py#L95-L101` | CORS used `allow_origins=["*"]` with `allow_credentials=True` while `get_cors_origins()` was unused. | Remediated by wiring `CORS_ORIGINS` into `CORSMiddleware`. |
| Medium | `backend/fmv_helpers.py#L30-L84` | `ffprobe` and `ffmpeg` subprocesses ran without timeouts in synchronous upload paths. | Remediated with `FMV_PROBE_TIMEOUT_S` and `FMV_TRANSCODE_TIMEOUT_S` guarded subprocess calls. |
| Medium | `docker-compose.yml#L118-L123` | Optional `llm-local-proxy` profile used `network_mode: host` and bound `0.0.0.0:18001`. | Remediated by binding the proxy listener to `127.0.0.1`. |
| Low | `frontend/vite.config.ts` | Production build emitted a 1.25 MB main JS chunk and a 388 KB HLS chunk. | Remediated with explicit vendor chunks; main entry is now ~461 KB and the large-chunk warning is gone. |

## Automated Checks Run

| Check | Result |
|---|---|
| `python3 -m compileall -q backend inference-sam3 scripts` | Passed |
| `npm run build` in `frontend/` | Passed; vendor chunking removed the large-chunk warning. Vite still notes unresolved runtime font URLs. |
| Pattern scans with `rg` for broad exceptions, subprocesses, SQL construction, network calls, file serving, secrets, CORS, and uploads | Findings above |
| Targeted backend pytest sample | Not run: `/usr/bin/python3: No module named pytest` |

## Proposed Diffs For Critical / High Findings

### 1. Remove Committed Token Placeholder

```diff
diff --git a/.env.example b/.env.example
--- a/.env.example
+++ b/.env.example
@@
-HF_TOKEN=<redacted>
+# Optional build-time Hugging Face token for gated model weights.
+# Never commit a real token; pass it via your local .env or BuildKit secret.
+HF_TOKEN=
```

Operational follow-up outside git: rotate/revoke the exposed token.

### 2. Fail Fast On Missing Production Secrets

```diff
diff --git a/docker-compose.yml b/docker-compose.yml
--- a/docker-compose.yml
+++ b/docker-compose.yml
@@
-      - ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
-      - ADMIN_PASSWORD=${ADMIN_PASSWORD:-<dev-default>}
-      - SESSION_SECRET=${SESSION_SECRET:-<dev-default>}
+      - ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
+      - ADMIN_PASSWORD=${ADMIN_PASSWORD:?set ADMIN_PASSWORD in .env to a long random value}
+      - SESSION_SECRET=${SESSION_SECRET:?set SESSION_SECRET in .env to openssl rand -hex 32 output}
```

### 3. Block SSRF In Remote Imagery Ingest

```diff
diff --git a/backend/worker_legacy.py b/backend/worker_legacy.py
--- a/backend/worker_legacy.py
+++ b/backend/worker_legacy.py
@@
 import base64
+import ipaddress
+import socket
 from collections import deque
@@
 from urllib.parse import urlparse
@@
+def _remote_imagery_allowed(url: str) -> None:
+    """Validate an operator-supplied remote imagery URL before worker fetch."""
+    if os.getenv("ALLOW_REMOTE_IMAGERY_URLS", "0") != "1":
+        raise RuntimeError("Remote imagery URLs are disabled; stage files under IMAGERY_PATH/incoming")
+    parsed = urlparse(url)
+    if parsed.scheme not in ("http", "https") or not parsed.hostname:
+        raise RuntimeError(f"Unsupported imagery URL scheme: {parsed.scheme}")
+    allowed_hosts = {
+        h.strip().lower()
+        for h in os.getenv("REMOTE_IMAGERY_ALLOWED_HOSTS", "").split(",")
+        if h.strip()
+    }
+    hostname = parsed.hostname.lower()
+    if allowed_hosts and hostname not in allowed_hosts:
+        raise RuntimeError(f"Remote imagery host {hostname!r} is not allowlisted")
+    try:
+        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
+    except socket.gaierror as exc:
+        raise RuntimeError(f"Remote imagery host did not resolve: {hostname}") from exc
+    for info in infos:
+        ip = ipaddress.ip_address(info[4][0])
+        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
+            raise RuntimeError(f"Remote imagery host resolves to disallowed address {ip}")
+
 def resolve_input_path(image_url: str) -> str:
@@
     if parsed.scheme in ("http", "https"):
+        _remote_imagery_allowed(image_url)
         filename = os.path.basename(parsed.path) or f"{uuid.uuid4()}.tif"
```

### 4. Add A Streaming Upload Byte Ceiling

```diff
diff --git a/backend/files.py b/backend/files.py
--- a/backend/files.py
+++ b/backend/files.py
@@
+import os
 import re
@@
+DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024
+
+def max_upload_bytes() -> int:
+    try:
+        return int(os.getenv("MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES)))
+    except ValueError:
+        return DEFAULT_MAX_UPLOAD_BYTES
+
 def save_upload_file(file: UploadFile, local_path: Path, chunk_size: int = 1024 * 1024) -> int:
@@
     size = 0
+    limit = max_upload_bytes()
     try:
         with local_path.open("wb") as handle:
@@
                 if not chunk:
                     break
                 size += len(chunk)
+                if limit > 0 and size > limit:
+                    raise HTTPException(status_code=413, detail=f"Upload exceeds MAX_UPLOAD_BYTES ({limit})")
                 handle.write(chunk)
+    except Exception:
+        local_path.unlink(missing_ok=True)
+        raise
     finally:
         file.file.close()
```

### 5. Use Session Identity And Pending Guards For Detection-Target Reviews

```diff
diff --git a/backend/main.py b/backend/main.py
--- a/backend/main.py
+++ b/backend/main.py
@@
+def _raise_detection_candidate_404_or_409(cursor, candidate_id: int) -> None:
+    cursor.execute(
+        "SELECT id, status, reviewed_by, reviewed_at FROM detection_target_candidates WHERE id = %s",
+        (candidate_id,),
+    )
+    row = cursor.fetchone()
+    if not row:
+        raise HTTPException(status_code=404, detail="Candidate link not found")
+    raise HTTPException(
+        status_code=409,
+        detail={
+            "error": "candidate already reviewed",
+            "status": row["status"],
+            "reviewed_by": row["reviewed_by"],
+            "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
+        },
+    )
+
 @app.post("/api/detection-target-candidates/{candidate_id}/approve")
-def approve_detection_target_candidate(candidate_id: int, req: CandidateLinkDecision = CandidateLinkDecision()):
+def approve_detection_target_candidate(
+    candidate_id: int,
+    user: SessionUser = Depends(get_current_user),
+):
@@
-            WHERE c.id = %s
+            WHERE c.id = %s AND c.status = 'pending'
@@
         if not candidate:
-            raise HTTPException(status_code=404, detail="Candidate link not found")
+            _raise_detection_candidate_404_or_409(cursor, candidate_id)
@@
-            WHERE id = %s
+            WHERE id = %s AND status = 'pending'
@@
-        """, (req.analyst or "analyst", candidate_id))
-        updated = dict(cursor.fetchone())
+        """, (user.username, candidate_id))
+        row = cursor.fetchone()
+        if not row:
+            _raise_detection_candidate_404_or_409(cursor, candidate_id)
+        updated = dict(row)
@@
-            "reviewed_by": req.analyst or "analyst",
+            "reviewed_by": user.username,
@@
 @app.post("/api/detection-target-candidates/{candidate_id}/reject")
-def reject_detection_target_candidate(candidate_id: int, req: CandidateLinkDecision = CandidateLinkDecision()):
+def reject_detection_target_candidate(
+    candidate_id: int,
+    user: SessionUser = Depends(get_current_user),
+):
@@
-            WHERE id = %s
+            WHERE id = %s AND status = 'pending'
@@
-        """, (req.analyst or "analyst", candidate_id))
+        """, (user.username, candidate_id))
         row = cursor.fetchone()
         if not row:
-            raise HTTPException(status_code=404, detail="Candidate link not found")
+            _raise_detection_candidate_404_or_409(cursor, candidate_id)
```

```diff
diff --git a/backend/routers/graph.py b/backend/routers/graph.py
--- a/backend/routers/graph.py
+++ b/backend/routers/graph.py
@@
-from fastapi import APIRouter, HTTPException, Query
+from fastapi import APIRouter, Depends, HTTPException, Query
+from auth import SessionUser, get_current_user
@@
-def promote_candidate_edge(candidate_id: int, req: GraphPromoteRequest = GraphPromoteRequest()):
+def promote_candidate_edge(
+    candidate_id: int,
+    user: SessionUser = Depends(get_current_user),
+):
@@
-    analyst = (req.analyst or "analyst").strip() or "analyst"
+    analyst = user.username
@@
-            WHERE id = %s
+            WHERE id = %s AND status = 'pending'
```

```diff
diff --git a/frontend/src/components/GaiaMap.tsx b/frontend/src/components/GaiaMap.tsx
--- a/frontend/src/components/GaiaMap.tsx
+++ b/frontend/src/components/GaiaMap.tsx
@@
-      await axios.post(`${API_URL}/api/detection-target-candidates/${candidateId}/approve`, { analyst: '<client>' }, { timeout: 12000 });
+      await axios.post(`${API_URL}/api/detection-target-candidates/${candidateId}/approve`, null, { timeout: 12000 });
@@
-      await axios.post(`${API_URL}/api/detection-target-candidates/${candidateId}/reject`, { analyst: '<client>' }, { timeout: 12000 });
+      await axios.post(`${API_URL}/api/detection-target-candidates/${candidateId}/reject`, null, { timeout: 12000 });
```

The graph diff should also mirror the 404/409 helper from `backend/main.py` to avoid returning 404 for already-reviewed rows; I kept the snippet focused on the security-critical identity and pending guard.
