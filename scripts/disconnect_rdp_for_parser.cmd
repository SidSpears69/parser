@echo off
setlocal

powershell.exe -NoProfile -NonInteractive -Command "$parserRdpSessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId; & (Join-Path $env:SystemRoot 'System32\tscon.exe') $parserRdpSessionId /dest:console; exit $LASTEXITCODE"
if errorlevel 1 (
    echo Could not transfer this RDP session to the console.
    echo Run the desktop shortcut as administrator in the same session as Web Parser.
    pause
)
