Option Explicit
' Launch_FastChat_Popup.vbs
'
' Phase 3 standalone launcher. Opens JS8FastChat with the full console
' window hidden and the FastChat popup pre-targeted to one callsign.
'
' This is what a future JS8Map handoff button (Phase 4) would shell out to,
' or the model for the equivalent subprocess call from JS8Map's own Python
' backend. For now it can be run directly for testing:
'
'   Launch_FastChat_Popup.vbs KO4BIA
'
' or by double-click, which will prompt for a callsign via an input box.
Dim fso, shell, folder, appPy, packageFile, pyExe, cmd, callsign
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
appPy = fso.BuildPath(folder, "JS8FastChat.py")
packageFile = fso.BuildPath(folder, "js8fastchat\app.py")

If Not fso.FileExists(appPy) Then
    MsgBox "Cannot find " & appPy, vbCritical, "JS8FastChat 1.75"
    WScript.Quit 1
End If
If Not fso.FileExists(packageFile) Then
    MsgBox "Cannot find js8fastchat\app.py beside this launcher. Keep the full js8fastchat folder with this VBS file.", vbCritical, "JS8FastChat 1.75"
    WScript.Quit 1
End If

If WScript.Arguments.Count >= 1 Then
    callsign = Trim(WScript.Arguments(0))
Else
    callsign = Trim(InputBox("Callsign to open in the FastChat popup:", "JS8FastChat 1.75"))
End If

If callsign = "" Then
    WScript.Quit 0  ' canceled or blank -- quietly do nothing, same as canceling a dialog
End If

pyExe = "pythonw.exe"
cmd = "cmd /c cd /d """ & folder & """ && " & pyExe & " """ & appPy & """ --callsign """ & callsign & """"
shell.Run cmd, 0, False
