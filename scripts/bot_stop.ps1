# Остановка процессов бота — как в bot_control.bat [2].
$ErrorActionPreference = "Continue"

$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like '*python* -m app.main*' -or $_.CommandLine -like '*run_bot.bat*'
}
if (-not $procs) {
    Write-Host "Запущенный процесс бота не найден."
    exit 1
}

foreach ($p in $procs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host "Остановлен PID $($p.ProcessId)"
    } catch {
        Write-Host "Не удалось остановить PID $($p.ProcessId): $_"
    }
}
Write-Host "Бот остановлен."
exit 0
