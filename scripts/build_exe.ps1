param(
    [string]$Browser = '',
    [string]$BuildChannel = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if ($BuildChannel -and $BuildChannel -notmatch '^[A-Za-z0-9_-]+$') {
    throw 'BuildChannel must contain only letters, digits, hyphens or underscores.'
}
$distRoot = Join-Path $projectRoot 'dist'
if ($BuildChannel) { $distRoot = Join-Path $distRoot $BuildChannel }
$outputFolder = Join-Path $distRoot 'WebParser'
$exe = Join-Path $outputFolder 'WebParser.exe'
$report = Join-Path $outputFolder 'artifacts\self-test.txt'
$originalPath = $env:Path

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Create .venv with Python 3.14 and install requirements.txt before building.'
}

$rootPrefix = $projectRoot.TrimEnd('\') + '\'
foreach ($candidate in @((Split-Path -Parent $distRoot), $distRoot, $outputFolder)) {
    if (Test-Path -LiteralPath $candidate) {
        $resolved = (Resolve-Path -LiteralPath $candidate).Path
        if (-not $resolved.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -and
            $resolved -ne $projectRoot) {
            throw "Build output resolves outside the project: $resolved"
        }
    }
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
            throw "Move saved configuration, images and logs out of $outputFolder before rebuilding."
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
        --distpath $distRoot --workpath build main.py
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
