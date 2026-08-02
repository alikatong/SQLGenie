Option Explicit

Dim shell, fso, rootDir, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

rootDir = fso.GetParentFolderName(WScript.ScriptFullName)
command = "wscript.exe """ & rootDir & "\scripts\start_service_hidden.vbs"""

shell.CurrentDirectory = rootDir
shell.Run command, 0, False
