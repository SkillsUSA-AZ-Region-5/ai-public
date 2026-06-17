#!/usr/bin/env python3
"""Write the ComfyUI Flux workflow into the stack .env for OpenWebUI.

OpenWebUI's image generation reads two env vars to drive ComfyUI:
  COMFYUI_WORKFLOW        - the ComfyUI graph (API format), as a JSON string
  COMFYUI_WORKFLOW_NODES  - a mapping telling OpenWebUI which node input to set for
                            the model / prompt / size / steps / seed

Without these it falls back to a built-in SD workflow with no model injected, so
ComfyUI rejects it (ckpt_name 'model.safetensors' not found -> HTTP 400, surfaced in
OpenWebUI as "'NoneType' object is not subscriptable"). This script minifies
comfyui/flux-workflow-api.json and writes both vars into the repo .env, which
docker-compose passes to the open-webui container. Re-run it if you change the workflow.

    python3 comfyui/build-owui-workflow.py
    docker compose up -d --force-recreate open-webui
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

workflow = json.dumps(json.load(open(os.path.join(HERE, "flux-workflow-api.json"))),
                      separators=(",", ":"))

# node ids reference comfyui/flux-workflow-api.json: 6=positive prompt (CLIPTextEncode),
# 30=checkpoint (CheckpointLoaderSimple), 27=latent size (EmptySD3LatentImage), 31=KSampler.
nodes = json.dumps([
    {"type": "prompt", "key": "text", "node_ids": ["6"]},
    {"type": "model", "key": "ckpt_name", "node_ids": ["30"]},
    {"type": "width", "key": "width", "node_ids": ["27"]},
    {"type": "height", "key": "height", "node_ids": ["27"]},
    {"type": "steps", "key": "steps", "node_ids": ["31"]},
    {"type": "seed", "key": "seed", "node_ids": ["31"]},
], separators=(",", ":"))

envp = os.path.join(REPO, ".env")
raw = open(envp, "rb").read()
nl = b"\r\n" if b"\r\n" in raw else b"\n"
lines = [l for l in raw.split(nl)
         if not (l.startswith(b"COMFYUI_WORKFLOW=") or l.startswith(b"COMFYUI_WORKFLOW_NODES="))]
while lines and lines[-1].strip() == b"":
    lines.pop()
lines.append(b"COMFYUI_WORKFLOW=" + workflow.encode())
lines.append(b"COMFYUI_WORKFLOW_NODES=" + nodes.encode())
open(envp, "wb").write(nl.join(lines) + nl)
print("wrote COMFYUI_WORKFLOW (%d chars) + COMFYUI_WORKFLOW_NODES to %s" % (len(workflow), envp))
