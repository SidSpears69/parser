param(
    [string]$PythonExe = '',
    [string]$Browser = '',
    [string]$TlsUrl = 'https://sail-master.ru/'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if (-not $PythonExe) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            $candidate = & $launcher.Source -3.14 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) {
                $PythonExe = ($candidate | Select-Object -Last 1).Trim()
            }
        }
    }
    if (-not $PythonExe) {
        throw 'Python 3.14 not found. Install it, then pass -PythonExe with its full path.'
    }
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Python executable not found: $PythonExe"
    }
    & $PythonExe -c 'import struct,sys; sys.exit(0 if sys.version_info[:2] == (3,14) and struct.calcsize(chr(80)) == 8 else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.14 x64 is required.' }
    & $PythonExe -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv.' }
}

& $venvPython -c 'import struct,sys; sys.exit(0 if sys.version_info[:2] == (3,14) and struct.calcsize(chr(80)) == 8 else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Existing .venv does not use Python 3.14 x64.' }

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed.' }

& $venvPython -m playwright install firefox
if ($LASTEXITCODE -ne 0) { throw 'Playwright Firefox installation failed.' }

Push-Location $projectRoot
try {
    $checkArguments = @('-m', 'scripts.check_windows_server', '--tls-url', $TlsUrl)
    if ($Browser) { $checkArguments += @('--browser', $Browser) }
    & $venvPython @checkArguments
    if ($LASTEXITCODE -ne 0) { throw 'Windows Server readiness check failed.' }
} finally {
    Pop-Location
}
