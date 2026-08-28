@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0opencode-agent.ps1" %*
exit /b %ERRORLEVEL%
