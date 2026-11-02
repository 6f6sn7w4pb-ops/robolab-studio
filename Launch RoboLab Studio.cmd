@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-RoboLab.ps1" %*
exit /b %ERRORLEVEL%
