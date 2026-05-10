#!/usr/bin/env bash
# Temporarily restart inference-sam3 with a "video profile" (only SAM3 image
# + SAM3 video + DINOV3_LVD loaded), run a command, then restore the
# default full-stack profile.
#
# Usage:  scripts/run_video_profile.sh <command> [args...]
# Example: scripts/run_video_profile.sh python scripts/video_tracking_stability.py
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=.env
ENV_BACKUP=.env.video_test_backup

cleanup() {
    if [ -f "$ENV_BACKUP" ]; then
        echo "[run_video_profile] restoring $ENV_FILE from backup ..."
        mv "$ENV_BACKUP" "$ENV_FILE"
        echo "[run_video_profile] recreating container with full profile ..."
        docker compose up -d inference-sam3 --force-recreate >/dev/null 2>&1 || true
        echo "[run_video_profile] waiting for /health ..."
        for _ in $(seq 1 60); do
            if curl -sf -m 3 http://172.18.0.2:8001/health >/dev/null 2>&1; then
                echo "[run_video_profile] full profile back up."
                return
            fi
            sleep 5
        done
        echo "[run_video_profile] WARNING: service did not return after restore."
    fi
}
trap cleanup EXIT

echo "[run_video_profile] backing up $ENV_FILE ..."
cp "$ENV_FILE" "$ENV_BACKUP"

# Set video profile: keep DINOV3_LVD on, turn everything else off.
echo "[run_video_profile] applying video profile to $ENV_FILE ..."
sed -i \
    -e 's/^SAM3_LOAD_OPTIONAL_MODELS=.*/SAM3_LOAD_OPTIONAL_MODELS=0/' \
    -e 's/^SAM3_LOAD_DINOV3_SAT=.*/SAM3_LOAD_DINOV3_SAT=0/' \
    -e 's/^SAM3_LOAD_DINOV3_LVD=.*/SAM3_LOAD_DINOV3_LVD=1/' \
    -e 's/^SAM3_LOAD_PRITHVI=.*/SAM3_LOAD_PRITHVI=0/' \
    -e 's/^SAM3_LOAD_TERRAMIND=.*/SAM3_LOAD_TERRAMIND=0/' \
    "$ENV_FILE"
# DOTA_OBB / GROUNDING_DINO use compose default 1; force them off via .env entry.
{
    echo ""
    echo "# video-profile overrides (auto-cleaned by run_video_profile.sh)"
    echo "SAM3_LOAD_DOTA_OBB=0"
    echo "SAM3_LOAD_GROUNDING_DINO=0"
    echo "SAM3_USE_MULTIPLEX=1"
} >> "$ENV_FILE"

echo "[run_video_profile] recreating container with video profile ..."
docker compose up -d inference-sam3 --force-recreate >/dev/null

echo "[run_video_profile] waiting for /health (model preload up to 3 min) ..."
for _ in $(seq 1 60); do
    if curl -sf -m 3 http://172.18.0.2:8001/health >/dev/null 2>&1; then
        echo "[run_video_profile] service ready."
        break
    fi
    sleep 5
done

# Show what's loaded
echo "[run_video_profile] loaded layers:"
curl -s http://172.18.0.2:8001/health | python3 -c "
import json, sys
d = json.load(sys.stdin)
comps = d['replicas'][0]['components']
for k, v in comps.items():
    print(f'    {k}: {v}')
"

echo "[run_video_profile] running: $@"
"$@"
EXIT=$?
echo "[run_video_profile] command exited with $EXIT"
exit $EXIT
