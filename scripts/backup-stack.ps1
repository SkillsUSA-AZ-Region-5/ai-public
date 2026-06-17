<#
Full disaster-recovery backup of the live AI-stack STATE (the part that does NOT
live in git): OpenWebUI accounts/chats/settings + its Knowledge vector index +
uploads, the Qdrant (Mem0) memories, LiteLLM's Postgres DB, Hermes state when
present, and the .env secrets.

Writes a single self-contained, timestamped folder into a Documents staging area.
A separate offsite script can then sweep that folder to a NAS / cloud / external drive.

  .\backup-stack.ps1                       # -> Documents\ai-stack-backups\stack-<ts>\
  .\backup-stack.ps1 -Dest D:\staging      # custom staging folder

What it captures (and why git does NOT):
  - webui.db            WAL-safe SQLite copy (accounts, chats, settings, Knowledge meta)
  - openwebui-files.*   vector_db (the RAG/Knowledge embeddings) + uploads + cache
  - qdrant.tar.gz       Mem0 cross-project memory vectors
  - litellm-db.dump     Postgres custom dump (virtual keys, spend, LiteLLM admin state)
  - hermes-data.tar.gz  /root/.hermes config/state/logs/skills if present
  - .env                the only secrets that can't be regenerated identically
The repo CODE + docs + SkillsUSA PDFs are in git; this is the runtime data only.

NOTE: the bundle contains .env (secrets). Keep the offsite destination trusted/encrypted.
#>
param(
  [string]$Dest = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ai-stack-backups')
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent

# WSL distro from .env (matches the other scripts), default Ubuntu-24.04
$distro = 'Ubuntu-24.04'
$envFile = Join-Path $repo '.env'
if (Test-Path $envFile) {
  $m = Select-String -Path $envFile -Pattern '^WSL_DISTRO=(.+)$' | Select-Object -First 1
  if ($m) { $distro = $m.Matches[0].Groups[1].Value.Trim() }
}
function wsl-root { param([string]$cmd) & wsl.exe -d $distro -u root -- bash -lc $cmd }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = Join-Path $Dest "stack-$stamp"
New-Item -ItemType Directory -Force $outDir | Out-Null
$wslOut = (wsl-root "wslpath '$outDir'").Trim()
$wslRepo = (wsl-root "wslpath '$repo'").Trim()
Write-Host "backup -> $outDir"

# 1) OpenWebUI SQLite, consistent via the sqlite .backup API (WAL-safe). Read the
# bind-mounted DB directly from the WSL host. Write a tiny temp script instead of
# a heredoc so CRLF never leaks into bash delimiters on Windows.
Write-Host "  [1/6] OpenWebUI database (WAL-safe)..."
$dbScript = Join-Path $outDir '_backup_webui.py'
@"
import sqlite3
src = sqlite3.connect('/srv/local-ai-stack/openwebui/webui.db')
dst = sqlite3.connect('$wslOut/webui.db')
with dst:
    src.backup(dst)
dst.close()
src.close()
print('db backup ok')
"@ | Set-Content $dbScript -Encoding ascii
try {
  wsl-root "python3 '$wslOut/_backup_webui.py'"
} finally {
  Remove-Item $dbScript -Force -ErrorAction SilentlyContinue
}

# 2) OpenWebUI file state: the vector index (Knowledge embeddings), uploads, cache.
Write-Host "  [2/6] OpenWebUI vector_db + uploads + cache (this is the big one)..."
wsl-root "tar czf '$wslOut/openwebui-files.tar.gz' -C /srv/local-ai-stack/openwebui vector_db uploads cache" | Out-Null

# 3) Qdrant (Mem0 memories).
Write-Host "  [3/6] Qdrant (Mem0 memories)..."
wsl-root "tar czf '$wslOut/qdrant.tar.gz' -C /srv/local-ai-stack qdrant" | Out-Null

# 4) LiteLLM Postgres DB: virtual keys, spend, admin UI state.
Write-Host "  [4/6] LiteLLM Postgres dump..."
wsl-root "cd '$wslRepo' && docker compose exec -T litellm-db sh -lc 'pg_dump -U litellm -d litellm --format=custom --no-owner --no-acl' > '$wslOut/litellm-db.dump'"

# 5) Hermes state/config if installed. This includes secrets, so it belongs with the trusted bundle.
Write-Host "  [5/6] Hermes state if present..."
wsl-root "if [ -d /root/.hermes ]; then tar --ignore-failed-read --warning=no-file-changed -czf '$wslOut/hermes-data.tar.gz' -C /root .hermes; else echo 'Hermes state not present; skipping'; fi" | Out-Null

# 6) Secrets.
Write-Host "  [6/6] .env secrets..."
Copy-Item $envFile (Join-Path $outDir '.env') -Force

# Restore notes travel with the bundle.
@"
AI-stack data backup taken $stamp from WSL distro '$distro'.
Restore (after recreating infra per the repo's INSTALL.md / RECOVERY.md):

  1. Stop the stack:  wsl -d $distro -u root -- bash -lc 'cd <repo> && docker compose down'
  2. Restore .env to the repo root.
  3. Recreate the data dirs, then:
       wsl -d $distro -u root -- tar xzf <this>/openwebui-files.tar.gz -C /srv/local-ai-stack/openwebui
       wsl -d $distro -u root -- tar xzf <this>/qdrant.tar.gz         -C /srv/local-ai-stack
       copy webui.db -> /srv/local-ai-stack/openwebui/webui.db  (overwrite; remove -wal/-shm)
       wsl -d $distro -u root -- bash -lc 'cd <repo> && docker compose up -d litellm-db'
       wsl -d $distro -u root -- bash -lc 'cd <repo> && cat <this>/litellm-db.dump | docker compose exec -T litellm-db sh -lc "pg_restore -U litellm -d litellm --clean --if-exists --no-owner --no-acl"'
       if hermes-data.tar.gz exists: wsl -d $distro -u root -- tar xzf <this>/hermes-data.tar.gz -C /root
  4. docker compose up -d ; then in OpenWebUI reindex Knowledge if needed.

Contains .env (secrets) - keep this bundle trusted/encrypted offsite.
"@ | Set-Content (Join-Path $outDir 'RESTORE.txt') -Encoding utf8

$size = '{0:N0} MB' -f ((Get-ChildItem $outDir -Recurse -File | Measure-Object Length -Sum).Sum/1MB)
Write-Host "done. bundle: $outDir ($size)"

# Self-verify: a backup you haven't checked is not a backup.
Write-Host "verifying..."
& wsl.exe -d $distro -u root -- python3 (wsl-root "wslpath '$PSScriptRoot/verify_backup.py'").Trim() $wslOut
if ($LASTEXITCODE -ne 0) { Write-Error "VERIFY FAILED - do not trust this bundle"; exit 1 }
Write-Host "move it offsite with your own sweep script, then it's a real DR copy."
