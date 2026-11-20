"""LibreHardwareMonitor access (runs only inside the elevated fan service)."""
import json
import os
import sys
from pathlib import Path

LHM_DIR = Path(os.environ.get("PRISMA_HOME", Path.home() / ".prisma")) / "lhm"
NEEDED = ["LibreHardwareMonitorLib.dll", "HidSharp.dll", "System.Management.dll", "System.CodeDom.dll",
          "RAMSPDToolkit-NDD.dll", "DiskInfoToolkit.dll", "BlackSharp.Core.dll", "System.IO.Ports.dll",
          "System.Threading.AccessControl.dll", "System.ServiceProcess.ServiceController.dll"]
SOURCES = [Path(r"C:\Program Files (x86)\FanControl"), Path(r"C:\Program Files\FanControl")]


def ensure_lib():
    """Copy LibreHardwareMonitor (+deps) from an installed FanControl into ~/.prisma/lhm once."""
    if (LHM_DIR / "LibreHardwareMonitorLib.dll").exists():
        return True
    import shutil
    for src in SOURCES:
        if (src / "LibreHardwareMonitorLib.dll").exists():
            LHM_DIR.mkdir(parents=True, exist_ok=True)
            for f in src.glob("*.dll"):  # LHM pulls in several helpers (Mono.Posix, HidSharp …) – take them all
                shutil.copy2(f, LHM_DIR / f.name)
            return True
    return False


def load_computer():
    """Return an opened LibreHardwareMonitor Computer (coreclr / .NET 10)."""
    from pythonnet import load
    cfg = LHM_DIR / "prisma.runtimeconfig.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({"runtimeOptions": {"tfm": "net10.0", "rollForward": "LatestMajor",
                                                      "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}))
    load("coreclr", runtime_config=str(cfg))
    sys.path.append(str(LHM_DIR))
    import clr  # noqa
    clr.AddReference(str(LHM_DIR / "LibreHardwareMonitorLib.dll"))
    from LibreHardwareMonitor.Hardware import Computer
    c = Computer()
    c.IsCpuEnabled = True
    c.IsMotherboardEnabled = True
    c.IsGpuEnabled = True
    c.IsControllerEnabled = True
    c.Open()
    return c


def walk(computer):
    """Yield every hardware node (including Super I/O sub-hardware)."""
    def rec(h):
        yield h
        for s in h.SubHardware:
            yield from rec(s)
    for h in computer.Hardware:
        yield from rec(h)


def update(computer):
    for h in walk(computer):
        h.Update()


if __name__ == "__main__":  # read-only probe
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else LHM_DIR / "probe.json"
    res = {"ok": False}
    try:
        res["copied"] = ensure_lib()
        c = load_computer()
        update(c)
        res["sensors"] = []
        for h in walk(c):
            for s in h.Sensors:
                st = str(s.SensorType)
                if st in ("Temperature", "Fan", "Control"):
                    res["sensors"].append({"hw": h.Name, "id": str(s.Identifier), "name": s.Name, "type": st,
                                           "value": None if s.Value is None else round(float(s.Value), 1),
                                           "controllable": s.Control is not None})
        res["ok"] = True
        c.Close()
    except Exception as e:
        import traceback
        res["error"] = traceback.format_exc()
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
