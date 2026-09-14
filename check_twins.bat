@echo off
REM ============================================================================
REM  check_twins.bat  --  #35 build gate.  Lives at the PROJECT ROOT.
REM  JS8MapChat 1.75
REM ============================================================================
REM  The shared-folder resolver (shared_resolver.py) ships as TWO byte-identical
REM  copies, one in each app tree:
REM      JS8Map Ver 1.75\shared_resolver.py
REM      JS8FastChat Ver 1.75\js8fastchat\shared_resolver.py
REM
REM  If they ever DIVERGE, the two shipped exes carry different resolvers and
REM  ticket #35 is silently back: they agree on this machine and split on a
REM  stranger's.  This gate makes that impossible to SHIP -- BOTH build scripts
REM  call it before PyInstaller runs, and a mismatch (or a missing file) ABORTS
REM  the build.
REM
REM  Called as:   call "<path-to-root>\check_twins.bat"
REM  Returns:     0 = twins identical (build may proceed)
REM               1 = missing file OR mismatch (build MUST abort)
REM
REM  It computes its own location (%~dp0), so it does not matter who calls it or
REM  from what directory.  Both copies are located RELATIVE TO THIS FILE.
REM ============================================================================

setlocal enabledelayedexpansion

set "ROOT=%~dp0"
set "MAP_COPY=%ROOT%JS8Map Ver 1.75\shared_resolver.py"
set "FC_COPY=%ROOT%JS8FastChat Ver 1.75\js8fastchat\shared_resolver.py"

echo.
echo   [#35 gate] Verifying the two shared_resolver.py copies are identical ...

REM -- 1) BOTH must exist.  A missing file ABORTS -- never read absence as OK. --
if not exist "%MAP_COPY%" (
    echo   [#35 gate] ^>^> ABORT: JS8Map copy NOT FOUND:
    echo               %MAP_COPY%
    endlocal & exit /b 1
)
if not exist "%FC_COPY%" (
    echo   [#35 gate] ^>^> ABORT: FastChat copy NOT FOUND:
    echo               %FC_COPY%
    endlocal & exit /b 1
)

REM -- 2) Hash both with certutil, grab the hash line (2nd line of output). -----
set "MAP_HASH="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%MAP_COPY%" MD5') do (
    if not defined MAP_HASH set "MAP_HASH=%%H"
)
set "FC_HASH="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%FC_COPY%" MD5') do (
    if not defined FC_HASH set "FC_HASH=%%H"
)

REM -- Guard: if EITHER hash came back empty, certutil failed -- ABORT loud. ----
if not defined MAP_HASH (
    echo   [#35 gate] ^>^> ABORT: could not hash the JS8Map copy.
    endlocal & exit /b 1
)
if not defined FC_HASH (
    echo   [#35 gate] ^>^> ABORT: could not hash the FastChat copy.
    endlocal & exit /b 1
)

REM -- 3) Compare. --------------------------------------------------------------
if /i "%MAP_HASH%"=="%FC_HASH%" (
    echo   [#35 gate] OK -- twins identical.  MD5: %MAP_HASH%
    endlocal & exit /b 0
) else (
    echo.
    echo   [#35 gate] ============================================================
    echo   [#35 gate]  ^>^> ABORT: the two shared_resolver.py copies DIVERGED.
    echo   [#35 gate] ============================================================
    echo   [#35 gate]  JS8Map  : %MAP_HASH%
    echo   [#35 gate]  FastChat: %FC_HASH%
    echo   [#35 gate]
    echo   [#35 gate]  You edited one copy and not the other.  Ticket #35 is back.
    echo   [#35 gate]  Reconcile them ^(make them identical^) before building.
    echo   [#35 gate]  Both live at:
    echo   [#35 gate]    %MAP_COPY%
    echo   [#35 gate]    %FC_COPY%
    echo.
    endlocal & exit /b 1
)
