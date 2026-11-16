#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Администраторски настройки за 24/7 работа на лаптопа като камера:
    * без sleep/hibernate, екранът изгасва след 1 мин (пести ток, камерата работи)
    * затваряне на капака = "Do nothing"
    * изключен USB selective suspend (USB камерата не "заспива")
    * задача в Task Scheduler: старт при вход + watchdog на всеки 5 мин
#>
param(
    [Parameter(Mandatory)] [string]$Root,
    [Parameter(Mandatory)] [string]$PythonW,
    [Parameter(Mandatory)] [string]$TaskUser,
    [switch]$BatteryToo,
    [switch]$NoPower,
    [switch]$NoTask,
    [string]$TaskName = 'HiddenCamera'
)
$ErrorActionPreference = 'Stop'
function Ok($t) { Write-Host "  OK  $t" -ForegroundColor Green }

try {
    if (-not $NoPower) {
        Write-Host 'Захранване (powercfg)...' -ForegroundColor Cyan
        # Sleep / Hibernate: никога (на захранване)
        powercfg /change standby-timeout-ac 0
        powercfg /change hibernate-timeout-ac 0
        powercfg /change monitor-timeout-ac 1
        # Капак: 0 = Do nothing, 1 = Sleep, 2 = Hibernate, 3 = Shut down
        powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
        # USB selective suspend: изключен
        powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
        if ($BatteryToo) {
            powercfg /change standby-timeout-dc 0
            powercfg /change hibernate-timeout-dc 0
            powercfg /change monitor-timeout-dc 1
            powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
            powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
            Ok 'приложено и за батерия'
        }
        powercfg /setactive SCHEME_CURRENT
        Ok 'sleep=никога, капак=нищо, екран изгасва след 1 мин, USB suspend=off'
    }

    if (-not $NoTask) {
        Write-Host "Task Scheduler ($TaskName)..." -ForegroundColor Cyan
        $action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Root\main.py`"" -WorkingDirectory $Root
        $atLogon = New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
        $atLogon.Delay = 'PT20S'
        # Watchdog: на всеки 5 мин. Ако приложението работи, новият процес веднага излиза (single-instance lock).
        $watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
            -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
        # Interactive = работи в сесията на потребителя (нужно за достъп до камера/микрофон)
        $principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atLogon, $watchdog) `
            -Settings $settings -Principal $principal -Description 'Hidden Camera — motion/audio security recorder' -Force | Out-Null
        Ok "задачата '$TaskName' е регистрирана за $TaskUser"
    }
    exit 0
} catch {
    Write-Host "ГРЕШКА: $_" -ForegroundColor Red
    Read-Host 'Натиснете Enter за изход'
    exit 1
}
