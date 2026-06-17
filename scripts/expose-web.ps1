<#
Open the Windows firewall for the stackctl web dashboard (host process on :8090).

Unlike the WSL/Docker services, the dashboard binds the host directly (WEB_BIND=0.0.0.0),
so it needs ONLY an inbound firewall rule - no netsh portproxy. Same as the LM Studio
and Mem0 host services.

  .\expose-web.ps1            # allow inbound TCP 8090
  .\expose-web.ps1 -Remove    # remove the rule

The dashboard can load/unload models and switch profiles, so it MUST have a password
(WEB_AUTH_PASS in .env) before you expose it. Basic auth rides plain HTTP - trusted LAN only.
#>
#Requires -RunAsAdministrator
param([int]$Port = 8090, [switch]$Remove)

$name = "AIStack web $Port"
Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
if ($Remove) { Write-Host "removed firewall rule for $Port"; return }

New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
Write-Host "firewall opened for TCP $Port. Dashboard reachable at http://<LAN-IP>:$Port (login required)."
