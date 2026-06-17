' Launches the WSL keepalive with NO visible window.
' Run by the WSL-KeepAlive scheduled task via: wscript.exe wsl-keepalive.vbs
' The "0" = hidden window; "False" = don't wait (the tail runs detached, holding the VM up).
Const DISTRO = "Ubuntu-24.04"   ' match WSL_DISTRO in .env (VBS can't easily parse it)
CreateObject("WScript.Shell").Run "wsl.exe -d " & DISTRO & " -u root -- tail -f /dev/null", 0, False
