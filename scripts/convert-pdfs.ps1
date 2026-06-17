<#
Batch-convert a folder of PDFs to markdown via the MinerU API.
Run from ANY machine that can reach the server (needs `stack expose` done once).

  .\convert-pdfs.ps1 -Src C:\my\pdfs -Out C:\my\pdfs\md
  .\convert-pdfs.ps1 -Src C:\my\pdfs -Out C:\my\md -Server http://192.0.2.10:8000

Notes:
- backend=pipeline is required on the CPU profile (the default backend tries to
  start a GPU engine and fails). It also works fine on the GPU profile, where it
  runs on CUDA automatically. For a big batch, run `stack profile extract-gpu`
  on the server first; it's much faster but unloads the chat model.
- Already-converted files (existing .md in -Out) are skipped, so it's resumable.
- Requests run one at a time; the server queues internally anyway.
#>
param(
  [Parameter(Mandatory)] [string]$Src,
  [Parameter(Mandatory)] [string]$Out,
  [string]$Server = "",
  [string]$User = "",   # default: MINERU_AUTH_USER from ..\.env
  [string]$Pass = ""    # default: MINERU_AUTH_PASS from ..\.env (never hardcode the secret)
)

# Pull MinerU credentials from .env if not passed (so the password isn't in this file).
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
$HostLanIp = ""
if ((-not $User -or -not $Pass) -and (Test-Path $envFile)) {
  foreach ($line in Get-Content $envFile) {
    if (-not $User -and $line -match '^MINERU_AUTH_USER=(.+)$') { $User = $Matches[1].Trim() }
    if (-not $Pass -and $line -match '^MINERU_AUTH_PASS=(.+)$') { $Pass = $Matches[1].Trim() }
    if (-not $HostLanIp -and $line -match '^HOST_LAN_IP=(.+)$') { $HostLanIp = $Matches[1].Trim() }
  }
}
if (-not $Server) {
  if (-not $HostLanIp) { throw "No server set. Pass -Server or set HOST_LAN_IP in .env." }
  $Server = "http://${HostLanIp}:8000"
}
if (-not $User) { $User = "admin" }
if (-not $Pass) { throw "No MinerU password. Pass -Pass or set MINERU_AUTH_PASS in .env." }

New-Item -ItemType Directory -Force $Out | Out-Null
$pdfs = Get-ChildItem $Src -Filter *.pdf
$i = 0
foreach ($pdf in $pdfs) {
  $i++
  $dest = Join-Path $Out ($pdf.BaseName + ".md")
  if (Test-Path $dest) { Write-Host "[$i/$($pdfs.Count)] skip (done): $($pdf.Name)"; continue }
  Write-Host "[$i/$($pdfs.Count)] converting: $($pdf.Name) ..."
  $raw = curl.exe -s -u "${User}:${Pass}" `
    -F "files=@$($pdf.FullName)" `
    -F "backend=pipeline" `
    -F "return_md=true" `
    "$Server/file_parse"
  try { $resp = $raw | ConvertFrom-Json } catch { Write-Host "  FAILED: bad response: $raw" -ForegroundColor Red; continue }
  if ($resp.status -eq "completed") {
    # one file per request, so take the first (only) result entry
    $md = ($resp.results.PSObject.Properties | Select-Object -First 1).Value.md_content
    [IO.File]::WriteAllText($dest, $md, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "  ok -> $dest"
  } else {
    Write-Host "  FAILED: $($resp.error)" -ForegroundColor Red
  }
}
Write-Host "done. output in $Out"
