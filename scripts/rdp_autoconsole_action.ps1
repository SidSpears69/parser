param(
    [Parameter(Mandatory = $true)]
    [string]$SessionId
)

$ErrorActionPreference = 'Stop'
$logPath = Join-Path $env:ProgramData 'WebParserRdpAutoConsole\rdp-autoconsole.log'

function Write-Result([string]$message) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message"
}

if ($SessionId -notmatch '^[1-9][0-9]*$') {
    Write-Result "Ignored invalid session ID: $SessionId"
    exit 2
}

$targetSession = [int]$SessionId
$parser = Get-Process -Name 'WebParser' -ErrorAction SilentlyContinue |
    Where-Object { $_.SessionId -eq $targetSession } |
    Select-Object -First 1
if ($null -eq $parser) {
    Write-Result "Session ${targetSession}: WebParser.exe is not running; skipped."
    exit 0
}

try {
Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class WebParserRdpSession
{
    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSQuerySessionInformation(
        IntPtr server, int sessionId, int infoClass, out IntPtr buffer, out int bytesReturned);

    [DllImport("wtsapi32.dll")]
    private static extern void WTSFreeMemory(IntPtr buffer);

    public static int GetConnectionState(int sessionId)
    {
        IntPtr buffer;
        int bytesReturned;
        // WTSConnectState is 8; WTSDisconnected is 4.
        if (!WTSQuerySessionInformation(IntPtr.Zero, sessionId, 8, out buffer, out bytesReturned))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            if (bytesReturned < sizeof(int))
                throw new InvalidOperationException("The session state response is too short.");
            return Marshal.ReadInt32(buffer);
        }
        finally
        {
            WTSFreeMemory(buffer);
        }
    }
}
'@

$tscon = Join-Path $env:SystemRoot 'System32\tscon.exe'
    Start-Sleep -Seconds 3
    $state = [WebParserRdpSession]::GetConnectionState($targetSession)
    if ($state -ne 4) {
        Write-Result "Session ${targetSession}: state $state is not disconnected; skipped."
        exit 0
    }
    Write-Result "Session ${targetSession}: transferring to console."
    $result = Start-Process -FilePath $tscon -ArgumentList @($SessionId, '/dest:console') `
        -WindowStyle Hidden -Wait -PassThru
    Write-Result "Session ${targetSession}: tscon exit code $($result.ExitCode)."
    exit $result.ExitCode
} catch {
    Write-Result "Session ${targetSession}: transfer failed: $($_.Exception.Message)"
    exit 1
}
