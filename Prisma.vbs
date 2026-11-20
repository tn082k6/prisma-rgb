' Starts Prisma without a console window.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
py = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.prisma\venv\Scripts\pythonw.exe"
args = ""
If WScript.Arguments.Count > 0 Then args = " " & WScript.Arguments(0)
sh.CurrentDirectory = dir
sh.Run """" & py & """ """ & dir & "\app.py""" & args, 0, False
