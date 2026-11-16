#Requires -Version 5.1
<#
.SYNOPSIS
  Пълна инсталация на Hidden Camera: venv, пакети, .env, права за камера/микрофон,
  Google авторизация, Windows настройки (с администраторски права) и автостарт.

.PARAMETER BatteryToo
  Прилага "не заспивай / нищо при затваряне на капака" и на батерия (по подразбиране само на захранване).
.PARAMETER NoAdmin
  Пропуска стъпката с администраторски права (powercfg + Task Scheduler).
.PARAMETER NoStart
  Не стартира приложението накрая.
#>
param(
    [switch]$BatteryToo,
    [switch]$NoAdmin,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Venv = Join-Path $Root '.venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$VenvPyW = Join-Path $Venv 'Scripts\pythonw.exe'

function Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    !!  $text" -ForegroundColor Yellow }

Write-Host '=============================================' -ForegroundColor Magenta
Write-Host '   Hidden Camera — инсталация за Windows' -ForegroundColor Magenta
Write-Host '=============================================' -ForegroundColor Magenta

# ---------------------------------------------------------------- 1. Python
Step 1 'Търся Python 3.12...'
$PyCmd = $null
$PyArgs = @()
try { & py -3.12 --version *> $null; if ($LASTEXITCODE -eq 0) { $PyCmd = 'py'; $PyArgs = @('-3.12') } } catch {}
if (-not $PyCmd) {
    try {
        $v = & python --version 2>&1
        if ($v -match 'Python 3\.12') { $PyCmd = 'python' }
    } catch {}
}
if (-not $PyCmd) {
    Warn 'Python 3.12 не е намерен.'
    $ans = Read-Host '    Да го инсталирам ли чрез winget? (y/n)'
    if ($ans -match '^[yYдД]') {
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $PyCmd = 'py'; $PyArgs = @('-3.12')
    } else {
        throw 'Инсталирайте Python 3.12 от https://www.python.org/downloads/ и пуснете отново.'
    }
}
Ok ("{0} {1}" -f $PyCmd, ($PyArgs -join ' '))

# ---------------------------------------------------------------- 2. venv
Step 2 'Създавам виртуална среда (.venv)...'
if (-not (Test-Path $VenvPy)) {
    & $PyCmd @PyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'venv не успя' }
    Ok 'създадена'
} else { Ok 'вече съществува' }

# ---------------------------------------------------------------- 3. пакети
Step 3 'Инсталирам пакетите от requirements.txt...'
& $VenvPy -m pip install --upgrade pip --disable-pip-version-check -q
& $VenvPy -m pip install -r (Join-Path $Root 'requirements.txt') --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'pip install не успя' }
Ok 'пакетите са инсталирани'

# ---------------------------------------------------------------- 4. .env / папки
Step 4 'Конфигурация (.env, secrets/)...'
New-Item -ItemType Directory -Force (Join-Path $Root 'secrets') | Out-Null
$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root '.env.example') $EnvFile
    Warn '.env е създаден от .env.example — попълнете Telegram/Drive настройките в него.'
} else { Ok '.env съществува' }

# ---------------------------------------------------------------- 5. поверителност
Step 5 'Разрешавам достъп до камера и микрофон за десктоп приложения (HKCU)...'
$cam = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore'
foreach ($cap in 'webcam', 'microphone') {
    foreach ($key in "$cam\$cap", "$cam\$cap\NonPackaged") {
        if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
        Set-ItemProperty -Path $key -Name 'Value' -Value 'Allow' -Type String
    }
}
Ok 'Settings > Privacy > Camera/Microphone > "Let desktop apps access..." = On'

# ---------------------------------------------------------------- 6. ffmpeg
Step 6 'Проверявам ffmpeg...'
$ff = & $VenvPy -c "import sys; sys.path.insert(0, r'$Root'); from hcam.winutils import find_ffmpeg; print(find_ffmpeg() or '')"
if ($ff) { Ok $ff } else { Warn 'ffmpeg не е намерен — инсталирайте: winget install Gyan.FFmpeg' }

# ---------------------------------------------------------------- 7. Google Drive
Step 7 'Връзка с Google Drive...'
& $VenvPy (Join-Path $Root 'main.py') --setup-script --if-needed
$cred = Join-Path $Root 'secrets\credentials.json'
$tok = Join-Path $Root 'secrets\token.json'
if (-not (Test-Path $cred)) {
    # Google Drive API (разширено) не се ползва — нищо за правене
} elseif (-not (Test-Path $tok)) {
    Write-Host '    Ще се отвори браузър — влезте с Google акаунта, в който да се качват клиповете.'
    & $VenvPy (Join-Path $Root 'main.py') --auth-gdrive
} else {
    Ok 'token.json вече съществува (за нов вход: python main.py --auth-gdrive)'
}

# ---------------------------------------------------------------- 8. админ стъпка
if (-not $NoAdmin) {
    Step 8 'Windows настройки (захранване, капак, USB, автостарт) — изисква администратор...'
    $TaskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $tweaks = Join-Path $PSScriptRoot 'windows-tweaks.ps1'
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$tweaks`"",
        '-Root', "`"$Root`"", '-PythonW', "`"$VenvPyW`"", '-TaskUser', "`"$TaskUser`"")
    if ($BatteryToo) { $argList += '-BatteryToo' }
    try {
        $p = Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -Wait -PassThru
        if ($p.ExitCode -eq 0) { Ok 'администраторските настройки са приложени' }
        else { Warn "администраторската стъпка завърши с код $($p.ExitCode)" }
    } catch {
        Warn 'UAC беше отказан — пуснете по-късно: scripts\windows-tweaks.ps1 (като администратор)'
    }
}

# ---------------------------------------------------------------- 9. старт
if (-not $NoStart) {
    Step 9 'Стартирам приложението във фонов режим...'
    & (Join-Path $PSScriptRoot 'start.ps1')
}

Write-Host "`nГотово! Лог: $Root\app.log    Статус: status.bat    Спиране: stop.bat" -ForegroundColor Green
