Option Explicit
Dim shell, files, root
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
On Error Resume Next
shell.Run "pythonw.exe """ & root & "\main.py""", 0, False
If Err.Number <> 0 Then
  MsgBox "Cannot start Attendance Studio. Install Python 3.11 or later on PATH, then run: python -m pip install -r requirements.txt", 16, "Attendance Studio"
End If
