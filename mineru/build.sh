#!/usr/bin/env bash
# MinerU image build. Launched detached via:
#   systemd-run --unit=mineru-build bash <this script>
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec >> build.log 2>&1          # all output -> build.log
echo "=== MinerU build START $(date) ==="
docker build -t mineru:latest -f Dockerfile .
rc=$?
echo "=== MinerU build END rc=$rc $(date) ==="
