@echo off
REM =========================================================================
REM  Registry - MAIN HOST setup (fully automatic)
REM  Double-click this file. Approve the admin prompt. That's it.
REM  Detects the server's IP, generates/keeps the certificate, wires Apache,
REM  updates hosts/firewall, and refreshes registry-client-setup.bat.
REM  Optional: registry-host-setup.bat /autostart   (also launch the app at logon)
REM  Optional: registry-host-setup.bat 10.0.0.5     (use this IP instead of detected)
REM =========================================================================
setlocal EnableExtensions
set "PS1=%~dp0registry-host-setup.ps1"

if not exist "%PS1%" (
    echo.
    echo registry-host-setup.ps1 was not found next to this file.
    echo Keep both files together in the same folder.
    echo.
    pause
    exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
echo.
pause
