<#
Register (or re-register) the scheduled tasks that keep the stack alive across logon:

  MemoriMemoryService  - starts the Mem0 memory service (windowless) at logon.
  StackctlWeb          - starts the stackctl web dashboard (:8090) at logon.
  LMStudioScheduler    - starts the LM Studio chat/code scheduler proxy (:1235).
  KimiLazyProxy        - starts the lightweight Kimi wake proxy (:8095) at logon.
                         It does not load the 339GB model until a request arrives.
  WSL-KeepAlive        - holds the WSL VM up so Docker / OpenWebUI / localhost
                         forwarding don't die when WSL idles out.

  KimiLlamaServer      - OPT-IN (-IncludeKimi): starts the Kimi-K2.7-Code llama.cpp
                         backend (:8096, CPU+RAM) at logon. OFF by default because it
                         pins ~305GB of RAM and takes ~5 min to load 339GB - Kimi is
                         normally on-demand through KimiLazyProxy or `stack kimi start`.

Self-locating: paths are derived from this script's location, so it works no matter
where the repo lives. Idempotent (-Force). Run as the user who logs in (NOT elevated;
the tasks run at Limited level, matching the originals).

  .\register-tasks.ps1                 # create/update the always-on tasks
  .\register-tasks.ps1 -IncludeKimi    # also register the on-demand Kimi server at logon
  .\register-tasks.ps1 -Remove         # delete the always-on tasks
  .\register-tasks.ps1 -IncludeKimi -Remove   # also delete the Kimi task

Exact copies of the original task definitions are in scripts/scheduled-tasks/*.xml
for reference / same-box restore via: Register-ScheduledTask -Xml (cat x.xml -Raw) -TaskName ...
#>
param([switch]$Remove, [switch]$IncludeKimi)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent

$tasks = @{
  'MemoriMemoryService' = @{
    Execute  = Join-Path $repo 'memori\.venv\Scripts\pythonw.exe'
    Argument = 'mem0_service.py'
    WorkDir  = Join-Path $repo 'memori'
  }
  'StackctlWeb' = @{
    Execute  = Join-Path $repo 'memori\.venv\Scripts\pythonw.exe'
    Argument = 'webapp.py'
    WorkDir  = Join-Path $repo 'manage'
  }
  'LMStudioScheduler' = @{
    Execute  = Join-Path $repo 'memori\.venv\Scripts\pythonw.exe'
    Argument = 'lmstudio_scheduler_proxy.py'
    WorkDir  = Join-Path $repo 'manage'
  }
  'KimiLazyProxy' = @{
    Execute  = Join-Path $repo 'memori\.venv\Scripts\pythonw.exe'
    Argument = 'kimi_lazy_proxy.py'
    WorkDir  = Join-Path $repo 'manage'
  }
  'WSL-KeepAlive' = @{
    Execute  = 'wscript.exe'
    Argument = '"' + (Join-Path $repo 'scripts\wsl-keepalive.vbs') + '"'
    WorkDir  = $null
  }
}

# Kimi backend lives OUTSIDE the repo (binary + 339GB GGUF). Override via env if your paths
# differ; these defaults use the current Windows user's home folder. Flags mirror `stack kimi start`.
if ($IncludeKimi) {
  $kimiExe   = if ($env:KIMI_SERVER) { $env:KIMI_SERVER } else { Join-Path $HOME 'llamacpp\llama-server.exe' }
  $kimiModel = if ($env:KIMI_MODEL)  { $env:KIMI_MODEL }  else { Join-Path $HOME 'models\Kimi-K2.7-Code-GGUF\UD-Q2_K_XL\Kimi-K2.7-Code-UD-Q2_K_XL-00001-of-00008.gguf' }
  $tasks['KimiLlamaServer'] = @{
    Execute  = $kimiExe
    Argument = "-m `"$kimiModel`" --numa distribute -t 192 --cpu-range 0-191 --cpu-strict 1 -c 8192 --host 127.0.0.1 --port 8096 --no-mmap"
    WorkDir  = Split-Path $kimiExe -Parent
  }
} else {
  Unregister-ScheduledTask -TaskName 'KimiLlamaServer' -Confirm:$false -ErrorAction SilentlyContinue
}

foreach ($name in $tasks.Keys) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
  if ($Remove) { Write-Host "removed $name"; continue }

  $t = $tasks[$name]
  if ($t.WorkDir) {
    $action = New-ScheduledTaskAction -Execute $t.Execute -Argument $t.Argument -WorkingDirectory $t.WorkDir
  } else {
    $action = New-ScheduledTaskAction -Execute $t.Execute -Argument $t.Argument
  }
  $settings = New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
  Register-ScheduledTask -TaskName $name -Action $action `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn) -Settings $settings -Force | Out-Null
  Write-Host "registered $name  ($($t.Execute) $($t.Argument))"
}

if (-not $Remove) {
  Start-ScheduledTask -TaskName 'WSL-KeepAlive' -ErrorAction SilentlyContinue   # bring the VM up now
  Start-ScheduledTask -TaskName 'LMStudioScheduler' -ErrorAction SilentlyContinue
  Start-ScheduledTask -TaskName 'KimiLazyProxy' -ErrorAction SilentlyContinue
  Write-Host "`nWSL-KeepAlive, LMStudioScheduler, and KimiLazyProxy started. Mem0 + web dashboard start at next logon"
  Write-Host "(or now: stack mem0 start ; stack web start ; stack lm-scheduler start ; stack kimi proxy-start)"
  if ($IncludeKimi) {
    Write-Host "KimiLlamaServer registered: loads ~339GB into RAM at next logon (~5 min)."
    Write-Host "(or now: stack kimi start)"
  }
}
