@rem Yerel radari baslatir ve tarayicida acar.
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0baslat.ps1"
set "radarExit=%errorlevel%"
if not "%radarExit%"=="0" pause
exit /b %radarExit%
