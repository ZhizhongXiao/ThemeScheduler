param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Read')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

[Windows.UI.ViewManagement.UISettings, Windows.UI.ViewManagement, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.ViewManagement.UIColorType, Windows.UI.ViewManagement, ContentType = WindowsRuntime] | Out-Null

$settings = [Windows.UI.ViewManagement.UISettings]::new()
$accent = $settings.GetColorValue(
    [Windows.UI.ViewManagement.UIColorType]::Accent
)

[ordered]@{
    ok = $true
    action = 'Read'
    source = 'winrt-ui-settings'
    color = [ordered]@{
        alpha = [int]$accent.A
        red = [int]$accent.R
        green = [int]$accent.G
        blue = [int]$accent.B
    }
} | ConvertTo-Json -Depth 3 -Compress
