"""Platform-specific browser and window integration for JS8Map."""

import sys

if sys.platform.startswith("win"):
    from .windows import focus_existing_map_window, launch_map_window
else:
    from .linux import focus_existing_map_window, launch_map_window

__all__ = ["focus_existing_map_window", "launch_map_window"]
