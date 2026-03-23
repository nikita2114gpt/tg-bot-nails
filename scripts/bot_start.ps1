# Запуск TG-бота (новое окно run_bot.bat), если ещё не запущен — как в bot_control.bat [1].
$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like '*python* -m app.main*' -or $_.CommandLine -like '*run_bot.bat*'
}
if ($running) {
    Write-Host "Бот уже запущен."
    exit 1
}

Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "run_bot.bat" -WorkingDirectory $RepoRoot
Write-Host "Бот запущен (отдельное окно run_bot.bat)."
exit 0
