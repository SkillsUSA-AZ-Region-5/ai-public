# Image generation: ComfyUI + Flux.1-dev (fp8)

Local text-to-image on the stack. [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
runs the [Flux.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) model in fp8,
and OpenWebUI generates images straight from chat. Nothing leaves the box.

| Thing | Value |
|---|---|
| Service | `comfyui` (profile-gated in the main `docker-compose.yml`) |
| Image | `comfyui-cu128:latest`, built locally from [comfyui/Dockerfile](Dockerfile) |
| Model | `flux1-dev-fp8.safetensors` (~17GB, all-in-one fp8 checkpoint) |
| Models dir | `/srv/local-ai-stack/comfyui/models/` (WSL ext4) |
| ComfyUI UI | http://localhost:8188 (127.0.0.1 only) |
| Profile | `stack profile image` (Flux on GPU1, gemma-4-12b split GPU0/CPU) |
| Chat model | `google/gemma-4-12b-qat` (pick it in OpenWebUI) writes/refines the prompts |

## Why a custom build

The RTX 5060 Ti cards are Blackwell (sm_120), which needs PyTorch built against CUDA 12.8.
The cu128 wheels bundle their own CUDA runtime, so the [Dockerfile](Dockerfile) starts from a
slim Python base and installs `torch ... --index-url .../whl/cu128`. The host driver already
supports 12.8 (it runs vLLM and MinerU), so that is all it takes. This is the same
"build it for Blackwell" pattern as MinerU.

## Why fp8 Flux (and no Hugging Face token)

Full Flux.1-dev is bf16 (~24GB) and gated on Hugging Face. Two problems solved at once by
using the **Comfy-Org fp8 repackage** (`Comfy-Org/flux1-dev/flux1-dev-fp8.safetensors`):

1. **It fits a 16GB card.** fp8 is ~12GB resident, so one RTX 5060 Ti runs it.
2. **It is ungated**, so no HF token or license click is needed to download it.

It is also an **all-in-one checkpoint** (model + CLIP + T5 + VAE in one file), so it loads
with the standard `CheckpointLoaderSimple` node and keeps the OpenWebUI workflow simple.

## First-time setup

The container build and model download are already done on this box. To redo from scratch:

```bash
# 1. Model -> WSL ext4 (NOT /mnt/c). ~17GB, ungated.
mkdir -p /srv/local-ai-stack/comfyui/models/checkpoints
curl -fL -C - -o /srv/local-ai-stack/comfyui/models/checkpoints/flux1-dev-fp8.safetensors \
  https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors

# 2. Build the image (cu128, ~10-15 min cold).
cd /mnt/c/Users/<you>/Documents/local-ai-stack
docker compose --profile image build comfyui

# 3. Wire the Flux workflow into OpenWebUI's env (writes COMFYUI_WORKFLOW* to .env).
python3 comfyui/build-owui-workflow.py
```

## Run it

```powershell
stack profile image          # Flux on GPU1 + gemma-4-12b (partial offload); brings ComfyUI + OpenWebUI up
stack profile chat           # done? switch back; this stops ComfyUI and reloads the daily model
```

`stack profile image` is the only way ComfyUI starts (it is gated behind the `image` compose
profile, like vLLM). The other GPU profiles (`chat`, `code`, `extract-gpu`, `vllm`) stop it on
the way in, so VRAM is reclaimed automatically when you switch.

## Generate from OpenWebUI

**How the flow works:** the text model and the image model are separate. You chat to produce the
*prompt text*, then OpenWebUI hands that text to ComfyUI. After an assistant reply, click the
**image icon** on the message ("Generate Image"): OpenWebUI uses that message's text as the Flux
prompt and drops the rendered image back into the message. In image mode the loaded chat model is
**`google/gemma-4-12b-qat`**. Pick it in OpenWebUI's model dropdown (refresh the model list if it
isn't there yet). Do not pick qwen or gemma-26b here: they are not loaded in image mode and would
JIT-load on top of Flux and thrash. Note gemma-4 is a *reasoning* model, so it thinks before it
answers, and it runs with partial CPU offload here (~15 tok/s), so prompt-writing is steady but not
instant. For the fastest prompts, a small non-reasoning instruct model would be a better fit.

**No admin-UI setup needed.** The whole image config is driven by env in `docker-compose.yml`:
engine (`comfyui`), base URL (`http://comfyui:8188`), model (`flux1-dev-fp8.safetensors`), and the
Flux **workflow + node mapping** (`COMFYUI_WORKFLOW` / `COMFYUI_WORKFLOW_NODES`, written into `.env`
by [build-owui-workflow.py](build-owui-workflow.py)). So once you are in image mode you just chat
and click the image icon.

The workflow + node mapping are the important part. Without them OpenWebUI submits its built-in SD
workflow with no model injected, ComfyUI 400s on `ckpt_name 'model.safetensors'`, and OpenWebUI
reports the unhelpful `'NoneType' object is not subscriptable`. The mapping injects the model into
node 30, the prompt into node 6, size into 27, steps/seed into 31 of
[flux-workflow-api.json](flux-workflow-api.json) (the canonical Flux-dev checkpoint graph:
`cfg=1.0`, `sampler=euler`, `scheduler=simple`, `FluxGuidance=3.5`, negative conditioning zeroed
out, `EmptySD3LatentImage`).

If you ever change the workflow, re-run the helper and recreate the container:

```bash
python3 comfyui/build-owui-workflow.py
docker compose up -d --force-recreate open-webui
```

## Verify (independent of OpenWebUI)

```bash
# ComfyUI is up and sees the model:
curl -s http://localhost:8188/object_info/CheckpointLoaderSimple | grep -o flux1-dev-fp8.safetensors

# queue the bundled workflow directly and confirm an image lands in output/:
curl -s -X POST http://localhost:8188/prompt \
  -H 'Content-Type: application/json' \
  -d "{\"prompt\": $(cat /mnt/c/Users/<you>/Documents/local-ai-stack/comfyui/flux-workflow-api.json)}"
ls -t /srv/local-ai-stack/comfyui/output | head
```

## Notes

- **VRAM / GPU layout:** Flux fp8 is ~11.4GB of weights plus several GB of activations, so it wants
  a whole 16GB card. ComfyUI is therefore pinned to **GPU1** via `CUDA_VISIBLE_DEVICES=1` (set in
  `docker-compose.yml`; the compose `device_ids` field does NOT restrict the container under WSL2,
  so the pin is done at the CUDA level). The `image` profile then loads `google/gemma-4-12b-qat`
  with **partial GPU offload** (`--gpu 0.45`, the rest on the 512GB system RAM) so its GPU share is
  only ~3GB per card and does not overflow Flux's card: a 1024x1024 / 20-step image takes ~70s while
  gemma serves chat at ~15 tok/s. A 12B will NOT fully fit beside Flux (LM Studio splits it across
  both GPUs and the GPU1 half overflows), which is why it offloads to CPU. The other trap: a bigger
  chat model (gemma-26b, qwen) fully on GPU; for full-speed big-model chat, switch to `chat`/`code`.
- **Storage:** models, outputs, inputs, and any installed custom nodes live under
  `/srv/local-ai-stack/comfyui/` (WSL ext4), bind-mounted into the container, so rebuilding the
  image keeps them.
- **LAN access:** the UI binds to `127.0.0.1:8188`. Expose it with `stack expose` only if you
  want it on the LAN.
- **Adding models:** drop checkpoints in `models/checkpoints/`, LoRAs in `models/loras/`, etc.
  They appear in ComfyUI without a rebuild.
