; ============================================================================
;  JS8FastChat.iss  --  Inno Setup script for the JS8FastChat installer
;  KW3KW - Ham Made Simple                                   Version 1.75
; ============================================================================
;
;  WHY THIS FILE EXISTS
;  --------------------
;  JS8Map.iss ships dist\JS8Map.exe and nothing else. FastChat was never in it.
;  On an installed machine there was therefore NO WAY to obtain JS8FastChat --
;  it built to dist\JS8FastChat.exe and stopped there. This script wraps that
;  exe the same way JS8Map's does, so the two programs can be handed to another
;  operator as two installers: the map first, the console after, if they want
;  it.
;
;  BUILD ORDER
;  -----------
;    1. Run Build_JS8FastChat.bat      -> produces dist\JS8FastChat.exe
;    2. Compile THIS file in Inno Setup -> installer_output\JS8FastChat_v1.75_Setup.exe
;  Compiling without step 1 fails at the [Files] line below, visibly, naming
;  the missing exe. It cannot silently ship a stale build.
;
;  WHAT IS *NOT* HERE, ON PURPOSE
;  ------------------------------
;  No C:\FCC_DB entry. JS8Map's installer creates that folder and sets its
;  permissions; JS8Map's first-run wizard fills it. FastChat only ever reads
;  the databases. Two installers both claiming the same folder root is how
;  permissions end up fighting each other, so exactly one of them owns it.
;
;  No build_canadian_db.py. Same reason -- that belongs to the program that
;  downloads and rebuilds the databases, which is JS8Map.
;
;  No bundled data files. JS8FastChat.spec builds onefile and carries
;  js8map_icon.png and JS8FastChat_icon.ico INSIDE the exe (see the datas
;  block in that spec). The only loose copy installed here is the .ico, and
;  that exists solely so the Start Menu and desktop shortcuts have an icon to
;  point at -- Windows reads shortcut icons from disk, not from inside a
;  onefile exe at runtime.
; ============================================================================

[Setup]
; --- APPID -- ADDED 2026-08-29. NEVER CHANGE THIS VALUE. -------------------
; Same reasoning as JS8Map.iss, which carries the matching comment and has had
; its AppId since 2026-08-28. Without one, Inno identifies the product by
; AppName + AppVersion, so each new version registers as a SEPARATE product:
; the old one drops out of Add/Remove Programs with its files still on disk,
; and setup cannot remember where the previous install went.
;
; THIS GUID IS DIFFERENT FROM JS8MAP'S, AND MUST STAY THAT WAY. Two products
; sharing an AppId each uninstall the other. JS8Map's is
; {8F3A6C21-5E4B-4D79-9A0C-B7E2D14F6835} -- never reuse it here.
;
; Brace style, which is easy to get wrong: Inno treats '{' as the start of a
; constant, so a literal one is written '{{'. It does NOT treat '}' as
; special, so the closing brace is written ONCE. Doubling both ends compiles
; and installs, but writes a malformed registry key ending '}}_is1'. That
; mistake was made and caught on JS8Map.iss on 2026-08-28.
;
; AppVersion changes freely. This line does not.
AppId={{EDFA7F85-3E78-4E27-93C7-B5F15AD792B6}
AppName=JS8FastChat
AppVersion=1.75
AppVerName=JS8FastChat 1.75
AppPublisher=KW3KW - Ham Made Simple
DefaultDirName={autopf}\JS8FastChat
DefaultGroupName=JS8FastChat
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=JS8FastChat_v1.75_Setup
SetupIconFile=JS8FastChat_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Admin, to match JS8Map: {autopf} resolves to Program Files, which is not
; writable by a standard user. Nothing else here needs elevation.
PrivilegesRequired=admin
DisableDirPage=no
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\JS8FastChat.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; No Flags here, and that is correct: Inno Setup ticks [Tasks] entries BY
; DEFAULT. "checked" is not a real flag -- it aborts the compile with "unknown
; flag", which is how this was caught. The flag that exists is "unchecked",
; which turns a task OFF by default. JS8Map.iss also omits flags, so its
; shortcuts were ticked all along; whatever caused a missing desktop icon on a
; test install, it was NOT an unticked box here.
Name: "desktopicon";   Description: "Create a &desktop shortcut";    GroupDescription: "Additional icons:"
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\JS8FastChat.exe";   DestDir: "{app}"; Flags: ignoreversion
Source: "JS8FastChat_icon.ico";   DestDir: "{app}"; Flags: ignoreversion

[Registry]
; --- HIGH-DPI SCALING ------------------------------------------------------
; Tells Windows to scale this program itself instead of letting the program do
; it. HIGHDPIAWARE is the value Windows itself writes for this -- DPIUNAWARE was
; tried first and did NOT work: it applied cleanly, showed as ticked in the
; Properties dialog, and the button was still clipped. Verified by reading back
; what Windows had recorded after setting it by hand. Do not swap it back.
; Same as ticking, by hand:
;     right-click JS8FastChat.exe > Properties > Compatibility
;     > Change high DPI settings > Override high DPI scaling behavior
;     > Scaling performed by: System
;
; WHY: on a display set to 125% or 150% -- normal on a laptop -- Windows hands
; a DPI-aware program raw pixels and expects it to scale itself. Tk grows the
; fonts but a PNG icon keeps its pixel size, so the JS8Map button in the header
; came out chopped: the label read "JS8N" and the icon was a sliver. This was
; reproduced on a 13" laptop at 125% and traced here; the app's own code was
; NOT at fault and was changed three times before this was found.
;
; TRADE-OFF: the whole window renders slightly smaller and text is a little
; softer on high-DPI screens. Operators can raise FastChat's own UI Scale to
; suit. That is the accepted cost -- a clipped button on first launch is worse.
;
; WHY TWO LINES, AND WHY HKLM64:
; Inno builds a 32-bit installer. On 64-bit Windows a 32-bit program writing to
; HKLM\SOFTWARE is silently redirected into ...\WOW6432Node, and Windows does
; NOT read scaling layers from there -- the value gets written and does nothing.
; A plain "Root: HKLM" line was tried first and had exactly that result: the
; button was still clipped and the Compatibility tab still showed unticked.
; HKLM64 forces the real 64-bit view. The HKCU line is a belt-and-braces copy
; for the installing user, which applies even where the machine-wide write is
; blocked by policy. HKCU\SOFTWARE is not subject to the same redirection.
;
; Both are removed cleanly on uninstall (uninsdeletevalue).
Root: HKLM64; Subkey: "SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"; ValueType: string; ValueName: "{app}\JS8FastChat.exe"; ValueData: "~ HIGHDPIAWARE"; Flags: uninsdeletevalue; Check: IsWin64
Root: HKCU;   Subkey: "SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"; ValueType: string; ValueName: "{app}\JS8FastChat.exe"; ValueData: "~ HIGHDPIAWARE"; Flags: uninsdeletevalue

[Icons]
Name: "{group}\JS8FastChat 1.75";   Filename: "{app}\JS8FastChat.exe"; IconFilename: "{app}\JS8FastChat_icon.ico"; Tasks: startmenuicon
Name: "{autodesktop}\JS8FastChat";  Filename: "{app}\JS8FastChat.exe"; IconFilename: "{app}\JS8FastChat_icon.ico"; Tasks: desktopicon
Name: "{group}\Uninstall JS8FastChat"; Filename: "{uninstallexe}"; Tasks: startmenuicon

[Code]
// --- BRING THE SETUP WINDOW TO THE FRONT ----------------------------------
// Setup could open BEHIND whatever was already on screen and sit unnoticed in
// the taskbar. It did this on one PC and not another with identical files, so
// it is Windows foreground behaviour rather than anything in this script.
//
// WHAT WAS TRIED AND FAILED, on a PC where setup always opened behind:
//   1. BringToFrontAndRestore    -- no effect
//   2. SetForegroundWindow       -- no effect
//   3. ForegroundLockTimeout = 0 -- no effect, even after signing out
// Those all ASK Windows for focus, and Windows is entitled to refuse.
//
// SetWindowPos with HWND_TOPMOST does not ask for focus at all: it just puts
// the window above the others. That is why it works where the above did not.
// The window will therefore float over anything the user switches to while
// setup is running. Accepted deliberately -- the install is quick, and a
// hidden setup window gets run twice by people who think nothing happened.
//
// SWP_NOMOVE + SWP_NOSIZE = change the z-order only, leave position and size
// alone. SWP_NOACTIVATE = do not fight Windows over focus while doing it.
const
  HWND_TOPMOST   = -1;
  SWP_NOSIZE     = $0001;
  SWP_NOMOVE     = $0002;
  SWP_NOACTIVATE = $0010;

function SetWindowPos(hWnd: HWND; hWndInsertAfter: HWND; X, Y, cx, cy: Integer;
  uFlags: UINT): Boolean; external 'SetWindowPos@user32.dll stdcall';

function SetForegroundWindow(hWnd: HWND): Boolean;
  external 'SetForegroundWindow@user32.dll stdcall';

procedure RaiseSetupWindow();
begin
  try
    BringToFrontAndRestore();
    SetForegroundWindow(WizardForm.Handle);
    SetWindowPos(WizardForm.Handle, HWND_TOPMOST, 0, 0, 0, 0,
                 SWP_NOMOVE or SWP_NOSIZE or SWP_NOACTIVATE);
  except
    // Never let a cosmetic window nudge break an install.
  end;
end;

procedure InitializeWizard();
begin
  RaiseSetupWindow();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  // Re-assert on every page. Cheap, and it survives anything that pushes the
  // window down the z-order mid-install.
  RaiseSetupWindow();
end;

[Messages]
WelcomeLabel1=Welcome to JS8FastChat 1.75 Setup
WelcomeLabel2=JS8FastChat is the operator console that works alongside JS8Map.%n%nIt handles directed messages, queries and group traffic, and shares the callsign and activity data that JS8Map collects.%n%nINSTALL JS8MAP FIRST. JS8FastChat reads the databases JS8Map downloads, and has nothing to show without them.%n%nClick Next to continue.
FinishedHeadingLabel=JS8FastChat 1.75 Installation Complete
FinishedLabel=JS8FastChat 1.75 has been installed successfully!%n%nBefore launching:%n%n  1. Install and run JS8Map, and let its setup download the callsign databases%n  2. Start JS8Call and enable the TCP API (port 2442)%n  3. Launch JS8FastChat from your desktop shortcut%n%nJS8Map and JS8FastChat each run in their own window. Neither one starts the other, but each has a button to bring the other to the front.%n%n73 de KW3KW
