# Обновление VERONIKA Mate: скачивает последнюю сборку с GitHub.
# Твои личные файлы (.env, config.local.json, память диалогов, лог) не трогаются.
$ErrorActionPreference = 'Stop'
# без полосы прогресса PowerShell 5 качает в разы быстрее
$ProgressPreference = 'SilentlyContinue'
$Repo = '__REPO__'
$Dir  = Split-Path -Parent $MyInvocation.MyCommand.Path
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$verFile = Join-Path $Dir 'version.txt'
$current = if (Test-Path $verFile) { (Get-Content $verFile -Raw).Trim() } else { '' }

Write-Host 'Проверяю обновления VERONIKA...'
$rel = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ 'User-Agent' = 'VERONIKA-updater' }
$latest = $rel.tag_name
if ($current -eq $latest) {
    Write-Host "У тебя уже последняя версия ($current)."
    exit 0
}
$asset = $rel.assets | Where-Object { $_.name -like 'VERONIKA-windows*.zip' } | Select-Object -First 1
if (-not $asset) { throw 'В релизе нет архива VERONIKA-windows.zip' }

Write-Host "Скачиваю $latest (было: $(if ($current) { $current } else { 'не установлено' }))..."
$tmp = Join-Path $env:TEMP 'veronika_update'
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $tmp | Out-Null
$zip = Join-Path $tmp 'update.zip'
Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
Expand-Archive $zip -DestinationPath (Join-Path $tmp 'x') -Force
$src = Join-Path $tmp 'x\VERONIKA'

Write-Host 'Закрываю VERONIKA, если она запущена...'
Get-Process VERONIKA, VeronikaBrain -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 800

# копируем всё, кроме личных файлов и самого обновлятора
robocopy $src $Dir /E /NFL /NDL /NJH /NJS /NP `
    /XF .env config.local.json memory.json auto_program_paths.json user_commands.json reminders.json brain.log 'Обновить.bat' `
    /XD models | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Ошибка копирования (robocopy $LASTEXITCODE)" }

# перенос личных файлов из старой версии (папка Documents\VeronikaMate)
$old = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'VeronikaMate\brain'
$old2 = Join-Path $env:USERPROFILE 'Documents\VeronikaMate\brain'
foreach ($o in @($old, $old2)) {
    foreach ($f in '.env', 'config.local.json', 'memory.json') {
        $dst = Join-Path $Dir "brain\$f"
        $from = Join-Path $o $f
        if (-not (Test-Path $dst) -and (Test-Path $from)) {
            Copy-Item $from $dst
            Write-Host "Перенёс $f из старой версии"
        }
    }
}

Set-Content $verFile $latest
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Готово! Установлена версия $latest. Запускаю..."
# через Проводник, чтобы программа не была привязана к этому окну консоли
Start-Process explorer.exe -ArgumentList ('"' + (Join-Path $Dir 'VERONIKA.exe') + '"')
