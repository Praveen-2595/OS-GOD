import subprocess
import os
from loguru import logger

# ─────────────────────────────────────────────────────────────────────────────
# Auto-detect Wallpaper Engine path
# Checks both default Steam locations (Program Files x86 and x64)
# User can also override by setting the WE_EXE_OVERRIDE variable below.
# ─────────────────────────────────────────────────────────────────────────────

WE_EXE_OVERRIDE: str | None = None   # set this if your Steam is in a custom path
                                      # e.g. WE_EXE_OVERRIDE = r"D:\Steam\...\wallpaper64.exe"

_WE_CANDIDATES = [
    r"C:\Program Files (x86)\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe",
    r"C:\Program Files\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\wallpaper_engine\wallpaper32.exe",
    r"C:\Program Files\Steam\steamapps\common\wallpaper_engine\wallpaper32.exe",
]

_WE_WORKSHOP_CANDIDATES = [
    r"C:\Program Files (x86)\Steam\steamapps\workshop\content\431960",
    r"C:\Program Files\Steam\steamapps\workshop\content\431960",
]


def _find_we_exe() -> str | None:
    if WE_EXE_OVERRIDE and os.path.exists(WE_EXE_OVERRIDE):
        return WE_EXE_OVERRIDE
    for path in _WE_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _find_workshop_base() -> str | None:
    for path in _WE_WORKSHOP_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


WE_EXE      = _find_we_exe()
WE_WORKSHOP = _find_workshop_base()

if WE_EXE:
    logger.info(f"Wallpaper Engine found: {WE_EXE}")
else:
    logger.warning(
        "Wallpaper Engine not found in default paths.\n"
        "  → Set WE_EXE_OVERRIDE in utils/wallpaper.py if Steam is on another drive."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _wallpaper_path(workshop_id: str) -> str | None:
    """
    Return full path to scene.pkg or project.json for a Workshop ID.
    Returns None if wallpaper folder not found (not subscribed / not downloaded).
    """
    if not WE_WORKSHOP:
        logger.error("Steam Workshop folder not found — cannot locate wallpaper.")
        return None

    folder = os.path.join(WE_WORKSHOP, str(workshop_id))

    if not os.path.isdir(folder):
        logger.warning(
            f"Wallpaper folder missing: {folder}\n"
            f"  → Subscribe to Workshop ID {workshop_id} in Wallpaper Engine."
        )
        return None

    for filename in ("scene.pkg", "project.json"):
        full = os.path.join(folder, filename)
        if os.path.exists(full):
            return full

    logger.warning(
        f"Wallpaper {workshop_id} folder exists but scene.pkg/project.json not found.\n"
        f"  → Try re-subscribing to the wallpaper in Steam Workshop."
    )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def set_wallpaper(workshop_id: str, monitor: int = 0) -> bool:
    """
    Switch Wallpaper Engine to a specific Workshop wallpaper.

    workshop_id : Steam Workshop item ID (the number from the Workshop URL)
    monitor     : 0 = first monitor (default), 1 = second, etc.

    Returns True on success, False on any failure.
    Wallpaper Engine must already be running for this to work.
    """
    if not WE_EXE:
        print(
            "⚠️  Wallpaper Engine not found — wallpaper will not change.\n"
            "   Set WE_EXE_OVERRIDE in utils/wallpaper.py to your wallpaper64.exe path."
        )
        return False

    wp_file = _wallpaper_path(workshop_id)
    if not wp_file:
        print(
            f"⚠️  Wallpaper ID {workshop_id} not found locally.\n"
            f"   Make sure you subscribed to it and Wallpaper Engine has downloaded it."
        )
        return False

    try:
        subprocess.Popen(
            [WE_EXE,
             "-control", "openWallpaper",
             "-file",    wp_file,
             "-monitor", str(monitor)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"Wallpaper changed → Workshop ID {workshop_id} (monitor {monitor})")
        return True

    except FileNotFoundError:
        logger.error(f"Wallpaper Engine exe not accessible: {WE_EXE}")
        print(f"❌ Could not launch Wallpaper Engine: {WE_EXE}")
        return False

    except Exception as e:
        logger.error(f"Wallpaper change failed for ID {workshop_id}: {e}")
        print(f"⚠️  Wallpaper error: {e}")
        return False
