#!/usr/bin/env bash
# Run this INSIDE the WSL2 Ubuntu distro (not Windows PowerShell):
#   wsl -d Ubuntu
#   bash /mnt/c/Users/<you>/Documents/local-ai-stack/scripts/install-docker-in-wsl.sh
#
# Installs Docker Engine (CE) + compose plugin, enables systemd so the docker
# daemon starts automatically, and adds you to the docker group.
set -euo pipefail

# 1) Enable systemd in WSL (so `docker` daemon runs as a service).
if ! grep -q '^\s*systemd=true' /etc/wsl.conf 2>/dev/null; then
  echo "[*] Enabling systemd in /etc/wsl.conf"
  sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
  echo "[!] systemd enabled. After this script, run 'wsl --shutdown' in PowerShell,"
  echo "    reopen the distro, and re-run this script if docker isn't up."
fi

# 2) Install Docker Engine from the official repo.
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 3) Run docker without sudo.
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker || sudo service docker start

echo
echo "[*] Done. Open a NEW shell (so the docker group applies), then verify:"
echo "    docker run --rm hello-world"
echo "    docker compose version"
