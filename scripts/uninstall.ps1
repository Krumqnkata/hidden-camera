#Requires -Version 5.1
# Премахва автостарта и връща капака към "Sleep". Записите, .venv и .env остават.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    exit
}
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root '.venv\Scripts\python.exe') (Join-Path $Root 'main.py') --stop
Unregister-ScheduledTask -TaskName 'HiddenCamera' -Confirm:$false -ErrorAction SilentlyContinue
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
powercfg /change standby-timeout-ac 30
powercfg /setactive SCHEME_CURRENT
Write-Host 'Задачата е премахната; капак = Sleep; sleep след 30 мин на захранване.' -ForegroundColor Green
Read-Host 'Enter за изход'
