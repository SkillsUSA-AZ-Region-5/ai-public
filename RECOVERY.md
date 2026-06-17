# Disaster recovery

What it takes to bring this box back after a drive failure. Two halves: **rebuild the
software** (from this repo + INSTALL.md) and **restore the live data** (from a backup
bundle). The software is reproducible; the data is not, so the data backups are what
actually save you.

## What lives where (and how it survives a dead drive)

| Thing | Lives on | Survives drive failure if... |
|---|---|---|
| Code, docs, SkillsUSA PDFs/cards | this repo (Windows drive) | the repo is pushed to a **git remote** (or copied offsite) |
| Secrets (`.env`) | Windows drive (gitignored) | included in the **data backup bundle** (it is) |
| OpenWebUI accounts/chats/settings | WSL ext4 `/srv/.../openwebui/webui.db` | in the **backup bundle** (`webui.db`) |
| OpenWebUI Knowledge index + uploads | WSL ext4 `/srv/.../openwebui/{vector_db,uploads}` | in the bundle (`openwebui-files.tar.gz`) |
| Mem0 memories | WSL ext4 `/srv/.../qdrant` | in the bundle (`qdrant.tar.gz`) |
| MinerU images (38+43 GB) | Docker in WSL | rebuildable from `mineru/`; or `docker save` them |
| Hermes Agent config/state/secrets | WSL `/root/.hermes` | in the bundle when present (`hermes-data.tar.gz`) |
| Hermes Agent source/image | WSL `/opt/hermes-agent` + Docker | rebuildable from upstream (`hermes/hermes-setup.md`); live config/state restores from bundle |
| ComfyUI image + Flux model (~17GB) | Docker in WSL + `/srv/.../comfyui/models` | rebuildable + re-downloadable (`comfyui/COMFYUI.md`); not in the bundle |
| LiteLLM virtual keys + spend | WSL ext4 `/srv/.../litellm-db` (Postgres) | in the bundle (`litellm-db.dump`) |
| LM Studio models | LM Studio app dir | re-download (documented in INSTALL.md) |

## Keep these off this drive (the two ongoing jobs)

1. **The repo.** Push to a private git remote. It's already a git repo; add a remote
   and push. ~40 MB, includes the SkillsUSA PDFs. Do this again after meaningful changes
   (or just commit + push regularly).
2. **The data.** Run `scripts/backup-stack.ps1`. It writes a verified, self-contained
   bundle to `Documents\ai-stack-backups\stack-<timestamp>\`. Then your own offsite sweep
   moves that folder to a NAS / external drive / cloud. The bundle contains `.env`
   (secrets), so the offsite destination must be trusted/encrypted.

```powershell
# data backup (verifies itself; bundle lands in Documents\ai-stack-backups)
powershell -ExecutionPolicy Bypass -File scripts\backup-stack.ps1
# check an existing bundle anytime:
wsl -d Ubuntu-24.04 -u root -- python3 /mnt/c/.../scripts/verify_backup.py /mnt/c/.../ai-stack-backups/stack-<ts>
```

## Restore, from bare metal

1. **Rebuild the infrastructure.** Follow [INSTALL.md](INSTALL.md) stages 0-2 (LM Studio,
   WSL2 + docker-ce, GPU toolkit) and create the ext4 data dirs (stage 2 step 5). Stop
   before `docker compose up` so you can drop the data in first.
2. **Get the repo back.** Clone it from your remote to
   `C:\Users\<you>\Documents\local-ai-stack` (or anywhere; it self-locates).
3. **Restore `.env`.** Copy it from the backup bundle to the repo root.
4. **Restore the WSL data** (distro name = `WSL_DISTRO` in `.env`):
   ```bash
   # from inside WSL as root; <B> = /mnt/c/.../ai-stack-backups/stack-<ts>
   mkdir -p /srv/local-ai-stack/openwebui /srv/local-ai-stack/qdrant /srv/local-ai-stack/litellm-db
   tar xzf <B>/openwebui-files.tar.gz -C /srv/local-ai-stack/openwebui      # vector_db, uploads, cache
   tar xzf <B>/qdrant.tar.gz          -C /srv/local-ai-stack                # qdrant/
   rm -f /srv/local-ai-stack/openwebui/webui.db-wal /srv/local-ai-stack/openwebui/webui.db-shm
   cp <B>/webui.db /srv/local-ai-stack/openwebui/webui.db
   if [ -f <B>/hermes-data.tar.gz ]; then tar xzf <B>/hermes-data.tar.gz -C /root; fi
   ```
5. **Restore LiteLLM Postgres.** Start only the database, restore the custom dump, then stop it:
   ```bash
   cd /mnt/c/Users/<you>/Documents/local-ai-stack
   docker compose up -d litellm-db
   cat <B>/litellm-db.dump | docker compose exec -T litellm-db sh -lc \
     'pg_restore -U litellm -d litellm --clean --if-exists --no-owner --no-acl'
   docker compose stop litellm-db
   ```
6. **MinerU images.** Rebuild per [INSTALL.md](INSTALL.md) stage 7, or `docker load` them
   if you saved them with `docker save`.
7. **Start it.** `docker compose up -d`, then `stack status`. In OpenWebUI, reindex
   Knowledge if retrieval looks empty, and run `stack skillsusa smoke` to confirm.
8. **Re-create the Windows bits.** Scheduled tasks, PATH + PowerShell `stack` function,
   Cline config: [INSTALL.md](INSTALL.md) stages 6, 8, 9.
9. **Hermes Agent** (optional). The live `/root/.hermes` config/state is in the data bundle
   if Hermes was installed when the backup ran. The source tree/image are still separate.
   Follow [hermes/hermes-setup.md](hermes/hermes-setup.md): clone INSIDE WSL to
   `/opt/hermes-agent` (the CRLF/s6 gotcha is why), `docker compose build`, then apply
   [hermes/config.snippet.yaml](hermes/config.snippet.yaml) only if you did not restore
   `hermes-data.tar.gz` or intentionally want a fresh Hermes config.
10. **Image generation** (optional). Not in the data bundle either. Rebuild the ComfyUI
   image and re-download the ~17GB Flux model (both steps in
   [comfyui/COMFYUI.md](comfyui/COMFYUI.md) under "First-time setup"), then
   `stack profile image`.

## Operational glue (the stuff outside the repo and the data volumes)

These make the stack actually run/restart. Each is reproducible; here's where from:

| Item | What it does | Restored by |
|---|---|---|
| `MemoriMemoryService` task | starts Mem0 at logon | `scripts/register-tasks.ps1` |
| `StackctlWeb` task | starts the local dashboard on :8090 | `scripts/register-tasks.ps1` |
| `LMStudioScheduler` task | starts the chat/code scheduler on :1235 | `scripts/register-tasks.ps1` |
| `KimiLazyProxy` task | starts the lightweight Kimi wake proxy on :8095 | `scripts/register-tasks.ps1` |
| `KimiLlamaServer` task | optional, loads Kimi backend at logon | `scripts/register-tasks.ps1 -IncludeKimi` |
| `WSL-KeepAlive` task | keeps the WSL VM (Docker) alive | same script |
| `C:\Users\<you>\.wslconfig` | NAT networking + localhost forwarding | copy `.wslconfig.template` (INSTALL stage 2) |
| `/etc/wsl.conf` `systemd=true` | systemd in WSL (docker boot, units) | set during WSL/Docker install (INSTALL stage 2) |
| docker enabled at boot | `systemctl enable docker` | INSTALL stage 2 / install-docker-in-wsl.sh |
| nvidia-container-toolkit | GPU in Docker | install-nvidia-container-toolkit.sh (INSTALL stage 2) |
| repo on user PATH | `stack` resolves anywhere | INSTALL stage 6 |
| PowerShell `stack` function | `stack` works in PS, not just cmd | INSTALL stage 6 |
| LM Studio server settings | serve on :1234, key auth, models | INSTALL stage 1 (manual in LM Studio) |

After a restore, `scripts/register-tasks.ps1` recreates the always-on scheduled tasks in one
command (self-locating, so the repo can live anywhere). Use `-IncludeKimi` only if the
339GB Kimi backend should load at every logon. The rest are INSTALL.md steps.

## What is NOT yet automated (do these yourself)
- Pushing the repo to a remote (one-time `git remote add` + `git push`, then push on changes).
- The offsite sweep of `Documents\ai-stack-backups` (your own script / scheduled task).
- Scheduling `backup-stack.ps1` (wire it into Task Scheduler if you want it routine).
- LM Studio server config and model downloads are GUI/manual (INSTALL stage 1).
