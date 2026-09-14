"""Non-Windows browser and window integration for JS8Map.

launch_map_window() now mirrors the Windows behaviour: the map opens in a
dedicated Chromium app-mode window (no tabs, no address bar) using its own
isolated profile, so it looks and behaves the same on both machines.

focus_existing_map_window() raises an already-open dedicated map window when
an external helper is available, so pressing an app-switch button does not
stack a second window. wmctrl is preferred, xdotool is the fallback; when
NEITHER is installed the function returns False and the previous behaviour
(open another window) is unchanged. Raising is also commonly blocked under
Wayland, which is why absence of a helper is a supported state and not an
error. Verified on Mint 22.2 / X11 / Cinnamon with wmctrl 1.07.

Every failure path returns False, which preserves the existing default-browser
fallback in js8map_web.py. Nothing here can prevent the map from opening.
"""

import os
import shutil
import subprocess
from typing import Optional

# Preference order mirrors windows.py: Brave, then Chrome, then Edge, with the
# distro-packaged Chromium names appended -- Debian/Ubuntu ship "chromium",
# older releases and some derivatives use "chromium-browser".
_BROWSER_CANDIDATES = (
    "brave-browser",
    "brave",
    "google-chrome-stable",
    "google-chrome",
    "microsoft-edge-stable",
    "microsoft-edge",
    "chromium",
    "chromium-browser",
)


def _find_app_mode_browser() -> Optional[str]:
    """Return the first Chromium-family browser found on PATH, if any."""
    for name in _BROWSER_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


# The Chromium --app window carries a WM_CLASS built from the URL's host and
# PATH but NOT its port -- e.g. "127.0.0.1__ham_map.html.Brave-browser" -- so
# this fragment is stable across sessions, across JS8Map's random HTTP port,
# and across every Chromium-family browser in _BROWSER_CANDIDATES. Confirmed
# by inspection of a live window list on Mint 22.2.
_MAP_WINDOW_CLASS_FRAGMENT = "ham_map.html"

_HELPER_TIMEOUT = 3  # seconds; a hung helper must never stall the map opening


def _raise_with_wmctrl(exe: str) -> bool:
    """Raise the map window using wmctrl. False if not found or not raised."""
    listing = subprocess.run(
        [exe, "-lx"], capture_output=True, text=True, timeout=_HELPER_TIMEOUT
    )
    if listing.returncode != 0:
        return False
    for line in listing.stdout.splitlines():
        # "0x04200004  0 127.0.0.1__ham_map.html.Brave-browser  host  Title"
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        if _MAP_WINDOW_CLASS_FRAGMENT in parts[2].lower():
            raised = subprocess.run(
                [exe, "-i", "-a", parts[0]],
                capture_output=True,
                timeout=_HELPER_TIMEOUT,
            )
            return raised.returncode == 0
    return False


def _raise_with_xdotool(exe: str) -> bool:
    """Raise the map window using xdotool. False if not found or not raised."""
    found = subprocess.run(
        [exe, "search", "--class", _MAP_WINDOW_CLASS_FRAGMENT],
        capture_output=True,
        text=True,
        timeout=_HELPER_TIMEOUT,
    )
    ids = [w for w in found.stdout.split() if w.strip()]
    if found.returncode != 0 or not ids:
        return False
    raised = subprocess.run(
        [exe, "windowactivate", ids[-1]],
        capture_output=True,
        timeout=_HELPER_TIMEOUT,
    )
    return raised.returncode == 0


def focus_existing_map_window() -> bool:
    """Raise an existing dedicated map window. False if that was not possible.

    Returns True ONLY when a window was found and the raise command succeeded.
    A missing helper, no matching window, a non-zero exit or a timeout all
    return False, which preserves the caller's launch/fallback path in
    js8map_web.py. Nothing here can prevent the map from opening.
    """
    for name, raiser in (("wmctrl", _raise_with_wmctrl), ("xdotool", _raise_with_xdotool)):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            if raiser(exe):
                return True
        except Exception:
            pass  # try the next helper, then give up quietly
    return False


def launch_map_window(url: str, data_dir: str) -> bool:
    """Launch the map in an isolated Chromium --app window. False if unavailable."""
    browser = _find_app_mode_browser()
    if not browser:
        return False

    try:
        profile = os.path.join(data_dir, "map_window_profile")
        args = [
            browser,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-service-autorun",
            "--start-maximized",
            f"--app={url}",
        ]
        # start_new_session detaches the browser into its own process group.
        # JS8Map is normally started FROM A TERMINAL on Linux; without this a
        # Ctrl-C (or closing that terminal) would take the map window down too.
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False
