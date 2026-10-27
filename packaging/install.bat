@echo off
rem Alternative to the setup .exe (console window): double-click, or run "install.bat -InstallDir E:\Research -Lan"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0files\install.ps1" %*
pause
