param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Read', 'Write')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$RequestPath
)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

namespace ThemeSchedulerShortcutBridge
{
    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    internal class ShellLink
    {
    }

    [ComImport]
    [Guid("0000010B-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPersistFile
    {
        [PreserveSig]
        int GetClassID(out Guid classId);

        [PreserveSig]
        int IsDirty();

        [PreserveSig]
        int Load(
            [MarshalAs(UnmanagedType.LPWStr)] string fileName,
            uint mode
        );

        [PreserveSig]
        int Save(
            [MarshalAs(UnmanagedType.LPWStr)] string fileName,
            [MarshalAs(UnmanagedType.Bool)] bool remember
        );

        [PreserveSig]
        int SaveCompleted(
            [MarshalAs(UnmanagedType.LPWStr)] string fileName
        );

        [PreserveSig]
        int GetCurFile(
            [MarshalAs(UnmanagedType.LPWStr)] out string fileName
        );
    }

    [ComImport]
    [Guid("000214F9-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IShellLinkW
    {
        [PreserveSig]
        int GetClassID(out Guid classId);

        [PreserveSig]
        int GetPath(
            [Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder fileName,
            int characterCount,
            IntPtr findData,
            uint flags
        );

        [PreserveSig]
        int GetIDList(out IntPtr itemIdList);

        [PreserveSig]
        int SetIDList(IntPtr itemIdList);

        [PreserveSig]
        int GetDescription(
            [Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder description,
            int characterCount
        );

        [PreserveSig]
        int SetDescription(
            [MarshalAs(UnmanagedType.LPWStr)] string description
        );
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    internal struct PropertyKey
    {
        internal Guid formatId;
        internal uint propertyId;

        internal PropertyKey(Guid formatId, uint propertyId)
        {
            this.formatId = formatId;
            this.propertyId = propertyId;
        }
    }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    internal struct PropVariant
    {
        [FieldOffset(0)]
        internal ushort valueType;

        [FieldOffset(8)]
        internal IntPtr pointerValue;
    }

    [ComImport]
    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPropertyStore
    {
        [PreserveSig]
        int GetCount(out uint propertyCount);

        [PreserveSig]
        int GetAt(uint propertyIndex, out PropertyKey key);

        [PreserveSig]
        int GetValue(ref PropertyKey key, out PropVariant value);

        [PreserveSig]
        int SetValue(ref PropertyKey key, ref PropVariant value);

        [PreserveSig]
        int Commit();
    }

    public static class ShortcutIdentity
    {
        private const ushort VT_EMPTY = 0;
        private const ushort VT_LPWSTR = 31;
        private const ushort VT_CLSID = 72;

        private static readonly PropertyKey AppUserModelIdKey =
            new PropertyKey(
                new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
                5
            );

        private static readonly PropertyKey ToastActivatorClsidKey =
            new PropertyKey(
                new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
                26
            );

        [DllImport("ole32.dll")]
        private static extern int PropVariantClear(ref PropVariant value);

        private static void Check(int result, string operation)
        {
            if (result < 0)
            {
                Marshal.ThrowExceptionForHR(
                    result,
                    new IntPtr(-1)
                );
            }
        }

        private static string ReadString(
            string path,
            PropertyKey propertyKey
        )
        {
            object link = new ShellLink();
            try
            {
                IPersistFile persist = (IPersistFile)link;
                Check(persist.Load(path, 0), "IPersistFile.Load");
                IPropertyStore store = (IPropertyStore)link;
                PropertyKey key = propertyKey;
                PropVariant value;
                Check(
                    store.GetValue(ref key, out value),
                    "IPropertyStore.GetValue"
                );
                try
                {
                    if (value.valueType == VT_EMPTY)
                    {
                        return null;
                    }
                    if (
                        value.valueType != VT_LPWSTR ||
                        value.pointerValue == IntPtr.Zero
                    )
                    {
                        throw new InvalidOperationException(
                            "Shortcut AppUserModelID has an unsupported type."
                        );
                    }
                    return Marshal.PtrToStringUni(value.pointerValue);
                }
                finally
                {
                    PropVariantClear(ref value);
                }
            }
            finally
            {
                Marshal.FinalReleaseComObject(link);
            }
        }

        private static string ReadGuid(
            string path,
            PropertyKey propertyKey
        )
        {
            object link = new ShellLink();
            try
            {
                IPersistFile persist = (IPersistFile)link;
                Check(persist.Load(path, 0), "IPersistFile.Load");
                IPropertyStore store = (IPropertyStore)link;
                PropertyKey key = propertyKey;
                PropVariant value;
                Check(
                    store.GetValue(ref key, out value),
                    "IPropertyStore.GetValue"
                );
                try
                {
                    if (value.valueType == VT_EMPTY)
                    {
                        return null;
                    }
                    if (
                        value.valueType != VT_CLSID ||
                        value.pointerValue == IntPtr.Zero
                    )
                    {
                        throw new InvalidOperationException(
                            "Shortcut Toast activator CLSID has an unsupported type."
                        );
                    }
                    Guid identity = (Guid)Marshal.PtrToStructure(
                        value.pointerValue,
                        typeof(Guid)
                    );
                    return identity.ToString("D").ToUpperInvariant();
                }
                finally
                {
                    PropVariantClear(ref value);
                }
            }
            finally
            {
                Marshal.FinalReleaseComObject(link);
            }
        }

        public static string ReadAppUserModelId(string path)
        {
            return ReadString(path, AppUserModelIdKey);
        }

        public static string ReadToastActivatorClsid(string path)
        {
            return ReadGuid(path, ToastActivatorClsidKey);
        }

        public static string ReadDescription(string path)
        {
            object link = new ShellLink();
            try
            {
                IPersistFile persist = (IPersistFile)link;
                Check(persist.Load(path, 0), "IPersistFile.Load");
                IShellLinkW shellLink = (IShellLinkW)link;
                StringBuilder description = new StringBuilder(1024);
                Check(
                    shellLink.GetDescription(description, description.Capacity),
                    "IShellLinkW.GetDescription"
                );
                return description.ToString();
            }
            finally
            {
                Marshal.FinalReleaseComObject(link);
            }
        }

        public static void WriteDescription(string path, string description)
        {
            object link = new ShellLink();
            try
            {
                IPersistFile persist = (IPersistFile)link;
                Check(persist.Load(path, 2), "IPersistFile.Load");
                IShellLinkW shellLink = (IShellLinkW)link;
                Check(
                    shellLink.SetDescription(description),
                    "IShellLinkW.SetDescription"
                );
                Check(persist.Save(path, true), "IPersistFile.Save");
            }
            finally
            {
                Marshal.FinalReleaseComObject(link);
            }
        }

        public static void Write(
            string path,
            string appUserModelId,
            string toastActivatorClsid
        )
        {
            object link = new ShellLink();
            try
            {
                IPersistFile persist = (IPersistFile)link;
                Check(persist.Load(path, 2), "IPersistFile.Load");
                IPropertyStore store = (IPropertyStore)link;
                PropertyKey appIdKey = AppUserModelIdKey;
                PropVariant appIdValue = new PropVariant();
                appIdValue.valueType = VT_LPWSTR;
                appIdValue.pointerValue =
                    Marshal.StringToCoTaskMemUni(appUserModelId);
                PropertyKey clsidKey = ToastActivatorClsidKey;
                PropVariant clsidValue = new PropVariant();
                clsidValue.valueType = VT_CLSID;
                Guid clsid = new Guid(toastActivatorClsid);
                clsidValue.pointerValue = Marshal.AllocCoTaskMem(
                    Marshal.SizeOf(typeof(Guid))
                );
                Marshal.StructureToPtr(
                    clsid,
                    clsidValue.pointerValue,
                    false
                );
                try
                {
                    Check(
                        store.SetValue(ref appIdKey, ref appIdValue),
                        "IPropertyStore.SetValue AppUserModelID"
                    );
                    Check(
                        store.SetValue(ref clsidKey, ref clsidValue),
                        "IPropertyStore.SetValue ToastActivatorCLSID"
                    );
                    Check(store.Commit(), "IPropertyStore.Commit");
                    Check(
                        persist.Save(path, true),
                        "IPersistFile.Save"
                    );
                }
                finally
                {
                    PropVariantClear(ref appIdValue);
                    PropVariantClear(ref clsidValue);
                }
            }
            finally
            {
                Marshal.FinalReleaseComObject(link);
            }
        }
    }
}
'@

function Write-Result {
    param([hashtable]$Value)
    $Value | ConvertTo-Json -Depth 8 -Compress
}

function Assert-ExactProperties {
    param(
        [object]$Value,
        [string[]]$Expected
    )
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if (($actual -join "`n") -ne ($wanted -join "`n")) {
        throw 'Shortcut request contains missing or unknown fields.'
    }
}

function Test-FullyQualifiedPath {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    return (
        $Value -match '^[A-Za-z]:[\\/]' -or
        $Value -match '^\\\\[^\\]+\\[^\\]+(?:\\|$)'
    )
}

function Resolve-ManagedShortcutPath {
    param([object]$Value)
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value)) {
        throw 'Shortcut path must be a non-empty string.'
    }
    if (-not (Test-FullyQualifiedPath $Value)) {
        throw 'Shortcut path must be absolute.'
    }
    $resolved = [IO.Path]::GetFullPath($Value)
    if (
        [IO.Path]::GetFileName($resolved) -cne 'ThemeScheduler.lnk' -or
        [IO.Path]::GetExtension($resolved) -ine '.lnk'
    ) {
        throw 'Shortcut path is not managed by ThemeScheduler.'
    }
    return $resolved
}

$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 |
    ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell

if ($Action -eq 'Read') {
    Assert-ExactProperties $request @('path')
    $path = Resolve-ManagedShortcutPath $request.path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Result @{
            ok = $true
            action = 'Read'
            exists = $false
            readable = $false
            shortcut = $null
            shortcutChanged = $false
        }
        exit 0
    }
    try {
        $shortcut = $shell.CreateShortcut($path)
        $definition = @{
            path = $path
            target = [string]$shortcut.TargetPath
            arguments = [string]$shortcut.Arguments
            workingDirectory = [string]$shortcut.WorkingDirectory
            description = (
                [ThemeSchedulerShortcutBridge.ShortcutIdentity]::
                    ReadDescription($path)
            )
            iconLocation = [string]$shortcut.IconLocation
            appUserModelId = (
                [ThemeSchedulerShortcutBridge.ShortcutIdentity]::
                    ReadAppUserModelId($path)
            )
            toastActivatorClsid = (
                [ThemeSchedulerShortcutBridge.ShortcutIdentity]::
                    ReadToastActivatorClsid($path)
            )
        }
    }
    catch {
        Write-Result @{
            ok = $true
            action = 'Read'
            exists = $true
            readable = $false
            shortcutChanged = $false
            shortcut = $null
        }
        exit 0
    }
    Write-Result @{
        ok = $true
        action = 'Read'
        exists = $true
        readable = $true
        shortcutChanged = $false
        shortcut = $definition
    }
    exit 0
}

Assert-ExactProperties $request @(
    'path',
    'target',
    'arguments',
    'workingDirectory',
    'description',
    'iconLocation',
    'appUserModelId',
    'toastActivatorClsid'
)
$path = Resolve-ManagedShortcutPath $request.path
foreach ($name in @(
    'target',
    'arguments',
    'workingDirectory',
    'description',
    'iconLocation',
    'appUserModelId',
    'toastActivatorClsid'
)) {
    if ($request.$name -isnot [string]) {
        throw "Shortcut field $name must be a string."
    }
}
if (
    -not (Test-FullyQualifiedPath $request.target) -or
    [IO.Path]::GetFileName($request.target) -cne 'ThemeScheduler.exe'
) {
    throw 'Shortcut target is invalid.'
}
if (-not (Test-FullyQualifiedPath $request.workingDirectory)) {
    throw 'Shortcut working directory is invalid.'
}
if ($request.arguments -cne '') {
    throw 'Shortcut arguments must be empty.'
}
if ($request.appUserModelId -cne 'ThemeScheduler.ThemeScheduler') {
    throw 'Shortcut AppUserModelID is invalid.'
}
if (
    $request.toastActivatorClsid -cne
    '403DE4CE-F3F9-4335-B847-8B2297DC9B6F'
) {
    throw 'Shortcut Toast activator CLSID is invalid.'
}
$shortcut = $shell.CreateShortcut($path)
$shortcut.TargetPath = $request.target
$shortcut.Arguments = $request.arguments
$shortcut.WorkingDirectory = $request.workingDirectory
$shortcut.Description = $request.description
$shortcut.IconLocation = $request.iconLocation
$shortcut.Save()
[ThemeSchedulerShortcutBridge.ShortcutIdentity]::WriteDescription(
    $path,
    $request.description
)
[ThemeSchedulerShortcutBridge.ShortcutIdentity]::Write(
    $path,
    $request.appUserModelId,
    $request.toastActivatorClsid
)
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw 'Shortcut was not created.'
}
$identity = (
    [ThemeSchedulerShortcutBridge.ShortcutIdentity]::
        ReadAppUserModelId($path)
)
$toastClsid = (
    [ThemeSchedulerShortcutBridge.ShortcutIdentity]::
        ReadToastActivatorClsid($path)
)
if ($identity -cne $request.appUserModelId) {
    throw 'Shortcut AppUserModelID readback mismatch.'
}
if ($toastClsid -cne $request.toastActivatorClsid) {
    throw 'Shortcut Toast activator CLSID readback mismatch.'
}
Write-Result @{
    ok = $true
    action = 'Write'
    exists = $true
    path = $path
    shortcutChanged = $true
}
