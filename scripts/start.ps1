#Requires -Version 5.1
# Стартира Hidden Camera във фонов режим (pythonw.exe, без прозорец).
$Root = Split-Path -Parent $PSScriptRoot
$PyW = Join-Path $Root '.venv\Scripts\pythonw.exe'
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $PyW)) { Write-Host 'Няма .venv — пуснете първо setup.bat' -ForegroundColor Red; exit 1 }

$task = Get-ScheduledTask -TaskName 'HiddenCamera' -ErrorAction SilentlyContinue
if ($task) {
    if ($task.State -eq 'Disabled') { Enable-ScheduledTask -TaskName 'HiddenCamera' | Out-Null }
    Start-ScheduledTask -TaskName 'HiddenCamera'
} else {
    Start-Process -FilePath $PyW -ArgumentList "`"$Root\main.py`"" -WorkingDirectory $Root -WindowStyle Hidden
}
Start-Sleep -Seconds 4
& $Py (Join-Path $Root 'main.py') --status
