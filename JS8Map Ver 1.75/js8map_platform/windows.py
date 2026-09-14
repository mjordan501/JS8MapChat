"""Windows browser and map-window integration for JS8Map."""

import ctypes
import os
import subprocess
from ctypes import wintypes
from typing import Optional


def _find_app_mode_browser() -> Optional[str]:
    """Return the preferred installed Chromium browser executable, if any."""
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get(
        "PROGRAMFILES(X86)", r"C:\Program Files (x86)"
    )
    local_app_data = os.environ.get("LOCALAPPDATA", "")

    relative_paths = [
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
        r"Google\Chrome\Application\chrome.exe",
        r"Microsoft\Edge\Application\msedge.exe",
    ]
    roots = [program_files, program_files_x86]
    if local_app_data:
        roots.append(local_app_data)

    for relative_path in relative_paths:
        for root in roots:
            browser_path = os.path.join(root, relative_path)
            if os.path.isfile(browser_path):
                return browser_path
    return None


def focus_existing_map_window() -> bool:
    """Bring an existing dedicated Chromium JS8Map window to the foreground."""
    try:
        user32 = ctypes.windll.user32
        found = []
        enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )

        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True

            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if class_name.value != "Chrome_WidgetWin_1":
                return True

            title_length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, title, title_length + 1)

            # Dedicated --app windows show only the page title. The map page
            # title ends with "JS8Map", unlike normal browser windows.
            if title.value.strip().endswith("JS8Map"):
                found.append(hwnd)
            return True

        user32.EnumWindows(enum_proc(_callback), 0)
        if not found:
            return False

        hwnd = found[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE

        swp_no_size_or_move = 0x0001 | 0x0002
        user32.SetWindowPos(
            hwnd, -1, 0, 0, 0, 0, swp_no_size_or_move
        )  # HWND_TOPMOST
        user32.SetWindowPos(
            hwnd, -2, 0, 0, 0, 0, swp_no_size_or_move
        )  # HWND_NOTOPMOST
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def launch_map_window(url: str, data_dir: str) -> bool:
    """Launch the map in an isolated Chromium --app window."""
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
        create_no_window = 0x08000000
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=create_no_window,
        )
        return True
    except Exception:
        return False
