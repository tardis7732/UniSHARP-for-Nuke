@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_Nuke17.ps1" %*
set "UNISHARP_INSTALL_EXIT=%ERRORLEVEL%"
echo.
if not "%UNISHARP_INSTALL_EXIT%"=="0" echo Installation failed. Read the error above and README.md.
pause
exit /b %UNISHARP_INSTALL_EXIT%
