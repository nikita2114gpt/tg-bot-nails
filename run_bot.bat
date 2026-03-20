@echo off
cd /d %~dp0

:loop
echo Starting bot...
python -m app.main

echo Bot crashed. Restarting in 5 seconds...
timeout /t 5
goto loop
