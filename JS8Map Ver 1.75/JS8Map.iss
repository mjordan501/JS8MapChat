; JS8MapChat 1.75 - Inno Setup Installer Script
; 73 de KW3KW

[Setup]
; --- APPID -- ADDED 2026-08-28. NEVER CHANGE THIS VALUE. -------------------
; Without an AppId, Inno identifies the product by AppName + AppVersion. That
; is why installs drifted: a new version registered as a SEPARATE product, so
; the old one fell out of Add/Remove Programs while its files stayed on disk,
; and setup could not remember where the previous install went. Three symptoms
; of this were seen on the test PC 2026-08-27: a June install invisible to
; Add/Remove, a doubled C:\JS8Map\JS8Map path, and setup offering C:\JS8Map
; when DefaultDirName says {autopf}\JS8Map.
;
; The GUID is this product's permanent identity across every future version.
; If it is ever edited, every machine with JS8Map installed sees the next
; build as a different product and the drift starts over -- with the added
; problem that the old install can no longer be found or removed by the new
; one. Change AppVersion freely. Do not touch this line.
;
; JS8FastChat.iss has no AppId either (checked 2026-08-28). When one is added
; there it MUST be a different GUID -- two products sharing an AppId would
; each uninstall the other.
;
; The brace style is not a typo and is easy to get wrong. Inno treats '{' as
; the start of a constant, so a literal one is written '{{'. It does NOT treat
; '}' as special, so the closing brace is written ONCE. Doubled braces at both
; ends produce a GUID ending in '}}' -- which still works, but writes a
; malformed registry key. That mistake was made and caught on 2026-08-28
; before this value reached any operator machine.
AppId={{8F3A6C21-5E4B-4D79-9A0C-B7E2D14F6835}
AppName=JS8Map
AppVersion=1.75
AppVerName=JS8MapChat 1.75
AppPublisher=KW3KW - Ham Made Simple
DefaultDirName={autopf}\JS8Map
DefaultGroupName=JS8Map
AllowNoIcons=yes
OutputDir=installer_output
; Renamed from JS8MapChat_v1.75_Setup 2026-08-25. This installer ships ONE
; program -- dist\JS8Map.exe -- plus build_canadian_db.py. FastChat was never
; in it and now has its own installer (JS8FastChat.iss). The old name promised
; two programs and delivered one, and sent operators hunting for a FastChat
; that no installer had ever put on their machine.
OutputBaseFilename=JS8Map_v1.75_Setup
SetupIconFile=JS8Map_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
DisableDirPage=no
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\JS8Map.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\JS8Map.exe";   DestDir: "{app}"; Flags: ignoreversion
Source: "JS8Map_icon.ico";   DestDir: "{app}"; Flags: ignoreversion
; SECURITY 2026-07-19 (Bug Testing Phase 1) -- was:
;   Source: "build_canadian_db.py"; DestDir: "C:\FCC_DB"; Flags: ignoreversion
; A .py file inside a world-writable folder is arbitrary code any local user can
; REPLACE. Whoever runs it next -- typically an admin, following this installer's
; own instructions -- executes their code. Moved to {app} (Program Files, admin-
; only write). The DB folder stays writable; the SCRIPT no longer lives in it.
; UNPROVEN: whether anything reads build_canadian_db.py FROM C:\FCC_DB by path.
; Checked the .iss only -- JS8Map.py and FCC Lookup were NOT read. If something
; does, it breaks visibly (file not found), not silently. Verify before shipping.
; RESOLVED 2026-07-20 (Bug Testing Phase 3): JS8Map.py HAS now been read. It did
; read the script by path -- update_canadian_db() joined it onto the DATA folder,
; so on an installed machine it failed with "build_canadian_db.py not found",
; exactly the visible break predicted above. Fixed on the JS8Map.py side via
; _find_canadian_builder(), which looks in _EXE_DIR ({app}) first and falls back
; to the data folder for existing installs. This line stays put -- do NOT revert
; it. Still UNPROVEN: the separate FCC Lookup tool was not reviewed.
Source: "build_canadian_db.py"; DestDir: "{app}"; Flags: ignoreversion

; ── OFFLINE MAP DATA 2026-08-26 ──────────────────────────────────────────
; JS8Map is an offline tool, but its map page was reaching out to the internet
; three separate ways on every open: the Leaflet library from a CDN, the world
; borders and US state lines from two files on GitHub, and the tiles from CARTO.
; With no internet the library never arrived, so nothing ran at all -- the map
; came up as a blank page, not a degraded one.
;
; These five files fix that. leaflet.js/.css are the same 1.9.4 the page always
; used. The three .json files are world country outlines, state/province lines,
; and country/state/city names -- open data, ~1.8 MB total.
;
; They are installed BESIDE the exe rather than packed INTO it on purpose: a
; one-file PyInstaller exe unpacks its whole payload to temp on every launch,
; and first launch already takes ~30 seconds. js8map_web.py looks in the exe's
; own folder for these, so no .spec change is needed.
Source: "leaflet.js";            DestDir: "{app}"; Flags: ignoreversion
Source: "leaflet.css";           DestDir: "{app}"; Flags: ignoreversion
Source: "js8map_borders.json";   DestDir: "{app}"; Flags: ignoreversion
Source: "js8map_admin1.json";    DestDir: "{app}"; Flags: ignoreversion
Source: "js8map_places.json";    DestDir: "{app}"; Flags: ignoreversion
Source: "js8map_lakes.json";     DestDir: "{app}"; Flags: ignoreversion

[Registry]
; --- HIGH-DPI SCALING ------------------------------------------------------
; Same entry JS8FastChat uses, added to JS8Map 2026-08-27 and verified by hand
; on the 13" test laptop at 125% before being written here.
;
; JS8Map previously needed no DPI setting -- its window measures itself and
; grows to fit its content, which is why it survived the scaling bug that hit
; FastChat. But the settings card is a fixed stack of rows with no scrolling,
; and once the content grew taller than the screen the window could no longer
; be dragged up to reach its own bottom. Letting Windows do the scaling brings
; the whole window back within the display.
;
; HIGHDPIAWARE is the value Windows itself writes for this. DPIUNAWARE was
; tried on FastChat first and did NOT work: it applied cleanly, showed as
; ticked in the Properties dialog, and changed nothing. Do not swap it back.
;
; Same as ticking, by hand:
;     right-click JS8Map.exe > Properties > Compatibility
;     > Change high DPI settings > Override high DPI scaling behavior
;     > Scaling performed by: System
;
; HKLM64 is required. A 32-bit installer writing plain HKLM is redirected to
; Wow6432Node, where Windows never looks for this. HKCU is written too, as a
; fallback for machines where the HKLM write is blocked by policy.
;
; Both are removed cleanly on uninstall (uninsdeletevalue).
Root: HKLM64; Subkey: "SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"; ValueType: string; ValueName: "{app}\JS8Map.exe"; ValueData: "~ HIGHDPIAWARE"; Flags: uninsdeletevalue; Check: IsWin64
Root: HKCU;   Subkey: "SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"; ValueType: string; ValueName: "{app}\JS8Map.exe"; ValueData: "~ HIGHDPIAWARE"; Flags: uninsdeletevalue

[Dirs]
; SECURITY 2026-07-19 -- was:  Permissions: everyone-full
; "everyone" includes Guest and anonymous logons; "full" also grants the right to
; change permissions and take ownership. "users-modify" keeps normal accounts able
; to read/write/delete the callsign databases -- which the FCC Lookup workflow
; needs -- without handing out ownership of a folder at the drive root.
; STILL NOT IDEAL: writable data at C:\ root at all is why this installer needs
; admin. The real fix is {localappdata}\JS8MapChat\FCC_DB, which requires editing
; JS8Map.py and the FCC Lookup tool in the same breath. NOT done here on purpose.
Name: "C:\FCC_DB"; Permissions: users-modify

[Icons]
Name: "{group}\JS8MapChat 1.75"; Filename: "{app}\JS8Map.exe"; IconFilename: "{app}\JS8Map_icon.ico"; Tasks: startmenuicon
Name: "{autodesktop}\JS8Map"; Filename: "{app}\JS8Map.exe"; IconFilename: "{app}\JS8Map_icon.ico"; Tasks: desktopicon
Name: "{group}\Uninstall JS8Map"; Filename: "{uninstallexe}"; Tasks: startmenuicon

[Code]
// --- BRING THE SETUP WINDOW TO THE FRONT ----------------------------------
// Copied verbatim from JS8FastChat.iss, where this was worked out and proven
// last session. JS8Map's installer never received it -- this file had no
// [Code] section at all -- so JS8Map setup still opened behind other windows
// and sat unnoticed in the taskbar. Seen again on the test PC 2026-08-27.
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
//
// NOTE: comments in an .iss [Code] block are Pascal -- '//', never ';'.
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
WelcomeLabel1=Welcome to JS8Map 1.75 Setup
WelcomeLabel2=JS8Map is a live map display for JS8Call.%n%nIt connects to JS8Call's TCP API, plots stations on an interactive map, and resolves callsigns using the FCC and Canadian license databases.%n%nThe first time you run JS8Map it will offer to download those databases for you. The US download takes several minutes; the Canadian one is optional and much quicker.%n%nClick Next to continue.
FinishedHeadingLabel=JS8Map 1.75 Installation Complete
FinishedLabel=JS8Map 1.75 has been installed successfully!%n%nBefore launching:%n%n  1. Start JS8Call and enable the TCP API (port 2442)%n  2. Launch JS8Map from your desktop shortcut%n  3. Follow the first-time setup to download the callsign databases%n%nClick Generate Map and the map opens in your browser.%n%nJS8FastChat, the operator console, has its own separate installer if you want it.%n%n73 de KW3KW
