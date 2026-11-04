@rem Bu klasore ait yerel radari kapatir.
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0durdur.ps1"
set "radarExit=%errorlevel%"
if not "%radarExit%"=="0" pause
exit /b %radarExit%
