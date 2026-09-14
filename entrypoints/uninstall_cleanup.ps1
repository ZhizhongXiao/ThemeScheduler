param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,

    [Parameter(Mandatory = $true)]
    [int]$WaitPid,

    [Parameter(Mandatory = $false)]
    [int]$WaitParentPid = 0,

    [Parameter(Mandatory = $false)]
    [int]$SignalWaitMilliseconds = 120000,

    [Parameter(Mandatory = $false)]
    [switch]$FinalizeProductRegistration
)

$ErrorActionPreference = 'Stop'

function Write-CleanupFailure {
    param([string]$Message)
    try {
        $parent = Split-Path -Parent $Workspace
        $name = Split-Path -Leaf $Workspace
        $failurePath = Join-Path $parent "$name.cleanup-failed.txt"
        [System.IO.File]::WriteAllText(
            $failurePath,
            $Message,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    catch {
        # The caller will also detect a retained uninstall workspace.
    }
}

try {
    if ($WaitPid -le 0) {
        throw 'WaitPid must be positive.'
    }
    if (
        $SignalWaitMilliseconds -lt 0 -or
        $SignalWaitMilliseconds -gt 600000
    ) {
        throw 'SignalWaitMilliseconds is outside the safe range.'
    }
    $resolved = [System.IO.Path]::GetFullPath($Workspace)
    $leaf = [System.IO.Path]::GetFileName($resolved)
    $parent = [System.IO.Directory]::GetParent($resolved)
    $expectedParent = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::Combine(
            [System.IO.Path]::GetTempPath(),
            'ThemeScheduler'
        )
    ).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if (
        $leaf -notmatch '^uninstall-[0-9a-f]{32}$' -or
        $null -eq $parent -or
        $parent.Name -ne 'ThemeScheduler' -or
        -not [string]::Equals(
            $parent.FullName.TrimEnd(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            ),
            $expectedParent,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'Workspace is outside the fixed uninstall boundary.'
    }
    if (Test-Path -LiteralPath $resolved) {
        $item = Get-Item -LiteralPath $resolved -Force
        if (
            -not $item.PSIsContainer -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
        ) {
            throw 'Workspace is not a regular directory.'
        }
    }
    $waitPids = @($WaitPid)
    if ($WaitParentPid -gt 0 -and $WaitParentPid -ne $WaitPid) {
        $waitPids += $WaitParentPid
    }
    $expectedExecutable = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::Combine($resolved, 'Uninstall.exe')
    )
    $exitSignal = [System.IO.Path]::Combine(
        $resolved,
        'exit-requested'
    )
    $waitProcesses = @()
    foreach ($processId in $waitPids) {
        try {
            $waitProcesses += (
                [System.Diagnostics.Process]::GetProcessById(
                    $processId
                )
            )
        }
        catch [System.ArgumentException] {
            # The target process already exited.
        }
    }
    $signalWait = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not [System.IO.File]::Exists($exitSignal)) {
        $running = @(
            $waitProcesses | Where-Object { -not $_.HasExited }
        )
        if ($running.Count -eq 0) {
            break
        }
        if ($signalWait.ElapsedMilliseconds -ge $SignalWaitMilliseconds) {
            break
        }
        Start-Sleep -Milliseconds 100
    }
    $signalWait.Stop()
    $naturalExitMilliseconds = 1000
    $forcedExitMilliseconds = 5000
    foreach ($process in $waitProcesses) {
        if ($process.HasExited) {
            continue
        }
        if ($process.WaitForExit($naturalExitMilliseconds)) {
            continue
        }
        $actualExecutable = [System.IO.Path]::GetFullPath(
            $process.MainModule.FileName
        )
        if (
            -not [string]::Equals(
                $actualExecutable,
                $expectedExecutable,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw (
                "Refusing to terminate unexpected process " +
                "$($process.Id): $actualExecutable"
            )
        }
        $process.Kill()
        if (-not $process.WaitForExit($forcedExitMilliseconds)) {
            throw "Uninstaller process $($process.Id) did not exit."
        }
    }
    if ($FinalizeProductRegistration) {
        $journalPath = [System.IO.Path]::Combine($resolved, 'journal.json')
        $requestPath = [System.IO.Path]::Combine($resolved, 'request.json')
        if (
            -not [System.IO.File]::Exists($journalPath) -or
            -not [System.IO.File]::Exists($requestPath)
        ) {
            throw 'Completed uninstall evidence is missing.'
        }
        $journal = Get-Content -LiteralPath $journalPath -Raw |
            ConvertFrom-Json
        $request = Get-Content -LiteralPath $requestPath -Raw |
            ConvertFrom-Json
        $expectedProgramRoot = [System.IO.Path]::GetFullPath(
            [System.IO.Path]::Combine(
                [System.Environment]::GetFolderPath('LocalApplicationData'),
                'Programs',
                'ThemeScheduler'
            )
        )
        $requestedProgramRoot = [System.IO.Path]::GetFullPath(
            [string]$request.programRoot
        )
        if (
            $journal.kind -ne 'themescheduler.uninstall-journal' -or
            $journal.status -ne 'completed' -or
            $journal.completedSteps -notcontains 'registration-removed' -or
            -not [string]::Equals(
                $requestedProgramRoot,
                $expectedProgramRoot,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw 'Registration cleanup is not authorized by completed evidence.'
        }
        $registrationPath = (
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
            'ThemeScheduler'
        )
        if (Test-Path -LiteralPath $registrationPath) {
            $registration = Get-Item -LiteralPath $registrationPath
            if (@($registration.GetSubKeyNames()).Count -ne 0) {
                throw 'Installed-app registration contains unexpected subkeys.'
            }
            Remove-Item -LiteralPath $registrationPath -Force
        }
        if (Test-Path -LiteralPath $registrationPath) {
            throw 'Installed-app registration still exists after final cleanup.'
        }
    }
    $maximumAttempts = 60
    $retryMilliseconds = 250
    $lastRemovalError = $null
    for ($attempt = 1; $attempt -le $maximumAttempts; $attempt++) {
        try {
            if (Test-Path -LiteralPath $resolved) {
                Remove-Item -LiteralPath $resolved -Recurse -Force
            }
            if (-not (Test-Path -LiteralPath $resolved)) {
                $lastRemovalError = $null
                break
            }
        }
        catch {
            $lastRemovalError = $_
        }
        if ($attempt -lt $maximumAttempts) {
            Start-Sleep -Milliseconds $retryMilliseconds
        }
    }
    if (Test-Path -LiteralPath $resolved) {
        if ($null -ne $lastRemovalError) {
            throw $lastRemovalError.Exception
        }
        throw 'Workspace still exists after cleanup.'
    }
    if (
        $parent.Exists -and
        @(
            [System.IO.Directory]::EnumerateFileSystemEntries(
                $parent.FullName
            )
        ).Count -eq 0
    ) {
        [System.IO.Directory]::Delete($parent.FullName)
    }
}
catch {
    Write-CleanupFailure -Message (
        "$(Get-Date -Format o) $($_.Exception.GetType().FullName): " +
        "$($_.Exception.Message)"
    )
    exit 2
}
