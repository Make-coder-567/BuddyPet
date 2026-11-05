' WorkBuddy Desktop Pet - silent launcher (no console window at all)
Option Explicit
Dim sh, fso, dir, pyw, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python314\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
sh.CurrentDirectory = dir
cmd = """" & pyw & """ """ & dir & "\main.py"""
sh.Run cmd, 0, False
