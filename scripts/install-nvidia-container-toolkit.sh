#!/usr/bin/env bash
# Run INSIDE the WSL2 Ubuntu distro AFTER install-docker-in-wsl.sh.
# Enables GPU access for containers (needed for the vLLM service).
# Prereq: a recent NVIDIA driver installed on WINDOWS (provides CUDA-on-WSL);
# you do NOT install a Linux GPU driver inside WSL.
set -euo pipefail

# 1) Add the NVIDIA Container Toolkit repo and install it.
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# 2) Wire the toolkit into the Docker daemon and restart it.
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker || sudo service docker restart

# 3) Verify the GPU is visible inside a container.
echo "[*] Verifying GPU passthrough..."
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

echo
echo "[*] If you see both GPUs above, you're ready:"
echo "    docker compose --profile vllm up -d vllm"
