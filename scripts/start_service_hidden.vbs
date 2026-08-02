Option Explicit

Dim shell, fso, scriptsDir, rootDir, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptsDir = fso.GetParentFolderName(WScript.ScriptFullName)
rootDir = fso.GetParentFolderName(scriptsDir)
command = "cmd.exe /d /c call """ & rootDir & "\start.cmd"" --no-browser 1>> """ & rootDir & "\service-start.log"" 2>&1"

shell.CurrentDirectory = rootDir
shell.Run command, 0, False
