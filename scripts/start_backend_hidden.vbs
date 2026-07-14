Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptsDir = fso.GetParentFolderName(WScript.ScriptFullName)
rootDir = fso.GetParentFolderName(scriptsDir)
command = """" & rootDir & "\scripts\start_backend.cmd"""

shell.CurrentDirectory = rootDir
shell.Run command, 0, False
