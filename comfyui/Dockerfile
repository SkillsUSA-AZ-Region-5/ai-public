# ComfyUI for Blackwell (RTX 50-series, sm_120). The trick is PyTorch built against
# CUDA 12.8: the cu128 wheels bundle their own CUDA runtime, so a slim Python base is
# enough and the host driver (already on 12.8 for vLLM/MinerU) supplies the rest.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# PyTorch with CUDA 12.8 (Blackwell sm_120). Pinned major to keep rebuilds reproducible.
RUN pip install --index-url https://download.pytorch.org/whl/cu128 \
      torch torchvision torchaudio

# ComfyUI. Pin a commit via --build-arg COMFYUI_REF=<sha> for reproducible rebuilds.
ARG COMFYUI_REF=master
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI && \
    cd /opt/ComfyUI && git checkout "${COMFYUI_REF}"
WORKDIR /opt/ComfyUI
RUN pip install -r requirements.txt

# models/, output/, input/, custom_nodes/ are bind-mounted from /srv at runtime.
EXPOSE 8188
# --listen 0.0.0.0 so the compose network (OpenWebUI) can reach it. Default front end.
CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
