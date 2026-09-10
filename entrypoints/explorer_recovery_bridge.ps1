param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Restart")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class ThemeSchedulerShellWindow {
    [DllImport("user32.dll")]
    public static extern IntPtr GetShellWindow();

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@

function Get-ScopedShell {
    $handle = [ThemeSchedulerShellWindow]::GetShellWindow()
    if ($handle -eq [IntPtr]::Zero) {
        return $null
    }
    [uint32]$processId = 0
    [void][ThemeSchedulerShellWindow]::GetWindowThreadProcessId($handle, [ref]$processId)
    if ($processId -eq 0) {
        return $null
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    $currentSession = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
    if ($process.ProcessName -ne "explorer" -or $process.SessionId -ne $currentSession) {
        throw "The shell window did not belong to Explorer in the current session."
    }
    return @{
        Process = $process
        ProcessId = [int]$process.Id
        SessionId = [int]$process.SessionId
    }
}

try {
    $oldShell = Get-ScopedShell
    if ($null -eq $oldShell) {
        throw "No Explorer shell window was found in the current session."
    }

    $oldProcessId = $oldShell.ProcessId
    $sessionId = $oldShell.SessionId
    $startedRecovery = $false
    try {
        Stop-Process -Id $oldProcessId -ErrorAction Stop
        $stopDeadline = [DateTime]::UtcNow.AddSeconds(8)
        while (
            $null -ne (Get-Process -Id $oldProcessId -ErrorAction SilentlyContinue) -and
            [DateTime]::UtcNow -lt $stopDeadline
        ) {
            Start-Sleep -Milliseconds 100
        }
    }
    finally {
        $currentShell = Get-ScopedShell
        if ($null -eq $currentShell) {
            # Windows normally restarts its registered shell itself.  Give that
            # path time to win the race; immediately launching explorer.exe a
            # second time can open an unwanted File Explorer Home window.
            $automaticRecoveryDeadline = [DateTime]::UtcNow.AddSeconds(5)
            do {
                Start-Sleep -Milliseconds 250
                $currentShell = Get-ScopedShell
            } while (
                $null -eq $currentShell -and
                [DateTime]::UtcNow -lt $automaticRecoveryDeadline
            )
        }
        if ($null -eq $currentShell) {
            $explorerPath = Join-Path $env:WINDIR "explorer.exe"
            Start-Process -FilePath $explorerPath | Out-Null
            $startedRecovery = $true
        }
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $newShell = $null
    do {
        Start-Sleep -Milliseconds 250
        $newShell = Get-ScopedShell
    } while ($null -eq $newShell -and [DateTime]::UtcNow -lt $deadline)

    if ($null -eq $newShell) {
        throw "Explorer did not restore its shell window within 15 seconds."
    }
    if ($newShell.ProcessId -eq $oldProcessId) {
        throw "Explorer shell PID did not change."
    }

    [ordered]@{
        ok = $true
        action = $Action
        recovered = $true
        sessionId = $sessionId
        oldProcessId = $oldProcessId
        newProcessId = $newShell.ProcessId
        recoveryProcessStarted = $startedRecovery
        recoveryMode = $(if ($startedRecovery) { "explicit-watchdog-start" } else { "windows-auto-restart" })
    } | ConvertTo-Json -Compress
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
