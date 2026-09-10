[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "Release",
        "PreInstall",
        "PostInstall",
        "PostBoundary",
        "PreUpgrade",
        "PostUpgrade",
        "PostUninstall"
    )]
    [string]$Phase,

    [string]$ReleaseRoot,
    [string]$ExpectedVersion = "0.1.2",
    [ValidatePattern("^$|^[0-9A-Fa-f]{64}$")]
    [string]$ExpectedSetupSha256 = "",
    [ValidateSet("", "light", "dark")]
    [string]$ExpectedAppsTheme = "",
    [ValidatePattern("^$|^0X[0-9A-F]{8}$")]
    [string]$ExpectedColorizationColor = "",
    [switch]$ExpectDesktopShortcut,
    [switch]$KeepData,
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$checks = New-Object "System.Collections.Generic.List[object]"
$facts = [ordered]@{}
$programRoot = Join-Path $env:LOCALAPPDATA "Programs\ThemeScheduler"
$dataRoot = Join-Path $env:LOCALAPPDATA "ThemeScheduler"
$mainExecutable = Join-Path $programRoot "app\ThemeScheduler.exe"
$uninstaller = Join-Path $programRoot "maintenance\Uninstall.exe"
$installationRecord = Join-Path $programRoot "metadata\installation.json"
$installedManifest = Join-Path $programRoot "metadata\payload-manifest.json"
$uninstallKey =
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ThemeScheduler"
$protocolKey = "HKCU:\Software\Classes\themescheduler-action"
$startMenuShortcut = Join-Path (
    [Environment]::GetFolderPath("Programs")
) "ThemeScheduler\ThemeScheduler.lnk"
$desktopShortcut = Join-Path (
    [Environment]::GetFolderPath("Desktop")
) "ThemeScheduler.lnk"
$taskName = "ThemeScheduler"
$taskPath = "\"
$webViewClientId = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

function Add-Check {
    param(
        [string]$Id,
        [bool]$Passed,
        [object]$Expected,
        [object]$Actual,
        [string]$Detail = "",
        [bool]$Blocking = $true
    )

    $status = if ($Passed) {
        "pass"
    }
    elseif ($Blocking) {
        "fail"
    }
    else {
        "warning"
    }
    $checks.Add(
        [ordered]@{
            id = $Id
            status = $status
            blocking = $Blocking
            expected = $Expected
            actual = $Actual
            detail = $Detail
        }
    )
}

function Get-SafeChild {
    param(
        [string]$Root,
        [string]$Relative
    )

    if (
        [IO.Path]::IsPathRooted($Relative) -or
        $Relative -match "(^|[\\/])\.\.([\\/]|$)"
    ) {
        throw "Unsafe relative path: $Relative"
    }
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd("\", "/")
    $candidate = [IO.Path]::GetFullPath((Join-Path $resolvedRoot $Relative))
    if (
        -not $candidate.StartsWith(
            $resolvedRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Path escapes the declared root: $Relative"
    }
    return $candidate
}

function Test-ChecksumFile {
    param(
        [string]$Root,
        [string]$ChecksumFile
    )

    $failures = New-Object "System.Collections.Generic.List[string]"
    $count = 0
    foreach ($line in Get-Content -LiteralPath $ChecksumFile -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line -notmatch "^([0-9a-fA-F]{64})  (.+)$") {
            $failures.Add("invalid checksum line: $line")
            continue
        }
        $count += 1
        try {
            $target = Get-SafeChild -Root $Root -Relative $Matches[2]
            if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
                $failures.Add("missing: $($Matches[2])")
                continue
            }
            $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            if ($actual -ine $Matches[1]) {
                $failures.Add("hash mismatch: $($Matches[2])")
            }
        }
        catch {
            $failures.Add($_.Exception.Message)
        }
    }
    return [ordered]@{
        count = $count
        failures = @($failures)
    }
}

function Test-PayloadTree {
    param(
        [string]$Root,
        [string]$ManifestPath,
        [string]$Version
    )

    $failures = New-Object "System.Collections.Generic.List[string]"
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if (
        $manifest.kind -ne "themescheduler.payload-manifest" -or
        $manifest.schemaVersion -ne 1 -or
        $manifest.productId -ne "ThemeScheduler" -or
        $manifest.version -ne $Version
    ) {
        $failures.Add("payload manifest identity mismatch")
    }
    $count = 0
    foreach ($item in @($manifest.files)) {
        $count += 1
        try {
            $target = Get-SafeChild -Root $Root -Relative ([string]$item.path)
            if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
                $failures.Add("missing: $($item.path)")
                continue
            }
            $file = Get-Item -LiteralPath $target
            if ($file.Length -ne [long]$item.size) {
                $failures.Add("size mismatch: $($item.path)")
                continue
            }
            $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            if ($actual -ine [string]$item.sha256) {
                $failures.Add("hash mismatch: $($item.path)")
            }
        }
        catch {
            $failures.Add($_.Exception.Message)
        }
    }
    return [ordered]@{
        count = $count
        version = [string]$manifest.version
        failures = @($failures)
    }
}

function Get-WebView2Versions {
    $versions = New-Object "System.Collections.Generic.List[string]"
    $paths = @(
        "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$webViewClientId",
        "HKLM:\Software\Microsoft\EdgeUpdate\Clients\$webViewClientId",
        "HKLM:\Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\$webViewClientId"
    )
    foreach ($path in $paths) {
        try {
            $version = (Get-ItemProperty -LiteralPath $path -Name "pv").pv
            if (-not [string]::IsNullOrWhiteSpace([string]$version)) {
                $versions.Add([string]$version)
            }
        }
        catch {
            continue
        }
    }
    return @($versions | Select-Object -Unique)
}

function Get-ProductTask {
    try {
        return Get-ScheduledTask `
            -TaskName $taskName `
            -TaskPath $taskPath `
            -ErrorAction Stop
    }
    catch {
        return $null
    }
}

function Add-AbsenceChecks {
    param([bool]$ExpectDataAbsent)

    $task = Get-ProductTask
    Add-Check "program-root-absent" (-not (Test-Path -LiteralPath $programRoot)) `
        "absent" (Test-Path -LiteralPath $programRoot)
    Add-Check "data-root-absent" (
        -not $ExpectDataAbsent -or -not (Test-Path -LiteralPath $dataRoot)
    ) $(if ($ExpectDataAbsent) { "absent" } else { "preserved or absent" }) `
        (Test-Path -LiteralPath $dataRoot)
    Add-Check "uninstall-registration-absent" (
        -not (Test-Path -LiteralPath $uninstallKey)
    ) "absent" (Test-Path -LiteralPath $uninstallKey)
    Add-Check "notification-protocol-absent" (
        -not (Test-Path -LiteralPath $protocolKey)
    ) "absent" (Test-Path -LiteralPath $protocolKey)
    Add-Check "start-menu-shortcut-absent" (
        -not (Test-Path -LiteralPath $startMenuShortcut)
    ) "absent" (Test-Path -LiteralPath $startMenuShortcut)
    Add-Check "desktop-shortcut-absent" (
        -not (Test-Path -LiteralPath $desktopShortcut)
    ) "absent" (Test-Path -LiteralPath $desktopShortcut)
    Add-Check "scheduled-task-absent" ($null -eq $task) "absent" $(
        if ($null -eq $task) { "absent" } else { $task.State.ToString() }
    )
}

function Add-ReleaseChecks {
    if ([string]::IsNullOrWhiteSpace($ExpectedSetupSha256)) {
        throw "ExpectedSetupSha256 is required for Release and PreInstall phases."
    }
    if ([string]::IsNullOrWhiteSpace($ReleaseRoot)) {
        throw "-ReleaseRoot is required for phase $Phase."
    }
    $root = (Resolve-Path -LiteralPath $ReleaseRoot).Path
    $setup = Join-Path $root "dist\ThemeScheduler-Setup.exe"
    $rootChecksums = Join-Path $root "SHA256SUMS.txt"
    $payloadRoot = Join-Path $root "evidence\bundle-evidence\payload"
    $payloadManifest = Join-Path $root (
        "evidence\bundle-evidence\payload-manifest.json"
    )
    foreach ($required in @($setup, $rootChecksums, $payloadRoot, $payloadManifest)) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Release evidence is incomplete: $required"
        }
    }

    $setupHash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
    $setupVersion = (Get-Item -LiteralPath $setup).VersionInfo.ProductVersion
    $signature = Get-AuthenticodeSignature -LiteralPath $setup
    $zone = $null
    try {
        $zoneText = Get-Content -LiteralPath ($setup + ":Zone.Identifier") `
            -Raw -ErrorAction Stop
        if ($zoneText -match "(?m)^ZoneId=(\d+)$") {
            $zone = [int]$Matches[1]
        }
    }
    catch {
        $zone = $null
    }

    Add-Check "setup-sha256" ($setupHash -ieq $ExpectedSetupSha256) `
        $ExpectedSetupSha256 $setupHash
    Add-Check "setup-version" ($setupVersion -eq $ExpectedVersion) `
        $ExpectedVersion $setupVersion
    Add-Check "setup-authenticode" ($signature.Status -eq "NotSigned") `
        "NotSigned (frozen unsigned release)" $signature.Status.ToString()
    Add-Check "setup-zone-id" ($zone -eq 3) "3 for SmartScreen exercise" $zone `
        "A missing ZoneId does not change bytes, but cannot exercise SmartScreen." $false

    $checksumResult = Test-ChecksumFile -Root $root -ChecksumFile $rootChecksums
    Add-Check "release-checksums" (
        $checksumResult.count -gt 0 -and $checksumResult.failures.Count -eq 0
    ) "all frozen release checksums match" $checksumResult

    $payloadResult = Test-PayloadTree -Root $payloadRoot `
        -ManifestPath $payloadManifest -Version $ExpectedVersion
    Add-Check "release-payload-tree" (
        $payloadResult.count -gt 0 -and $payloadResult.failures.Count -eq 0
    ) "all frozen payload files match" $payloadResult

    $facts.release = [ordered]@{
        root = $root
        setup = $setup
        setupSha256 = $setupHash.ToLowerInvariant()
        setupVersion = $setupVersion
        authenticodeStatus = $signature.Status.ToString()
        zoneId = $zone
        payloadFileCount = $payloadResult.count
    }
}

function Add-EnvironmentChecks {
    $currentVersion = Get-ItemProperty -LiteralPath (
        "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    )
    $build = [int]$currentVersion.CurrentBuildNumber
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    $elevated = $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    $pythonCommands = @(
        Get-Command python, py -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Source -notmatch "\\WindowsApps\\python(3)?\.exe$"
            } |
            ForEach-Object { $_.Source }
    )
    $webViewVersions = @(Get-WebView2Versions)

    Add-Check "windows-11-x64" (
        [Environment]::Is64BitOperatingSystem -and $build -ge 22000
    ) "Windows 11 x64" "$($currentVersion.ProductName), build $build"
    Add-Check "not-elevated" (-not $elevated) "standard current-user token" $elevated
    Add-Check "python-runtime-absent" ($pythonCommands.Count -eq 0) `
        "no usable python or py command" $pythonCommands
    Add-Check "webview2-runtime" ($webViewVersions.Count -gt 0) `
        "installed runtime" $webViewVersions `
        "If absent, verify that Setup blocks safely and explains the prerequisite." $false

    $facts.environment = [ordered]@{
        caption = $currentVersion.ProductName
        displayVersion = $currentVersion.DisplayVersion
        build = $build
        architecture = $env:PROCESSOR_ARCHITECTURE
        elevated = $elevated
        pythonCommands = $pythonCommands
        webView2Versions = $webViewVersions
    }
}

function Add-InstalledChecks {
    param([bool]$ExpectInitialSetup)

    Add-Check "main-executable" (Test-Path -LiteralPath $mainExecutable -PathType Leaf) `
        "present" (Test-Path -LiteralPath $mainExecutable)
    Add-Check "independent-uninstaller" (
        Test-Path -LiteralPath $uninstaller -PathType Leaf
    ) "present" (Test-Path -LiteralPath $uninstaller)
    Add-Check "installation-record" (
        Test-Path -LiteralPath $installationRecord -PathType Leaf
    ) "present" (Test-Path -LiteralPath $installationRecord)
    Add-Check "installed-payload-manifest" (
        Test-Path -LiteralPath $installedManifest -PathType Leaf
    ) "present" (Test-Path -LiteralPath $installedManifest)

    if (
        (Test-Path -LiteralPath $installationRecord -PathType Leaf) -and
        (Test-Path -LiteralPath $installedManifest -PathType Leaf)
    ) {
        $installation = Get-Content -LiteralPath $installationRecord `
            -Raw -Encoding UTF8 | ConvertFrom-Json
        Add-Check "installed-version" ($installation.version -eq $ExpectedVersion) `
            $ExpectedVersion $installation.version
        $payloadResult = Test-PayloadTree -Root $programRoot `
            -ManifestPath $installedManifest -Version $ExpectedVersion
        Add-Check "installed-payload-tree" (
            $payloadResult.count -gt 0 -and $payloadResult.failures.Count -eq 0
        ) "all installed payload files match metadata" $payloadResult
        $facts.installation = $installation
        $facts.installedPayload = $payloadResult
    }

    $registration = $null
    if (Test-Path -LiteralPath $uninstallKey) {
        $registration = Get-ItemProperty -LiteralPath $uninstallKey
    }
    Add-Check "uninstall-registration" ($null -ne $registration) "present" $(
        if ($null -eq $registration) { "absent" } else { $registration.DisplayVersion }
    )
    if ($null -ne $registration) {
        Add-Check "registration-version" (
            $registration.DisplayVersion -eq $ExpectedVersion
        ) $ExpectedVersion $registration.DisplayVersion
        Add-Check "registration-location" (
            [IO.Path]::GetFullPath([string]$registration.InstallLocation) -eq
            [IO.Path]::GetFullPath($programRoot)
        ) $programRoot $registration.InstallLocation
    }

    Add-Check "start-menu-shortcut" (
        Test-Path -LiteralPath $startMenuShortcut -PathType Leaf
    ) "present" (Test-Path -LiteralPath $startMenuShortcut)
    Add-Check "desktop-shortcut" (
        (Test-Path -LiteralPath $desktopShortcut -PathType Leaf) -eq
        [bool]$ExpectDesktopShortcut
    ) ([bool]$ExpectDesktopShortcut) (Test-Path -LiteralPath $desktopShortcut)
    Add-Check "notification-protocol" (Test-Path -LiteralPath $protocolKey) `
        "present" (Test-Path -LiteralPath $protocolKey)

    $task = Get-ProductTask
    Add-Check "scheduled-task" ($null -ne $task) "present and enabled" $(
        if ($null -eq $task) { "absent" } else { $task.State.ToString() }
    )
    if ($null -ne $task) {
        $action = @($task.Actions)[0]
        Add-Check "scheduled-task-action" (
            [IO.Path]::GetFullPath([string]$action.Execute) -eq
            [IO.Path]::GetFullPath($mainExecutable) -and
            [string]$action.Arguments -eq "auto"
        ) "$mainExecutable auto" "$($action.Execute) $($action.Arguments)"
        Add-Check "scheduled-task-enabled" ([bool]$task.Settings.Enabled) `
            $true ([bool]$task.Settings.Enabled)
        $taskInfo = Get-ScheduledTaskInfo -InputObject $task
        $facts.task = [ordered]@{
            state = $task.State.ToString()
            enabled = [bool]$task.Settings.Enabled
            action = [string]$action.Execute
            arguments = [string]$action.Arguments
            triggerCount = @($task.Triggers).Count
            lastRunTime = $taskInfo.LastRunTime
            lastTaskResult = $taskInfo.LastTaskResult
            nextRunTime = $taskInfo.NextRunTime
        }
    }

    foreach ($name in @("config.json", "state.json")) {
        $path = Join-Path $dataRoot $name
        Add-Check "data-$name" (Test-Path -LiteralPath $path -PathType Leaf) `
            "present" (Test-Path -LiteralPath $path)
    }
    $initialSetup = Join-Path $dataRoot "initial-setup.json"
    Add-Check "initial-setup-marker" (
        -not $ExpectInitialSetup -or
        (Test-Path -LiteralPath $initialSetup -PathType Leaf)
    ) $(if ($ExpectInitialSetup) { "present before first save" } else { "not required" }) `
        (Test-Path -LiteralPath $initialSetup)

    $dataFiles = [ordered]@{}
    foreach ($relative in @(
        "config.json",
        "state.json",
        "profiles\day.json",
        "profiles\night.json",
        "backup\install.json",
        "backup\install.theme"
    )) {
        $path = Join-Path $dataRoot $relative
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $dataFiles[$relative] = (
                Get-FileHash -LiteralPath $path -Algorithm SHA256
            ).Hash.ToLowerInvariant()
        }
    }
    $facts.dataFiles = $dataFiles
}

function Add-BoundaryChecks {
    param([bool]$RequireTask = $true)

    $task = Get-ProductTask
    if ($null -eq $task) {
        Add-Check "boundary-task-result" (-not $RequireTask) $(
            if ($RequireTask) { 0 } else { "task absent after uninstall" }
        ) "task absent"
    }
    else {
        $taskInfo = Get-ScheduledTaskInfo -InputObject $task
        Add-Check "boundary-task-result" ($taskInfo.LastTaskResult -eq 0) `
            0 $taskInfo.LastTaskResult
        $facts.boundaryTask = [ordered]@{
            lastRunTime = $taskInfo.LastRunTime
            lastTaskResult = $taskInfo.LastTaskResult
            nextRunTime = $taskInfo.NextRunTime
        }
    }

    try {
        $personalize = Get-ItemProperty -LiteralPath (
            "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        )
        $dwm = Get-ItemProperty -LiteralPath (
            "HKCU:\Software\Microsoft\Windows\DWM"
        )
    }
    catch {
        Add-Check "windows-appearance-readable" $false `
            "Personalize and DWM values readable" $_.Exception.Message
        $facts.windowsAppearance = [ordered]@{
            error = $_.Exception.Message
        }
        return
    }
    $actualTheme = if ($personalize.AppsUseLightTheme -eq 1) { "light" } else { "dark" }
    $colorBits = [Convert]::ToUInt32(
        ([long]$dwm.ColorizationColor -band 4294967295)
    )
    $actualColor = "0X{0:X8}" -f $colorBits
    if ($ExpectedAppsTheme) {
        Add-Check "boundary-apps-theme" ($actualTheme -eq $ExpectedAppsTheme) `
            $ExpectedAppsTheme $actualTheme
    }
    else {
        Add-Check "boundary-apps-theme" $true "record only" $actualTheme `
            "Pass -ExpectedAppsTheme to make this comparison blocking." $false
    }
    if ($ExpectedColorizationColor) {
        Add-Check "boundary-accent" (
            $actualColor -eq $ExpectedColorizationColor
        ) $ExpectedColorizationColor $actualColor
    }
    else {
        Add-Check "boundary-accent" $true "record only" $actualColor `
            "Pass -ExpectedColorizationColor to make this comparison blocking." $false
    }
    $facts.windowsAppearance = [ordered]@{
        appsTheme = $actualTheme
        appsUseLightTheme = [int]$personalize.AppsUseLightTheme
        colorizationColor = $actualColor
    }
}

if ($Phase -in @("Release", "PreInstall")) {
    Add-ReleaseChecks
}
if ($Phase -eq "PreInstall") {
    Add-EnvironmentChecks
    Add-AbsenceChecks -ExpectDataAbsent $true
}
elseif ($Phase -eq "PostInstall") {
    Add-InstalledChecks -ExpectInitialSetup $true
}
elseif ($Phase -in @("PreUpgrade", "PostUpgrade")) {
    Add-InstalledChecks -ExpectInitialSetup $false
}
elseif ($Phase -eq "PostBoundary") {
    Add-InstalledChecks -ExpectInitialSetup $false
    Add-BoundaryChecks
}
elseif ($Phase -eq "PostUninstall") {
    Add-AbsenceChecks -ExpectDataAbsent (-not $KeepData)
    $processes = @(
        Get-Process -Name ThemeScheduler, Uninstall -ErrorAction SilentlyContinue |
            ForEach-Object { "$($_.ProcessName):$($_.Id)" }
    )
    Add-Check "product-processes-absent" ($processes.Count -eq 0) `
        "none" $processes
    $temporaryRoot = Join-Path $env:TEMP "ThemeScheduler"
    Add-Check "uninstall-temporary-root-absent" (
        -not (Test-Path -LiteralPath $temporaryRoot)
    ) "absent after cleanup delay" (Test-Path -LiteralPath $temporaryRoot)
    Add-BoundaryChecks -RequireTask $false
}

$manual = switch ($Phase) {
    "Release" {
        @("No end-to-end behavior is proven by static release verification.")
    }
    "PreInstall" {
        @(
            "Record whether Windows shows SmartScreen or an Open File security warning.",
            "If WebView2 is absent, confirm Setup blocks before mutation with a clear message."
        )
    }
    "PostInstall" {
        @(
            "Setup completes without a console window or elevation prompt.",
            "The completion action opens the main GUI and the first-run state is visible.",
            "No Windows appearance change occurs before the first successful save."
        )
    }
    "PostBoundary" {
        @(
            "The five-minute notification exposes Confirm, Skip once, and Delay 30 minutes.",
            "The visible application mode, window border, taskbar, and Start color match.",
            "A success notification is delivered about five seconds after the switch."
        )
    }
    "PreUpgrade" {
        @("Keep this report for comparison with PostUpgrade dataFiles.")
    }
    "PostUpgrade" {
        @(
            "Compare dataFiles with PreUpgrade: configuration, profiles, backup, and state persist.",
            "The GUI opens and the existing schedule remains enabled."
        )
    }
    "PostUninstall" {
        @(
            "The selected restore/keep appearance behavior matches the uninstall choice.",
            "Control Panel no longer lists ThemeScheduler.",
            "The uninstall result window closes and both uninstall processes disappear."
        )
    }
}

$failed = @($checks | Where-Object { $_.status -eq "fail" }).Count
$warnings = @($checks | Where-Object { $_.status -eq "warning" }).Count
$report = [ordered]@{
    kind = "themescheduler.clean-machine-check"
    schemaVersion = 1
    capturedAt = [DateTimeOffset]::Now.ToString("o")
    phase = $Phase
    expectedVersion = $ExpectedVersion
    passed = ($failed -eq 0)
    failedChecks = $failed
    warningChecks = $warnings
    checks = $checks.ToArray()
    facts = $facts
    manualVerificationRequired = $manual
}
$json = $report | ConvertTo-Json -Depth 12

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $target = [IO.Path]::GetFullPath($OutputPath)
    if (Test-Path -LiteralPath $target) {
        throw "Refusing to overwrite existing evidence: $target"
    }
    $parent = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Evidence parent directory does not exist: $parent"
    }
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($target, $json + [Environment]::NewLine, $utf8)
}

$json
if ($failed -ne 0) {
    exit 1
}
