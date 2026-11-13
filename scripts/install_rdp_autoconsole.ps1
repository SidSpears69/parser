param(
    [switch]$Remove,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $ValidateOnly -and -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an elevated PowerShell window.'
}
$taskName = 'WebParser RDP Auto Console'
$installDir = Join-Path $env:ProgramData 'WebParserRdpAutoConsole'
$actionPath = Join-Path $installDir 'rdp_autoconsole_action.ps1'

$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$taskFolder = $service.GetFolder('\')

if ($Remove) {
    $taskFolder.DeleteTask($taskName, 0)
    Remove-Item -LiteralPath $actionPath -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task: $taskName"
    exit 0
}

$task = $service.NewTask(0)
$task.RegistrationInfo.Description = 'Transfer an RDP session running WebParser.exe to the console after disconnect.'
$task.Principal.UserId = 'SYSTEM'
$task.Principal.LogonType = 5
$task.Principal.RunLevel = 1
$task.Settings.Enabled = $true
$task.Settings.StartWhenAvailable = $true
$task.Settings.MultipleInstances = 2
$task.Settings.ExecutionTimeLimit = 'PT1M'

$channel = 'Microsoft-Windows-TerminalServices-LocalSessionManager/Operational'
$trigger = $task.Triggers.Create(0)
$trigger.Subscription = "<QueryList><Query Id='0' Path='$channel'><Select Path='$channel'>" +
    "*[System[Provider[@Name='Microsoft-Windows-TerminalServices-LocalSessionManager'] and EventID=24]]" +
    '</Select></Query></QueryList>'
$null = $trigger.ValueQueries.Create('sessionId', 'Event/UserData/EventXML/SessionID')

$action = $task.Actions.Create(0)
$action.Path = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    $actionPath + '" -SessionId $(sessionId)'

if ($ValidateOnly) {
    $null = $taskFolder.RegisterTaskDefinition($taskName, $task, 1, 'SYSTEM', $null, 5, $null)
    Write-Output 'Task definition validated without installing it.'
    exit 0
}

New-Item -ItemType Directory -Path $installDir -Force | Out-Null
$installedDirectory = Get-Item -LiteralPath $installDir
if ($installedDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "The task directory must not be a reparse point: $installDir"
}
& icacls.exe $installDir /reset | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not reset access rules on $installDir"
}
& icacls.exe $installDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict write access to $installDir"
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'rdp_autoconsole_action.ps1') -Destination $actionPath -Force
& icacls.exe $actionPath /reset | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict write access to $actionPath"
}
$null = $taskFolder.RegisterTaskDefinition($taskName, $task, 6, 'SYSTEM', $null, 5, $null)
Write-Output "Installed scheduled task: $taskName"
Write-Output "Action log: $(Join-Path $installDir 'rdp-autoconsole.log')"
