#Requires -Version 5.1
# Плавно спира Hidden Camera. С -Disable спира и watchdog-а в Task Scheduler (иначе ще се рестартира до 5 мин).
param([switch]$Disable)
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root '.venv\Scripts\python.exe'

if ($Disable -and (Get-ScheduledTask -TaskName 'HiddenCamera' -ErrorAction SilentlyContinue)) {
    try {
        Disable-ScheduledTask -TaskName 'HiddenCamera' -ErrorAction Stop | Out-Null
        Write-Host 'Автостартът е изключен (start.bat го включва отново).' -ForegroundColor Yellow
    } catch {
        Write-Host 'Не мога да изключа задачата без администратор — пуснете stop.bat като администратор.' -ForegroundColor Yellow
    }
}
& $Py (Join-Path $Root 'main.py') --stop
