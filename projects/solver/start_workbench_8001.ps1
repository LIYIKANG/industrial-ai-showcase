# EFESO Operations AI Workbench launcher (wraps start_workbench_8001.bat)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat  = Join-Path $here 'start_workbench_8001.bat'
if (-not (Test-Path $bat)) { Write-Error "Missing: $bat"; exit 1 }
# Hand off to the .bat. Use cmd /c so the new console window can be interactive.
Start-Process -FilePath $bat -WorkingDirectory $here -WindowStyle Normal
