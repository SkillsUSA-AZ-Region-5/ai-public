<#
Expose selected WSL/Docker service ports to the LAN.  ***RUN AS ADMINISTRATOR.***

Why: WSL NAT only forwards container ports to Windows *localhost*, not the LAN. For each
port this adds:
  - a netsh portproxy:   <LanIp>:<port>  ->  127.0.0.1:<port>
  - an inbound firewall rule "AIStack <port>"
so other machines can reach the service at  http://<LanIp>:<port>.

Usage (elevated PowerShell):
  .\expose-services.ps1                       # default user-facing ports
  .\expose-services.ps1 -Ports 3000,7860      # custom set
  .\expose-services.ps1 -Remove               # tear the rules back down

SECURITY: only expose what you need. Do NOT expose internal/no-auth services to the LAN:
  - Qdrant 6333 (your memory vectors, no auth)
  - LiteLLM 4000 (holds the master key)
  MinerU API/Gradio are exposed through the Caddy basic-auth proxy.
  SearXNG is internal-only; OpenWebUI reaches it over the Docker network.
#>
#Requires -RunAsAdministrator
param(
  [string]$LanIp = "",                    # default: HOST_LAN_IP from ..\.env, else auto-detect
  [int[]] $Ports = @(3000, 7860, 8000),   # OpenWebUI, MinerU Gradio, MinerU API
  [switch]$Remove
)

if (-not $LanIp) {
  $envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
  if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^HOST_LAN_IP=(.+)$' | Select-Object -First 1
    if ($m) { $LanIp = $m.Matches[0].Groups[1].Value.Trim() }
  }
  if (-not $LanIp) {
    $LanIp = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway }).IPv4Address.IPAddress | Select-Object -First 1
  }
  if (-not $LanIp) { throw "could not determine the LAN IP - pass -LanIp explicitly" }
  Write-Host "using LAN IP $LanIp"
}

foreach ($p in $Ports) {
  $name = "AIStack $p"
  # always clear any prior rule first (idempotent)
  netsh interface portproxy delete v4tov4 listenport=$p listenaddress=$LanIp 2>$null | Out-Null
  Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue

  if ($Remove) { Write-Host "removed exposure for $p"; continue }

  netsh interface portproxy add v4tov4 listenaddress=$LanIp listenport=$p connectaddress=127.0.0.1 connectport=$p
  New-NetFirewallRule -DisplayName $name -Direction Inbound -LocalPort $p -Protocol TCP -Action Allow | Out-Null
  Write-Host "exposed  http://$LanIp`:$p"
}

Write-Host "`n=== active portproxy rules ==="
netsh interface portproxy show v4tov4
