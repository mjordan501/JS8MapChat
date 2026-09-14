Option Explicit
Dim fso, shell, folder, appPy, packageFile, pyExe, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
appPy = fso.BuildPath(folder, "JS8FastChat.py")
packageFile = fso.BuildPath(folder, "js8fastchat\app.py")
If Not fso.FileExists(appPy) Then
    MsgBox "Cannot find " & appPy, vbCritical, "JS8MapChat 1.75"
    WScript.Quit 1
End If
If Not fso.FileExists(packageFile) Then
    MsgBox "Cannot find js8fastchat\app.py beside this launcher. Keep the full js8fastchat folder with this VBS file.", vbCritical, "JS8MapChat 1.75"
    WScript.Quit 1
End If
pyExe = "pythonw.exe"
cmd = "cmd /c cd /d """ & folder & """ && " & pyExe & " """ & appPy & """"
shell.Run cmd, 0, False
