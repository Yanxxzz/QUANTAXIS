[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Plan', 'Install', 'Start', 'Stop', 'Status', 'Uninstall', 'Run')]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [string]$Config
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$configPath = (Resolve-Path -LiteralPath $Config).Path
$definition = Get-Content -LiteralPath $configPath -Encoding UTF8 -Raw | ConvertFrom-Json
if ($definition.schema -ne 1 -or $definition.name -notmatch '^PandaAlpha-[A-Za-z0-9-]{1,48}$') {
    throw 'Invalid Panda Alpha service definition'
}
$repositoryPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ($definition.repo -ne $repositoryPath) { throw 'Use the original deployment checkout' }
$pythonPath = (Resolve-Path -LiteralPath $definition.python).Path
$entryPath = Join-Path $PSScriptRoot 'team_service.py'
$serviceName = $definition.name
$accountName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
# These are Windows executable arguments, not interpolated shell commands.
foreach ($argument in @($configPath, $PSCommandPath)) {
    if ($argument.Contains('"') -or $argument.Contains("`n") -or $argument.Contains("`r")) {
        throw 'Unsupported quote/newline in deployment path'
    }
}
$powershellPath = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$taskArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -Action Run -Config "{1}"' -f $PSCommandPath, $configPath
$description = 'Panda Alpha bounded offline service: ' + $configPath
$runtimeManifest = Get-Content -LiteralPath (Join-Path $definition.runtime 'manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if ($null -ne $runtimeManifest.binding.execution_config -and $Action -in @('Plan', 'Install', 'Run', 'Start')) {
    throw 'Website deployments require explicit host-control start --execute; login/timer startup is not supported'
}

if ($Action -eq 'Plan') {
    [pscustomobject]@{Name=$serviceName; Account=$accountName; Trigger='Current user logon';
        LogonType='Interactive'; RunLevel='Limited'; Execute=$powershellPath; Arguments=$taskArguments;
        RestartCount=3; RecoveryCheckSeconds=60; Deadline='Original immutable host deadline';
        InstallArmsBackgroundStart=$true;
        WebsiteExecution=$false} | ConvertTo-Json -Depth 4
    return
}
if ($Action -eq 'Run') {
    Set-Location -LiteralPath $repositoryPath
    & $pythonPath $entryPath --config $configPath
    exit $LASTEXITCODE
}

$task = Get-ScheduledTask -TaskName $serviceName -TaskPath '\' -ErrorAction SilentlyContinue
if ($task) {
    $taskUser = $task.Principal.UserId
    $taskSid = if ($taskUser -match '^S-1-') { $taskUser } else {
        (New-Object Security.Principal.NTAccount($taskUser)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    if ($task.Description -ne $description -or $taskSid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value `
        -or @($task.Actions).Count -ne 1 -or $task.Actions.Execute -ne $powershellPath -or $task.Actions.Arguments -ne $taskArguments) {
        throw 'Existing task has different ownership/bindings; refusing to replace or operate on it'
    }
}
function Request-GracefulStop {
    & $pythonPath $entryPath --config $configPath --stop
    if ($LASTEXITCODE -ne 0) { throw 'Graceful stop request failed' }
    $until = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $currentTask = Get-ScheduledTask -TaskName $serviceName -TaskPath '\' -ErrorAction SilentlyContinue
        if (-not $currentTask -or $currentTask.State -ne 'Running') { return }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $until)
    throw 'Graceful stop still pending; inspect private runtime status before a forced stop'
}
switch ($Action) {
    'Install' {
        if ($task) { throw 'Task already installed; inspect Status or explicitly Uninstall first' }
        $manifest = Get-Content -LiteralPath (Join-Path $definition.runtime 'manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
        $deadline = [DateTimeOffset]::FromUnixTimeSeconds([long]$manifest.deadline_epoch).LocalDateTime
        if ($deadline -le [DateTime]::Now.AddMinutes(1)) { throw 'Host deadline must allow at least one minute for startup' }
        # An explicit, bounded timer also recovers loss of the entire launcher.
        # RestartOnFailure alone did not restart a demand-started task on the acceptance machine.
        $scheduler = New-Object -ComObject Schedule.Service
        $scheduler.Connect()
        $taskDefinition = $scheduler.NewTask(0)
        $taskDefinition.RegistrationInfo.Description = $description
        $taskDefinition.Principal.UserId = $accountName
        $taskDefinition.Principal.LogonType = 3 # InteractiveToken
        $taskDefinition.Principal.RunLevel = 0 # Least privilege
        $taskDefinition.Settings.MultipleInstances = 2 # IgnoreNew
        $taskDefinition.Settings.RestartCount = 3
        $taskDefinition.Settings.RestartInterval = 'PT1M'
        $taskDefinition.Settings.ExecutionTimeLimit = 'PT0S'
        $taskDefinition.Settings.DisallowStartIfOnBatteries = $false
        $taskDefinition.Settings.StopIfGoingOnBatteries = $false
        $taskDefinition.Settings.StartWhenAvailable = $true
        $taskDefinition.Settings.Hidden = $true
        $trigger = $taskDefinition.Triggers.Create(9) # Logon
        $trigger.UserId = $accountName
        $trigger.EndBoundary = $deadline.ToString('s')
        $recoveryTrigger = $taskDefinition.Triggers.Create(1) # Time
        $recoveryTrigger.StartBoundary = [DateTime]::Now.AddMinutes(1).ToString('s')
        $recoveryTrigger.EndBoundary = $deadline.ToString('s')
        $recoveryTrigger.Repetition.Interval = 'PT1M'
        $taskAction = $taskDefinition.Actions.Create(0) # Exec
        $taskAction.Path = $powershellPath
        $taskAction.Arguments = $taskArguments
        $taskAction.WorkingDirectory = $repositoryPath
        $scheduler.GetFolder('\').RegisterTaskDefinition($serviceName, $taskDefinition, 2, $accountName, $null, 3) | Out-Null
        [pscustomobject]@{Name=$serviceName; Installed=$true; BackgroundStart='Next minute or next logon'; Deadline=$deadline} | ConvertTo-Json
    }
    'Start' {
        if (-not $task) { throw 'Task not installed' }
        & $pythonPath $entryPath --config $configPath --enable
        if ($LASTEXITCODE -ne 0) { throw 'Could not enable this service' }
        Start-ScheduledTask -TaskName $serviceName -TaskPath '\'
    }
    'Stop' {
        Request-GracefulStop
    }
    'Status' {
        $info = if ($task) { Get-ScheduledTaskInfo -TaskName $serviceName -TaskPath '\' }
        $statePath = Join-Path (Split-Path -Parent $configPath) 'service-state.json'
        $runtimeState = if (Test-Path -LiteralPath $statePath) { Get-Content -LiteralPath $statePath -Encoding UTF8 -Raw | ConvertFrom-Json }
        [pscustomobject]@{Name=$serviceName; Installed=[bool]$task; TaskState=$task.State;
            LastTaskResult=$info.LastTaskResult; LastRunTime=$info.LastRunTime;
            RuntimeStatus=$runtimeState.status; Reason=$runtimeState.reason;
            LastCheck=$runtimeState.last_check;
            Starts=@($runtimeState.starts).Count; MaxStarts=$definition.max_starts} | ConvertTo-Json
    }
    'Uninstall' {
        if ($task) {
            # Disable triggers before stopping so a minute boundary cannot race removal.
            Disable-ScheduledTask -TaskName $serviceName -TaskPath '\' | Out-Null
            Request-GracefulStop
            Unregister-ScheduledTask -TaskName $serviceName -TaskPath '\' -Confirm:$false
        }
        [pscustomobject]@{Name=$serviceName; Installed=$false; HistoryPreserved=$true} | ConvertTo-Json
    }
}
