@echo off
setlocal
chcp 65001 >NUL
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set LANG=zh_CN.UTF-8
set VTS_ENABLED=0
set VTS_HEARTBEAT_ENABLED=0
set VTS_RECONNECT_ENABLED=0
rem Wallpaper scene + its interactive overlays follow this display (primary|secondary|<display id>).
set AMADEUS_WALLPAPER_MONITOR=secondary
rem Start straight into wallpaper mode instead of the control panel.
set AMADEUS_WALLPAPER=1
rem Explicit comparison option; normal startup uses the saved Graphics settings.
if /I "%~1"=="--static" set "RENDER_FRAME_SOURCE=static"
cd /d "%~dp0electron"
npm run electron:dev
