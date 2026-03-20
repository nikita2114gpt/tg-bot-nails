@echo off
cd /d %~dp0
title BOT2 Control

:menu
cls
echo ============================
echo        BOT2 CONTROL
echo ============================
echo [1] Включить бота
echo [2] Выключить бота
echo [3] Выход
echo.
set /p choice=Выберите действие (1-3): 

if "%choice%"=="1" goto start_bot
if "%choice%"=="2" goto stop_bot
if "%choice%"=="3" goto end
goto menu

:start_bot
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*python* -m app.main*' -or $_.CommandLine -like '*run_bot.bat*' }; if ($p) { exit 1 } else { exit 0 }"
if "%errorlevel%"=="0" (
    start "" cmd /c ""%~dp0run_bot.bat""
    echo Бот запущен.
) else (
    echo Бот уже запущен.
)
pause
goto menu

:stop_bot
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*python* -m app.main*' -or $_.CommandLine -like '*run_bot.bat*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; exit 0 } else { exit 1 }"
if "%errorlevel%"=="0" (
    echo Бот остановлен.
) else (
    echo Запущенный процесс бота не найден.
)
pause
goto menu

:end
exit /b 0
