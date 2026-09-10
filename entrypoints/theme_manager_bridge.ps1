param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('CurrentV2', 'ApplyV2', 'SetV2')]
    [string]$Action,

    [string]$ThemePath,

    [int]$ThemeIndex = -1
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace ThemeScheduler.NativeBridge
{
    [ComImport]
    [Guid("26E4185F-0528-475F-ACAF-ABE89BA6017D")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface ITheme2
    {
        [return: MarshalAs(UnmanagedType.BStr)] string GetDisplayName();
        void SetDisplayName([In, MarshalAs(UnmanagedType.BStr)] string value);
        [return: MarshalAs(UnmanagedType.BStr)] string GetVisualStyle1();
        void SetVisualStyle1([In, MarshalAs(UnmanagedType.BStr)] string value);
        [return: MarshalAs(UnmanagedType.BStr)] string GetVisualStyle2();
        void SetVisualStyle2([In, MarshalAs(UnmanagedType.BStr)] string value);
    }

    [ComImport]
    [Guid("C1E8C83E-845D-4D95-81DB-E283FDFFC000")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IThemeManager2
    {
        void Init(int flags);
        void InitAsync(IntPtr hwnd, int unknown);
        void Refresh();
        void RefreshAsync(IntPtr hwnd, int unknown);
        void RefreshComplete();
        [PreserveSig] int GetThemeCount(out int count);
        void GetTheme(int index, [MarshalAs(UnmanagedType.Interface)] out ITheme2 theme);
        void IsThemeDisabled(int index, out int disabled);
        void GetCurrentTheme(out int index);
        [PreserveSig] int SetCurrentTheme(IntPtr parent, int themeIndex, int applyNow, int applyFlags, int packFlags);
        void GetCustomTheme(out int index);
        void GetDefaultTheme(out int index);
        void CreateThemePack(IntPtr hwnd, [In, MarshalAs(UnmanagedType.BStr)] string path, int packFlags);
        void CloneAndSetCurrentTheme(IntPtr hwnd, [In, MarshalAs(UnmanagedType.BStr)] string source, [MarshalAs(UnmanagedType.BStr)] out string target);
        void InstallThemePack(IntPtr hwnd, [In, MarshalAs(UnmanagedType.BStr)] string path, int unknown, int packFlags, [MarshalAs(UnmanagedType.BStr)] out string target, [MarshalAs(UnmanagedType.Interface)] out ITheme2 theme);
        void DeleteTheme([In, MarshalAs(UnmanagedType.BStr)] string name);
        [PreserveSig] int OpenTheme(IntPtr hwnd, [In, MarshalAs(UnmanagedType.BStr)] string path, int packFlags);
        [PreserveSig] int AddAndSelectTheme(IntPtr hwnd, [In, MarshalAs(UnmanagedType.BStr)] string path, int applyFlags, int packFlags);
    }

    public static class ThemeBridge
    {
        private static IThemeManager2 CreateManager2()
        {
            Guid classId = new Guid("9324DA94-50EC-4A14-A770-E90CA03E7C8F");
            Type managerType = Type.GetTypeFromCLSID(classId, true);
            IThemeManager2 manager = (IThemeManager2)Activator.CreateInstance(managerType);
            manager.Init(0);
            return manager;
        }

        public static int GetCurrentV2Index()
        {
            IThemeManager2 manager = CreateManager2();
            try
            {
                int index;
                manager.GetCurrentTheme(out index);
                return index;
            }
            finally
            {
                if (manager != null && Marshal.IsComObject(manager))
                {
                    Marshal.FinalReleaseComObject(manager);
                }
            }
        }

        public static int GetCustomV2Index()
        {
            IThemeManager2 manager = CreateManager2();
            try
            {
                int index;
                manager.GetCustomTheme(out index);
                return index;
            }
            finally
            {
                if (manager != null && Marshal.IsComObject(manager))
                {
                    Marshal.FinalReleaseComObject(manager);
                }
            }
        }

        public static int[] ApplyV2(string themePath, int applyFlags, int packFlags)
        {
            IThemeManager2 manager = CreateManager2();
            try
            {
                int before;
                manager.GetCurrentTheme(out before);
                int result = manager.AddAndSelectTheme(IntPtr.Zero, themePath, applyFlags, packFlags);
                if (result != 0)
                {
                    throw new COMException("AddAndSelectTheme failed", result);
                }
                int after;
                manager.GetCurrentTheme(out after);
                return new int[] { before, after };
            }
            finally
            {
                if (manager != null && Marshal.IsComObject(manager))
                {
                    Marshal.FinalReleaseComObject(manager);
                }
            }
        }

        public static int SetV2(int themeIndex, int applyFlags)
        {
            IThemeManager2 manager = CreateManager2();
            try
            {
                int result = manager.SetCurrentTheme(IntPtr.Zero, themeIndex, 1, applyFlags, 0);
                if (result != 0)
                {
                    throw new COMException("SetCurrentTheme failed", result);
                }
                int after;
                manager.GetCurrentTheme(out after);
                return after;
            }
            finally
            {
                if (manager != null && Marshal.IsComObject(manager))
                {
                    Marshal.FinalReleaseComObject(manager);
                }
            }
        }
    }
}
'@

if ($Action -eq 'CurrentV2') {
    $index = [ThemeScheduler.NativeBridge.ThemeBridge]::GetCurrentV2Index()
    $custom = [ThemeScheduler.NativeBridge.ThemeBridge]::GetCustomV2Index()
    [ordered]@{ ok = $true; action = 'CurrentV2'; currentIndex = $index; customIndex = $custom } |
        ConvertTo-Json -Compress
    exit 0
}

if ($Action -eq 'SetV2') {
    if ($ThemeIndex -lt 0) {
        throw 'ThemeIndex must be non-negative for SetV2.'
    }
    $after = [ThemeScheduler.NativeBridge.ThemeBridge]::SetV2($ThemeIndex, 55)
    [ordered]@{ ok = $true; action = 'SetV2'; requestedIndex = $ThemeIndex; currentIndex = $after } |
        ConvertTo-Json -Compress
    exit 0
}

if ([string]::IsNullOrWhiteSpace($ThemePath)) {
    throw 'ThemePath is required for ApplyV2.'
}

$resolved = (Resolve-Path -LiteralPath $ThemePath).Path
if ([IO.Path]::GetExtension($resolved) -ine '.theme') {
    throw "ThemePath must identify a .theme file: $resolved"
}

$indices = [ThemeScheduler.NativeBridge.ThemeBridge]::ApplyV2($resolved, 55, 4)
[ordered]@{
    ok = $true
    action = 'ApplyV2'
    themePath = $resolved
    beforeIndex = $indices[0]
    currentIndex = $indices[1]
    applyFlags = 55
    packFlags = 4
} | ConvertTo-Json -Compress
