@echo off
rem VFG voice for Minecraft Java -- the installer for Windows. Double-click it.
rem It runs the PowerShell installer that follows the marker line at the end of this batch part.
setlocal
title VFG voice installer
set "VFG_SELF=%~f0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$s = [IO.File]::ReadAllText($env:VFG_SELF); $m = [char]10 + '#' + '#VFG-POWERSHELL##'; $i = $s.IndexOf($m); if ($i -lt 0) { Write-Host 'VFG: this file is damaged; download it again.'; exit 1 }; Invoke-Expression $s.Substring($i); if (-not $global:VfgInstallOk) { exit 1 }"
set "VFG_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %VFG_EXIT%
##VFG-POWERSHELL##
