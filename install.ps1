# Installs Prisma to %USERPROFILE%\.prisma\app and creates Start-menu + Desktop shortcuts.
$ErrorActionPreference = 'Stop'
$src  = $PSScriptRoot
$home_ = Join-Path $env:USERPROFILE '.prisma'
$dst  = Join-Path $home_ 'app'
$venv = Join-Path $home_ 'venv'

Write-Host "Prisma wird installiert nach $dst"
New-Item -ItemType Directory -Force $dst | Out-Null
robocopy $src $dst /MIR /XD __pycache__ .venv /XF install.ps1 *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null

if (-not (Test-Path (Join-Path $venv 'Scripts\pythonw.exe'))) {
    Write-Host 'Python-Umgebung wird eingerichtet ...'
    python -m venv $venv
    & (Join-Path $venv 'Scripts\python.exe') -m pip install -q --upgrade pip
    & (Join-Path $venv 'Scripts\python.exe') -m pip install -q -r (Join-Path $dst 'requirements.txt')
}

$shell = New-Object -ComObject WScript.Shell
if (-not ('PrismaLnk' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    int GetCount(out uint c); int GetAt(uint i, out PKey k); int GetValue(ref PKey k, out PVar v);
    int SetValue(ref PKey k, ref PVar v); int Commit();
}
[StructLayout(LayoutKind.Sequential, Pack = 4)] struct PKey { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit, Size = 16)] struct PVar { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
public static class PrismaLnk {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern int SHGetPropertyStoreFromParsingName(string path, IntPtr pbc, int flags, ref Guid iid, out IPropertyStore ps);
    public static void SetAppId(string path, string id) {
        Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore ps;
        int hr = SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 2, ref iid, out ps);  // GPS_READWRITE
        if (hr != 0) throw new Exception("HRESULT " + hr);
        PKey k = new PKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };  // System.AppUserModel.ID
        PVar v = new PVar { vt = 31, p = Marshal.StringToCoTaskMemUni(id) };                       // VT_LPWSTR
        try { ps.SetValue(ref k, ref v); ps.Commit(); }
        finally { Marshal.FreeCoTaskMem(v.p); Marshal.ReleaseComObject(ps); }
    }
}
'@
}
$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Prisma.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Prisma.lnk')
)
foreach ($lnk in $targets) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = 'wscript.exe'
    $s.Arguments = '"' + (Join-Path $dst 'Prisma.vbs') + '"'
    $s.WorkingDirectory = $dst
    $s.IconLocation = (Join-Path $dst 'ui\prisma.ico')
    $s.Description = 'Prisma RGB Studio'
    $s.Save()
    # same AppUserModelID as the running window -> the taskbar takes the Prisma icon from this shortcut
    try { [PrismaLnk]::SetAppId($lnk, 'Prisma.RGBStudio') } catch { Write-Host "AppID nicht gesetzt: $_" }
    Write-Host "Verknuepfung: $lnk"
}
Write-Host 'Fertig.'
exit 0
