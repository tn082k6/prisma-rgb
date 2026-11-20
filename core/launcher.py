"""Build and locate Prisma.exe (see launcher.cs)."""
import glob
import os
import subprocess
from pathlib import Path


def exe_path(app_dir):
    p = Path(app_dir) / "Prisma.exe"
    return p if p.exists() else None


def build(app_dir):
    """Compile launcher.cs with the C# compiler that ships with Windows. Works while Prisma.exe is running:
    the running file is renamed out of the way (Windows allows renaming a running exe)."""
    app_dir = Path(app_dir)
    src = app_dir / "launcher.cs"
    if not src.exists():
        return False
    cands = sorted(glob.glob(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), r"Microsoft.NET\Framework64\v4*\csc.exe")))
    if not cands:
        return False
    out = app_dir / "Prisma.exe"
    tmp = app_dir / "Prisma.build.exe"
    r = subprocess.run([cands[-1], "/nologo", "/target:winexe", "/optimize+", f"/win32icon:{app_dir / 'ui' / 'prisma.ico'}",
                        f"/out:{tmp}", str(src)], capture_output=True, creationflags=0x08000000)
    if r.returncode != 0 or not tmp.exists():
        return False
    old = app_dir / "Prisma.old.exe"
    try:
        if old.exists():
            old.unlink()
    except OSError:
        pass
    if out.exists():
        try:
            out.unlink()
        except OSError:
            os.replace(out, old)  # in use -> move aside
    os.replace(tmp, out)
    return True


def command(app_dir, *args):
    """Command line that starts Prisma (exe if built, else the VBS fallback)."""
    exe = exe_path(app_dir)
    a = " ".join(args)
    if exe:
        return f'"{exe}" {a}'.strip()
    return f'wscript.exe "{Path(app_dir) / "Prisma.vbs"}" {a}'.strip()
