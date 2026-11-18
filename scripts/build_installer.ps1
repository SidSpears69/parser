param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$BuildChannel = '',
    [string]$IsccPath = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
)

$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw 'Version must have the form 1.2.3.'
}
if ($BuildChannel -and $BuildChannel -notmatch '^[A-Za-z0-9_-]+$') {
    throw 'BuildChannel must contain only letters, digits, hyphens or underscores.'
}
if (-not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw "Inno Setup compiler was not found: $IsccPath"
}

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$distRoot = Join-Path $projectRoot 'dist'
if ($BuildChannel) { $distRoot = Join-Path $distRoot $BuildChannel }
$buildDir = Join-Path $distRoot 'WebParser'
$outputDir = Join-Path $distRoot 'installer'
$exe = Join-Path $buildDir 'WebParser.exe'
$versionPath = Join-Path $buildDir 'version.json'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Build the exe first: $exe"
}
if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf)) {
    throw "Build version file is missing: $versionPath"
}
$firefoxBuilds = @(
    Get-ChildItem -Path (Join-Path $buildDir 'ms-playwright\firefox-*\firefox\firefox.exe') `
        -File -ErrorAction SilentlyContinue
)
if (-not $firefoxBuilds) {
    throw 'Bundled Playwright Firefox is missing; rebuild the exe with -BundleFirefox.'
}
$builtVersion = (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8 | ConvertFrom-Json).version
if ($builtVersion -ne $Version) {
    throw "Build version $builtVersion does not match installer version $Version."
}

& $IsccPath "/DBuildVersion=$Version" "/DBuildDir=$buildDir" "-o$outputDir" `
    (Join-Path $projectRoot 'installer\WebParser.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }

$filename = "WebParserSetup-$Version.exe"
$installer = Join-Path $outputDir $filename
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "Installer was not created: $installer"
}
$manifest = @{
    version = $Version
    tag_name = "v$Version"
    filename = $filename
    size = (Get-Item -LiteralPath $installer).Length
    sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json -Compress
[IO.File]::WriteAllText(
    (Join-Path $outputDir 'update.json'), $manifest, [Text.UTF8Encoding]::new($false)
)
Write-Output "Built installer and update manifest: $outputDir"
