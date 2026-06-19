@echo off
REM DeepCode installer — copies the exes to %LOCALAPPDATA%\DeepCode and puts the
REM CLI on your PATH so `deepcode` works in any terminal. No admin needed.
REM
REM PATH is edited via PowerShell + the registry (REG_EXPAND_SZ), NOT `setx`:
REM setx silently truncates the value at 1024 chars and can destroy your PATH.
setlocal

set "DEST=%LOCALAPPDATA%\DeepCode"
set "HERE=%~dp0"

echo.
echo   Installing DeepCode to %DEST%
echo.

if not exist "%DEST%" mkdir "%DEST%"

if exist "%HERE%DeepCodeCLI.exe" (
    copy /Y "%HERE%DeepCodeCLI.exe" "%DEST%\deepcode.exe" >nul
    echo   - CLI  -^> %DEST%\deepcode.exe
) else (
    echo   ! DeepCodeCLI.exe not found next to this script.
)
if exist "%HERE%DeepCodeGUI.exe" (
    copy /Y "%HERE%DeepCodeGUI.exe" "%DEST%\DeepCodeGUI.exe" >nul
    echo   - GUI  -^> %DEST%\DeepCodeGUI.exe
)

REM --- add %DEST% to the USER PATH safely (registry, no 1024 truncation) ---
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dest='%DEST%';" ^
  "$p=[Environment]::GetEnvironmentVariable('Path','User');" ^
  "$parts=@($p -split ';' | Where-Object { $_ -ne '' });" ^
  "if (-not ($parts | Where-Object { $_.TrimEnd('\') -ieq $dest.TrimEnd('\') })) {" ^
  "  $parts += $dest;" ^
  "  [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User');" ^
  "  Write-Host '   - added '$dest' to your PATH' } else { Write-Host '   - PATH already has '$dest }"

REM --- desktop shortcut to the GUI ---
set "LNK=%USERPROFILE%\Desktop\DeepCode.lnk"
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%DEST%\DeepCodeGUI.exe'; $s.IconLocation='%DEST%\DeepCodeGUI.exe'; $s.Save()" 2>nul
if exist "%LNK%" echo   - desktop shortcut: DeepCode (GUI)

echo.
echo   Done. Open a NEW terminal and run:  deepcode
echo   Or launch the GUI from the desktop shortcut.
echo.
pause
