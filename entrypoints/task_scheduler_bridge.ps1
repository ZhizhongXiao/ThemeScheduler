param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Probe', 'Read', 'Register', 'Delete', 'Export', 'Restore', 'Run')]
    [string]$Action,

    [string]$TaskPath = '\ThemeScheduler',

    [string]$RequestPath
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$TaskCreateOrUpdate = 6
$TaskLogonInteractiveToken = 3
$TaskRunLevelLua = 0
$TaskTriggerTime = 1
$TaskTriggerDaily = 2
$TaskActionExec = 0
$TaskInstancesIgnoreNew = 2
$ManagedTaskPath = '\ThemeScheduler'

function Write-BridgeResult {
    param([hashtable]$Payload)
    $Payload | ConvertTo-Json -Depth 16 -Compress
}

function Read-BridgeRequest {
    if ([string]::IsNullOrWhiteSpace($RequestPath)) {
        throw 'Bridge request path is required.'
    }
    $resolved = Resolve-Path -LiteralPath $RequestPath
    $item = Get-Item -LiteralPath $resolved.Path
    if ($item.Length -le 0 -or $item.Length -gt 2097152) {
        throw 'Bridge request file is empty or too large.'
    }
    $raw = Get-Content -LiteralPath $resolved.Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw 'Bridge request JSON is empty.'
    }
    return $raw | ConvertFrom-Json
}

function Get-CurrentUserSid {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity.User) {
        throw 'The current Windows user SID is unavailable.'
    }
    return $identity.User.Value
}

function ConvertTo-UserSid {
    param([string]$UserId)
    if ([string]::IsNullOrWhiteSpace($UserId)) {
        return $UserId
    }
    if ($UserId -match '^S-\d-\d+-.+$') {
        return $UserId
    }
    try {
        $account = [System.Security.Principal.NTAccount]::new($UserId)
        return $account.Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        return $UserId
    }
}

function Get-TaskOrNull {
    param($RootFolder, [string]$Name)
    try {
        return $RootFolder.GetTask($Name)
    }
    catch {
        if (
            $_.FullyQualifiedErrorId -eq 'DirectoryNotFoundException' -or
            $_.FullyQualifiedErrorId -like 'System.IO.DirectoryNotFoundException*' -or
            $_.CategoryInfo.Reason -eq 'DirectoryNotFoundException' -or
            $_.Exception -is [System.IO.DirectoryNotFoundException]
        ) {
            return $null
        }
        $exception = $_.Exception
        while ($null -ne $exception) {
            if ($exception.HResult -in @(-2147024894, -2147024893)) {
                return $null
            }
            $exception = $exception.InnerException
        }
        throw
    }
}

function Get-TriggerLocalTime {
    param($Trigger)
    $boundary = [string]$Trigger.StartBoundary
    if ($boundary -match 'T(?<time>\d{2}:\d{2})') {
        return $Matches.time
    }
    return '00:00'
}

function Get-MultipleInstancesName {
    param([int]$Value)
    switch ($Value) {
        0 { return 'Parallel' }
        1 { return 'Queue' }
        2 { return 'IgnoreNew' }
        3 { return 'StopExisting' }
        default { return "Value-$Value" }
    }
}

function ConvertTo-NormalizedSpec {
    param($RegisteredTask)

    $definition = $RegisteredTask.Definition
    $triggerItems = @()
    for ($index = 1; $index -le $definition.Triggers.Count; $index++) {
        $trigger = $definition.Triggers.Item($index)
        $triggerId = [string]$trigger.Id
        if ([string]::IsNullOrWhiteSpace($triggerId)) {
            $triggerId = "__unnamed-$index"
        }
        $triggerType = [int]$trigger.Type
        $daysInterval = 1
        $typeName = "Type-$triggerType"
        if ($triggerType -eq $TaskTriggerDaily) {
            $typeName = 'Daily'
            $daysInterval = [int]$trigger.DaysInterval
            $triggerItems += [ordered]@{
                id = $triggerId
                localTime = Get-TriggerLocalTime -Trigger $trigger
                daysInterval = $daysInterval
                enabled = [bool]$trigger.Enabled
                type = $typeName
            }
            continue
        }
        if ($triggerType -eq $TaskTriggerTime) {
            $triggerItems += [ordered]@{
                id = $triggerId
                startAt = [DateTimeOffset]::Parse(
                    [string]$trigger.StartBoundary,
                    [System.Globalization.CultureInfo]::InvariantCulture
                ).ToString(
                    "yyyy-MM-dd'T'HH:mm:sszzz",
                    [System.Globalization.CultureInfo]::InvariantCulture
                )
                enabled = [bool]$trigger.Enabled
                type = 'Time'
            }
            continue
        }
        throw "Unsupported trigger type $triggerType in managed task."
    }

    $actionCount = [int]$definition.Actions.Count
    $executable = ''
    $arguments = ''
    $workingDirectory = ''
    $actionTypeName = 'None'
    if ($actionCount -gt 0) {
        $firstAction = $definition.Actions.Item(1)
        $firstActionType = [int]$firstAction.Type
        $actionTypeName = "Type-$firstActionType"
        if ($firstActionType -eq $TaskActionExec) {
            $actionTypeName = 'Exec'
            $executable = [string]$firstAction.Path
            $arguments = [string]$firstAction.Arguments
            $workingDirectory = [string]$firstAction.WorkingDirectory
        }
    }

    $settings = $definition.Settings
    return [ordered]@{
        kind = 'themescheduler.task-spec'
        schemaVersion = 2
        taskPath = [string]$RegisteredTask.Path
        enabled = [bool]$RegisteredTask.Enabled
        triggers = $triggerItems
        action = [ordered]@{
            executable = $executable
            arguments = $arguments
            workingDirectory = $workingDirectory
            type = $actionTypeName
            count = $actionCount
        }
        principal = [ordered]@{
            userId = ConvertTo-UserSid -UserId ([string]$definition.Principal.UserId)
            logonType = if ([int]$definition.Principal.LogonType -eq $TaskLogonInteractiveToken) {
                'InteractiveToken'
            } else {
                "Value-$([int]$definition.Principal.LogonType)"
            }
            runLevel = if ([int]$definition.Principal.RunLevel -eq $TaskRunLevelLua) {
                'LeastPrivilege'
            } else {
                "Value-$([int]$definition.Principal.RunLevel)"
            }
        }
        settings = [ordered]@{
            startWhenAvailable = [bool]$settings.StartWhenAvailable
            wakeToRun = [bool]$settings.WakeToRun
            multipleInstances = Get-MultipleInstancesName -Value ([int]$settings.MultipleInstances)
            allowStartOnBatteries = -not [bool]$settings.DisallowStartIfOnBatteries
            stopIfGoingOnBatteries = [bool]$settings.StopIfGoingOnBatteries
            runOnlyIfNetworkAvailable = [bool]$settings.RunOnlyIfNetworkAvailable
            hidden = [bool]$settings.Hidden
            executionTimeLimit = [string]$settings.ExecutionTimeLimit
        }
    }
}

function Assert-DesiredSpec {
    param($Spec, [string]$CurrentUserSid)

    if ($Spec.kind -ne 'themescheduler.task-spec' -or $Spec.schemaVersion -ne 2) {
        throw 'Unsupported task specification.'
    }
    if ($Spec.taskPath -ne $ManagedTaskPath) {
        throw "The bridge only manages $ManagedTaskPath."
    }
    if ($Spec.principal.userId -ne $CurrentUserSid) {
        throw 'The task principal must be the current Windows user SID.'
    }
    if (
        $Spec.principal.logonType -ne 'InteractiveToken' -or
        $Spec.principal.runLevel -ne 'LeastPrivilege'
    ) {
        throw 'The task principal contract is invalid.'
    }
    if (
        $Spec.action.type -ne 'Exec' -or
        [int]$Spec.action.count -ne 1 -or
        $Spec.action.arguments -ne 'auto'
    ) {
        throw 'The task must contain exactly one ThemeScheduler auto action.'
    }
    if (-not [System.IO.Path]::IsPathRooted([string]$Spec.action.executable)) {
        throw 'The task executable must use an absolute path.'
    }
    if (-not (Test-Path -LiteralPath $Spec.action.executable -PathType Leaf)) {
        throw "The task executable does not exist: $($Spec.action.executable)"
    }
    $expectedWorkingDirectory = [System.IO.Path]::GetDirectoryName(
        [string]$Spec.action.executable
    )
    if (
        -not [string]::Equals(
            $expectedWorkingDirectory,
            [string]$Spec.action.workingDirectory,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'The task working directory must contain the executable.'
    }

    $triggers = @($Spec.triggers)
    if ($triggers.Count -notin @(4, 6)) {
        throw 'The managed task requires four or six triggers.'
    }
    $ids = @{}
    foreach ($trigger in $triggers) {
        if ($ids.ContainsKey([string]$trigger.id)) {
            throw 'Managed trigger identifiers must be unique.'
        }
        $ids[[string]$trigger.id] = $true
    }

    $fixedIds = @(
        'DayPrepare',
        'DayBoundary',
        'NightPrepare',
        'NightBoundary'
    )
    foreach ($fixedId in $fixedIds) {
        if (-not $ids.ContainsKey($fixedId)) {
            throw "The managed task is missing $fixedId."
        }
    }
    $hasDeferredPrepare = $ids.ContainsKey('DeferredPrepare')
    $hasDeferredBoundary = $ids.ContainsKey('DeferredBoundary')
    if ($hasDeferredPrepare -ne $hasDeferredBoundary) {
        throw 'Deferred triggers must form a complete pair.'
    }
    if (($triggers.Count -eq 6) -ne $hasDeferredPrepare) {
        throw 'Managed trigger identities do not match trigger count.'
    }

    $byId = @{}
    foreach ($trigger in $triggers) {
        $byId[[string]$trigger.id] = $trigger
    }
    foreach ($fixedId in $fixedIds) {
        $trigger = $byId[$fixedId]
        if (
            $trigger.type -ne 'Daily' -or
            [int]$trigger.daysInterval -ne 1 -or
            -not [bool]$trigger.enabled -or
            [string]$trigger.localTime -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$'
        ) {
            throw "Managed fixed trigger $fixedId is invalid."
        }
    }
    if (
        [string]$byId.DayBoundary.localTime -eq
        [string]$byId.NightBoundary.localTime
    ) {
        throw 'Managed boundary times must differ.'
    }
    foreach ($pair in @(
        @('DayPrepare', 'DayBoundary'),
        @('NightPrepare', 'NightBoundary')
    )) {
        $prepare = [DateTime]::ParseExact(
            [string]$byId[$pair[0]].localTime,
            'HH:mm',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        $boundary = [DateTime]::ParseExact(
            [string]$byId[$pair[1]].localTime,
            'HH:mm',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        $difference = ($boundary - $prepare).TotalMinutes
        if ($difference -lt 0) {
            $difference += 1440
        }
        if ($difference -ne 5) {
            throw 'Each fixed prepare trigger must precede its boundary by five minutes.'
        }
    }

    if ($hasDeferredPrepare) {
        foreach ($deferredId in @('DeferredPrepare', 'DeferredBoundary')) {
            $trigger = $byId[$deferredId]
            if ($trigger.type -ne 'Time' -or -not [bool]$trigger.enabled) {
                throw "Managed deferred trigger $deferredId is invalid."
            }
        }
        $deferredPrepare = [DateTimeOffset]::Parse(
            [string]$byId.DeferredPrepare.startAt,
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        $deferredBoundary = [DateTimeOffset]::Parse(
            [string]$byId.DeferredBoundary.startAt,
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        if (($deferredBoundary - $deferredPrepare).TotalMinutes -ne 5) {
            throw 'Deferred prepare must precede deferred execution by five minutes.'
        }
    }

    $settings = $Spec.settings
    if (
        -not [bool]$settings.startWhenAvailable -or
        [bool]$settings.wakeToRun -or
        $settings.multipleInstances -ne 'IgnoreNew' -or
        -not [bool]$settings.allowStartOnBatteries -or
        [bool]$settings.stopIfGoingOnBatteries -or
        [bool]$settings.runOnlyIfNetworkAvailable -or
        [bool]$settings.hidden -or
        $settings.executionTimeLimit -ne 'PT5M'
    ) {
        throw 'The managed task settings are invalid.'
    }
}

if ($TaskPath -ne $ManagedTaskPath) {
    throw "The bridge only manages $ManagedTaskPath."
}

$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$rootFolder = $service.GetFolder('\')
$taskName = $ManagedTaskPath.Substring(1)
$currentUserSid = Get-CurrentUserSid

switch ($Action) {
    'Probe' {
        $task = Get-TaskOrNull -RootFolder $rootFolder -Name $taskName
        Write-BridgeResult @{
            ok = $true
            action = 'Probe'
            currentUserId = $currentUserSid
            taskExists = $null -ne $task
            taskSchedulerChanged = $false
        }
    }
    'Read' {
        $task = Get-TaskOrNull -RootFolder $rootFolder -Name $taskName
        Write-BridgeResult @{
            ok = $true
            action = 'Read'
            exists = $null -ne $task
            task = if ($null -ne $task) {
                ConvertTo-NormalizedSpec -RegisteredTask $task
            } else {
                $null
            }
            taskSchedulerChanged = $false
        }
    }
    'Export' {
        $task = Get-TaskOrNull -RootFolder $rootFolder -Name $taskName
        Write-BridgeResult @{
            ok = $true
            action = 'Export'
            exists = $null -ne $task
            backup = if ($null -ne $task) {
                [ordered]@{
                    kind = 'themescheduler.task-definition-backup'
                    schemaVersion = 1
                    taskPath = [string]$task.Path
                    definitionXml = [string]$task.Xml
                    enabled = [bool]$task.Enabled
                }
            } else {
                $null
            }
            taskSchedulerChanged = $false
        }
    }
    'Register' {
        $request = Read-BridgeRequest
        $spec = $request.task
        Assert-DesiredSpec -Spec $spec -CurrentUserSid $currentUserSid

        $definition = $service.NewTask(0)
        $definition.RegistrationInfo.Author = 'ThemeScheduler'
        $definition.RegistrationInfo.Description = (
            'Prepares and applies ThemeScheduler profiles at managed boundaries.'
        )
        $definition.Principal.UserId = $currentUserSid
        $definition.Principal.LogonType = $TaskLogonInteractiveToken
        $definition.Principal.RunLevel = $TaskRunLevelLua

        $definition.Settings.Enabled = [bool]$spec.enabled
        $definition.Settings.StartWhenAvailable = $true
        $definition.Settings.WakeToRun = $false
        $definition.Settings.MultipleInstances = $TaskInstancesIgnoreNew
        $definition.Settings.DisallowStartIfOnBatteries = $false
        $definition.Settings.StopIfGoingOnBatteries = $false
        $definition.Settings.RunOnlyIfNetworkAvailable = $false
        $definition.Settings.Hidden = $false
        $definition.Settings.ExecutionTimeLimit = 'PT5M'

        $now = [DateTime]::Now
        foreach ($sourceTrigger in @($spec.triggers)) {
            if ($sourceTrigger.type -eq 'Daily') {
                $parsedTime = [DateTime]::ParseExact(
                    [string]$sourceTrigger.localTime,
                    'HH:mm',
                    [System.Globalization.CultureInfo]::InvariantCulture
                )
                $candidate = $now.Date.AddHours($parsedTime.Hour).AddMinutes($parsedTime.Minute)
                if ($candidate -le $now) {
                    $candidate = $candidate.AddDays(1)
                }
                $trigger = $definition.Triggers.Create($TaskTriggerDaily)
                $trigger.StartBoundary = $candidate.ToString(
                    "yyyy-MM-dd'T'HH:mm:ss",
                    [System.Globalization.CultureInfo]::InvariantCulture
                )
                $trigger.DaysInterval = 1
            }
            elseif ($sourceTrigger.type -eq 'Time') {
                $candidate = [DateTimeOffset]::Parse(
                    [string]$sourceTrigger.startAt,
                    [System.Globalization.CultureInfo]::InvariantCulture
                )
                $trigger = $definition.Triggers.Create($TaskTriggerTime)
                $trigger.StartBoundary = $candidate.ToString(
                    "yyyy-MM-dd'T'HH:mm:sszzz",
                    [System.Globalization.CultureInfo]::InvariantCulture
                )
            }
            else {
                throw "Unsupported trigger type: $($sourceTrigger.type)"
            }
            $trigger.Id = [string]$sourceTrigger.id
            $trigger.Enabled = $true
        }

        $execAction = $definition.Actions.Create($TaskActionExec)
        $execAction.Path = [string]$spec.action.executable
        $execAction.Arguments = [string]$spec.action.arguments
        $execAction.WorkingDirectory = [string]$spec.action.workingDirectory

        $registered = $rootFolder.RegisterTaskDefinition(
            $taskName,
            $definition,
            $TaskCreateOrUpdate,
            $currentUserSid,
            $null,
            $TaskLogonInteractiveToken,
            $null
        )
        $registered.Enabled = [bool]$spec.enabled
        Write-BridgeResult @{
            ok = $true
            action = 'Register'
            taskPath = [string]$registered.Path
            taskSchedulerChanged = $true
        }
    }
    'Delete' {
        $task = Get-TaskOrNull -RootFolder $rootFolder -Name $taskName
        $deleted = $false
        if ($null -ne $task) {
            $rootFolder.DeleteTask($taskName, 0)
            $deleted = $true
        }
        Write-BridgeResult @{
            ok = $true
            action = 'Delete'
            deleted = $deleted
            taskSchedulerChanged = $deleted
        }
    }
    'Restore' {
        $request = Read-BridgeRequest
        $backup = $request.backup
        if (
            $backup.kind -ne 'themescheduler.task-definition-backup' -or
            $backup.schemaVersion -ne 1 -or
            $backup.taskPath -ne $ManagedTaskPath -or
            [string]::IsNullOrWhiteSpace([string]$backup.definitionXml) -or
            ([string]$backup.definitionXml).Length -gt 1048576
        ) {
            throw 'The task definition backup is invalid.'
        }
        $restored = $rootFolder.RegisterTask(
            $taskName,
            [string]$backup.definitionXml,
            $TaskCreateOrUpdate,
            $currentUserSid,
            $null,
            $TaskLogonInteractiveToken,
            $null
        )
        $restored.Enabled = [bool]$backup.enabled
        Write-BridgeResult @{
            ok = $true
            action = 'Restore'
            taskPath = [string]$restored.Path
            taskSchedulerChanged = $true
        }
    }
    'Run' {
        $task = Get-TaskOrNull -RootFolder $rootFolder -Name $taskName
        if ($null -eq $task) {
            throw "The managed task does not exist: $ManagedTaskPath"
        }
        $instance = $task.Run($null)
        Write-BridgeResult @{
            ok = $true
            action = 'Run'
            taskPath = [string]$task.Path
            instanceGuid = [string]$instance.InstanceGuid
            taskStarted = $true
            taskSchedulerChanged = $false
        }
    }
}
