// Prisma.exe – small native starter so Windows (Task Manager, taskbar, Alt+Tab) shows "Prisma" with
// its own icon instead of "Python". It starts the Python app (or, with --fans, the fan service) and waits;
// Task Manager groups the Python processes underneath. Built by install.ps1 / the updater with the
// C# compiler that ships with Windows (.NET Framework csc.exe).
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

[assembly: AssemblyTitle("Prisma")]
[assembly: AssemblyDescription("Prisma RGB Studio")]
[assembly: AssemblyProduct("Prisma RGB Studio")]
[assembly: AssemblyCompany("Prisma")]
[assembly: AssemblyVersion("1.4.2.0")]
[assembly: AssemblyFileVersion("1.4.2.0")]

static class PrismaLauncher
{
    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string pythonw = Path.Combine(home, ".prisma", "venv", "Scripts", "pythonw.exe");
        bool fans = args.Length > 0 && args[0] == "--fans";
        string script = Path.Combine(dir, fans ? "fanservice.py" : "app.py");
        string rest = "";
        if (!fans)
            foreach (string a in args) rest += " \"" + a.Replace("\"", "") + "\"";
        var psi = new ProcessStartInfo(pythonw, "\"" + script + "\"" + rest)
        {
            UseShellExecute = false,
            WorkingDirectory = dir,
            CreateNoWindow = true,
        };
        try
        {
            using (Process p = Process.Start(psi))
            {
                p.WaitForExit();
                return p.ExitCode;
            }
        }
        catch (Exception e)
        {
            File.AppendAllText(Path.Combine(home, ".prisma", "launcher.log"), DateTime.Now + " " + e + Environment.NewLine);
            return 1;
        }
    }
}
