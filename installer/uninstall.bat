@echo off
REM Removes DeepCode: deletes %LOCALAPPDATA%\DeepCode, the desktop shortcut, and
REM strips the dir from your user PATH (via registry, never `setx`).
setlocal

set "DEST=%LOCALAPPDATA%\DeepCode"

echo.
echo   Uninstalling DeepCode...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dest='%DEST%';" ^
  "$p=[Environment]::GetEnvironmentVariable('Path','User');" ^
  "$parts=@($p -split ';' | Where-Object { $_ -ne '' -and $_.TrimEnd('\') -ine $dest.TrimEnd('\') });" ^
  "[Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User');" ^
  "Write-Host '   - removed from PATH'"

if exist "%USERPROFILE%\Desktop\DeepCode.lnk" del "%USERPROFILE%\Desktop\DeepCode.lnk"
if exist "%DEST%" rmdir /S /Q "%DEST%"
echo   - deleted %DEST%

echo.
echo   Done. (Conversation history in %%USERPROFILE%%\.deepcodev3 was left intact.)
echo.
pause
