@echo off
REM DeepCode installer — copies the exes to %LOCALAPPDATA%\DeepCode and puts the
REM CLI on your PATH so `deepcode` works in any terminal. No admin needed.
setlocal EnableDelayedExpansion

set "DEST=%LOCALAPPDATA%\DeepCode"
set "HERE=%~dp0"

echo.
echo   Installing DeepCode to %DEST%
echo.

if not exist "%DEST%" mkdir "%DEST%"

REM copy whichever exes are sitting next to this script
if exist "%HERE%DeepCodeCLI.exe" (
    copy /Y "%HERE%DeepCodeCLI.exe" "%DEST%\deepcode.exe" >nul
    echo   - CLI  -> %DEST%\deepcode.exe
) else (
    echo   ! DeepCodeCLI.exe not found next to this script.
)
if exist "%HERE%DeepCodeGUI.exe" (
    copy /Y "%HERE%DeepCodeGUI.exe" "%DEST%\DeepCodeGUI.exe" >nul
    echo   - GUI  -> %DEST%\DeepCodeGUI.exe
)

REM add %DEST% to the USER PATH (idempotent — skip if already present)
echo %PATH% | find /I "%DEST%" >nul
if errorlevel 1 (
    for /f "skip=2 tokens=2,*" %%A in ('reg query HKCU\Environment /v Path 2^>nul') do set "USERPATH=%%B"
    if not defined USERPATH (
        setx Path "%DEST%" >nul
    ) else (
        setx Path "!USERPATH!;%DEST%" >nul
    )
    echo   - added %DEST% to your PATH
) else (
    echo   - PATH already contains %DEST%
)

REM optional desktop shortcut to the GUI
set "LNK=%USERPROFILE%\Desktop\DeepCode.lnk"
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%DEST%\DeepCodeGUI.exe'; $s.IconLocation='%DEST%\DeepCodeGUI.exe'; $s.Save()" 2>nul
if exist "%LNK%" echo   - desktop shortcut: DeepCode (GUI)

echo.
echo   Done. Open a NEW terminal and run:  deepcode
echo   Or launch the GUI from the desktop shortcut.
echo.
pause
