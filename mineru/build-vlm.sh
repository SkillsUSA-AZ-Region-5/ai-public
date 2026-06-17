#!/usr/bin/env bash
# Launched detached via: systemd-run --unit=mineru-vlm-build --collect bash build-vlm.sh
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec >> vlm-build.log 2>&1
echo "=== MinerU VLM build START $(date) ==="
docker build -t mineru:vlm -f Dockerfile.vlm .
echo "=== MinerU VLM build END rc=$? $(date) ==="
