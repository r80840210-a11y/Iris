@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MisuGram bot (auto-restart)
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

where python >nul 2>nul
if errorlevel 1 (
    echo Python not found in PATH.
    echo Install Python 3.10+ and tick "Add Python to PATH" during setup.
    pause
    exit /b 1
)

echo Bot is running now. Log file: bot.log
echo Close this window to stop the bot.
echo.

:loop
echo ==================== %date% %time% ====================>> bot.log
python bot_ru.py >> bot.log 2>&1
echo ---- %date% %time% : bot stopped, restart in 5 seconds -------->> bot.log
echo.
echo Bot stopped. Restarting in 5 seconds... (close this window to stop for good)
timeout /t 5 /nobreak >nul
goto loop
