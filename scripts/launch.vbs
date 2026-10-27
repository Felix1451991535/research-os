' Research OS shortcut target: runs launch.ps1 with no visible window (owner rule: never pop console windows).
' Windows execution: NOT RUN on the dev box (Linux).
Dim fso, here, sh
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\launch.ps1""", 0, False
