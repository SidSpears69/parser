param(
    [string]$Browser = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$exe = Join-Path $projectRoot 'dist\WebParser\WebParser.exe'
$report = Join-Path $projectRoot 'dist\WebParser\artifacts\self-test.txt'
$outputFolder = Join-Path $projectRoot 'dist\WebParser'
$originalPath = $env:Path

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Create .venv with Python 3.14 and install requirements.txt before building.'
}

Push-Location $projectRoot
try {
    if (Test-Path -LiteralPath $outputFolder -PathType Container) {
        $extraEntries = Get-ChildItem -LiteralPath $outputFolder -Force |
            Where-Object { $_.Name -notin @('_internal', 'WebParser.exe', 'artifacts') }
        $savedData = Get-ChildItem -LiteralPath (Join-Path $outputFolder 'artifacts') `
            -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -ne $report }
        if ($extraEntries -or $savedData) {
            throw 'Move saved configuration, images and logs out of dist\WebParser before rebuilding.'
        }
    }

    & $python -m pip install --disable-pip-version-check -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Could not install build requirements.' }

    # Do not let PyInstaller collect unrelated DLLs from developer tools on PATH.
    $pythonHome = (& $python -c 'import sys; print(sys.base_prefix)' | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $pythonHome) { throw 'Could not locate base Python.' }
    $env:Path = @(
        (Split-Path -Parent $python),
        $pythonHome,
        (Join-Path $env:SystemRoot 'System32'),
        $env:SystemRoot,
        (Join-Path $env:SystemRoot 'System32\Wbem')
    ) -join ';'

    & $python -m PyInstaller --noconfirm --clean --onedir --windowed `
        --name WebParser --collect-all playwright --collect-all playwright_stealth `
        --distpath dist --workpath build main.py
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw 'Could not build WebParser.exe.'
    }

    $checkArguments = '--self-test'
    if ($Browser) {
        if ($Browser.Contains('"')) { throw 'Browser name must not contain a quotation mark.' }
        $checkArguments += ' --browser "' + $Browser + '"'
    }
    $checkProcess = Start-Process -FilePath $exe -ArgumentList $checkArguments -PassThru
    if (-not $checkProcess.WaitForExit(120000)) {
        Stop-Process -Id $checkProcess.Id -Force
        throw 'Packaged application self-test timed out after 120 seconds.'
    }
    if (-not (Test-Path -LiteralPath $report -PathType Leaf)) {
        throw 'Packaged application did not write a self-test report.'
    }
    Get-Content -LiteralPath $report -Encoding UTF8
    if ($checkProcess.ExitCode -ne 0) { throw 'Packaged application self-test failed.' }

    Write-Output "Built and checked: $exe"
} finally {
    $env:Path = $originalPath
    Pop-Location
}
