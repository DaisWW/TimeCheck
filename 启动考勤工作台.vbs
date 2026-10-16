Option Explicit
Dim shell, files, root
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
On Error Resume Next
shell.Run "pythonw.exe """ & root & "\main.py""", 0, False
If Err.Number <> 0 Then
  MsgBox "Cannot start Attendance Studio. Install Python on PATH and project requirements, or extract the release ZIP and open dingding-attendance.exe.", 16, "Attendance Studio"
End If
