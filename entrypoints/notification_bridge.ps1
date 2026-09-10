param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Probe', 'Send')]
    [string]$Action,

    [string]$RequestPath
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$FrozenAumid = 'ThemeScheduler.ThemeScheduler'
$FrozenScheme = 'themescheduler-action'
$FrozenHealthUri = 'themescheduler-action://health/'

function Write-BridgeResult {
    param([hashtable]$Payload)
    $Payload | ConvertTo-Json -Depth 8 -Compress
}

function Read-BridgeRequest {
    if ([string]::IsNullOrWhiteSpace($RequestPath)) {
        throw 'Notification request path is required.'
    }
    $resolved = Resolve-Path -LiteralPath $RequestPath
    $item = Get-Item -LiteralPath $resolved.Path
    if ($item.Length -le 0 -or $item.Length -gt 65536) {
        throw 'Notification request is empty or too large.'
    }
    return (
        Get-Content -LiteralPath $resolved.Path -Raw -Encoding UTF8 |
        ConvertFrom-Json
    )
}

function Escape-XmlText {
    param([string]$Value)
    return [System.Security.SecurityElement]::Escape($Value)
}

function Assert-BoundedText {
    param(
        [string]$Value,
        [string]$Label,
        [int]$Maximum
    )
    if (
        [string]::IsNullOrWhiteSpace($Value) -or
        $Value.Length -gt $Maximum -or
        $Value.IndexOfAny([char[]]"`r`n`t") -ge 0
    ) {
        throw "$Label is empty, too long, or contains control characters."
    }
}

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

if ($Action -eq 'Probe') {
    Write-BridgeResult @{
        ok = $true
        action = 'Probe'
        available = $true
        notificationSent = $false
    }
    exit 0
}

$request = Read-BridgeRequest
if ($request.appUserModelId -ne $FrozenAumid) {
    throw 'Notification AppUserModelID does not match the product identity.'
}
Assert-BoundedText -Value ([string]$request.title) -Label 'title' -Maximum 64
Assert-BoundedText -Value ([string]$request.body) -Label 'body' -Maximum 240

$buttons = @($request.buttons)
if ($buttons.Count -gt 3) {
    throw 'A notification can contain at most three buttons.'
}
$buttonXml = ''
foreach ($button in $buttons) {
    Assert-BoundedText -Value ([string]$button.content) -Label 'button content' -Maximum 32
    if ($button.activationType -ne 'protocol') {
        throw 'Only protocol notification actions are allowed.'
    }
    $uri = $null
    if (
        -not [Uri]::TryCreate(
            [string]$button.arguments,
            [UriKind]::Absolute,
            [ref]$uri
        ) -or
        $uri.Scheme -ne $FrozenScheme
    ) {
        throw 'Notification action URI is not a product protocol URI.'
    }
    $buttonXml += (
        '<action content="' +
        (Escape-XmlText ([string]$button.content)) +
        '" activationType="protocol" arguments="' +
        (Escape-XmlText ([string]$button.arguments)) +
        '" />'
    )
}

$toastOpen = '<toast>'
if ($null -ne $request.launch) {
    if ([string]$request.launch -ne $FrozenHealthUri) {
        throw 'Notification launch URI does not match the health endpoint.'
    }
    $toastOpen = (
        '<toast activationType="protocol" launch="' +
        (Escape-XmlText ([string]$request.launch)) +
        '">'
    )
}

$actionsXml = if ($buttons.Count -gt 0) {
    "<actions>$buttonXml</actions>"
} else {
    ''
}
$toastXml = (
    $toastOpen + '<visual><binding template="ToastGeneric">' +
    '<text>' + (Escape-XmlText ([string]$request.title)) + '</text>' +
    '<text>' + (Escape-XmlText ([string]$request.body)) + '</text>' +
    '</binding></visual>' + $actionsXml + '</toast>'
)
$document = [Windows.Data.Xml.Dom.XmlDocument]::new()
$document.LoadXml($toastXml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($document)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(
    $FrozenAumid
)
$notifier.Show($toast)

Write-BridgeResult @{
    ok = $true
    action = 'Send'
    sent = $true
    notificationSent = $true
}
