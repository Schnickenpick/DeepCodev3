@echo off
REM Removes DeepCode: deletes %LOCALAPPDATA%\DeepCode, the desktop shortcut, and
REM strips the dir from your user PATH.
setlocal EnableDelayedExpansion

set "DEST=%LOCALAPPDATA%\DeepCode"

echo.
echo   Uninstalling DeepCode...

REM strip %DEST% from user PATH
for /f "skip=2 tokens=2,*" %%A in ('reg query HKCU\Environment /v Path 2^>nul') do set "USERPATH=%%B"
if defined USERPATH (
    set "NEW=!USERPATH:;%DEST%=!"
    set "NEW=!NEW:%DEST%;=!"
    set "NEW=!NEW:%DEST%=!"
    setx Path "!NEW!" >nul
    echo   - removed from PATH
)

if exist "%USERPROFILE%\Desktop\DeepCode.lnk" del "%USERPROFILE%\Desktop\DeepCode.lnk"
if exist "%DEST%" rmdir /S /Q "%DEST%"
echo   - deleted %DEST%

echo.
echo   Done. (Conversation history in %%USERPROFILE%%\.deepcodev3 was left intact.)
echo.
pause
