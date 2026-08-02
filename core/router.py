import os
import sys
import re
import time
import string
import difflib
import threading
import subprocess
import webbrowser
import yaml
import psutil
from loguru import logger

from utils.wallpaper import set_wallpaper
from utils.feedback  import speak, beep, play_sound, play_sound_blocking, fade_out_sound, set_volume, get_volume
from utils.effects   import show_mode_popup, show_dj_pawnin_popup, dim_brightness, restore_brightness
from utils.hotkeys   import start_dj_hotkeys, stop_dj_hotkeys
from utils.server    import update_current_mode
from utils.timer     import on_mode_start, cancel_mode_timer, start_mode_timer, print_history

from sl_bridge import bridge
# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
def _resource(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, relative)


try:
    with open(_resource("config.yaml"), "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    logger.error("config.yaml not found.")
    config = {"modes": {}, "defaults": {}}

_DEFAULTS: dict = config.get("defaults", {}) or {}


# ─────────────────────────────────────────────────────────────────────────────
# App registry
# ─────────────────────────────────────────────────────────────────────────────
_USER = os.environ.get("USERNAME", "user")

APP_PATHS: dict[str, str] = {
    "whatsapp":      rf"C:\Users\{_USER}\AppData\Local\WhatsApp\WhatsApp.exe",
    "opera gx":      rf"C:\Users\{_USER}\AppData\Local\Programs\Opera GX\opera.exe",
    "notion":        rf"C:\Users\{_USER}\AppData\Local\Programs\Notion\Notion.exe",
    "after effects": rf"C:\Program Files\Adobe\Adobe After Effects 2020\Support Files\AfterFX.exe",
    "discord":       rf"C:\Users\{_USER}\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Discord Inc\Discord.lnk",
    "vs code":       rf"C:\Users\{_USER}\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "notepad":       rf"C:\Windows\System32\notepad.exe",
    "system":        rf"C:\Users\{_USER}\questapp\questapp\launch_quest.py",
    "pomo":          rf"C:\Users\{_USER}\Downloads\pomodoro-timer\pomodoro\launch_pomodoro.py",
}

APP_EXE: dict[str, str] = {
    "whatsapp":       "WhatsApp.exe",
    "opera gx":       "opera.exe",
    "opera":          "opera.exe",
    "notion":         "Notion.exe",
    "after effects":  "AfterFX.exe",
    "discord":        "discord.exe",
    "vs code":        "Code.exe",
    "code":           "Code.exe",
    "notepad":        "notepad.exe",
    "claude":         "claude.exe",
    "claude ai":      "claude.exe",
    "spotify":        "Spotify.exe",
    "telegram":       "Telegram.exe",
    "file explorer":  "explorer.exe",
    "explorer":       "explorer.exe",
    "pomo":          "pythonw.exe",
    "system":        "pythonw.exe"
}

# ── App auto-discovery ────────────────────────────────────────────────────────
# In-memory cache: requested name → resolved path. Reset on restart.
_app_cache: dict[str, str] = {}

# Lazy-built index of all discoverable apps: stem_lower → path
_app_index: dict[str, str] | None = None

_START_MENU_DIRS = [
    r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
    rf"C:\Users\{_USER}\AppData\Roaming\Microsoft\Windows\Start Menu\Programs",
]
_INSTALL_DIRS = [
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    rf"C:\Users\{_USER}\AppData\Local\Programs",
]
# Stems containing these substrings are noise (uninstallers, setup helpers, etc.)
_EXCLUDE_SUBSTRINGS = ("uninstall", "setup", "update", "installer", "unins", "helper")


def _is_noise(stem: str) -> bool:
    s = stem.lower()
    return any(x in s for x in _EXCLUDE_SUBSTRINGS)


def _build_app_index() -> dict[str, str]:
    """One-time scan of Start Menu and common install dirs → stem→path index."""
    idx: dict[str, str] = {}

    # Start Menu — .lnk and .exe files, all subdirs
    for base in _START_MENU_DIRS:
        for root, _, files in os.walk(base):
            for fname in files:
                if not fname.lower().endswith((".lnk", ".exe")):
                    continue
                stem = os.path.splitext(fname)[0].lower()
                if _is_noise(stem):
                    continue
                idx.setdefault(stem, os.path.join(root, fname))

    # Common install dirs — one level deep only (avoid full recursive scan)
    for base in _INSTALL_DIRS:
        try:
            for entry in os.scandir(base):
                if not entry.is_dir():
                    continue
                folder_stem = entry.name.lower()
                try:
                    exes = [f for f in os.listdir(entry.path)
                            if f.lower().endswith(".exe") and not _is_noise(os.path.splitext(f)[0])]
                except (PermissionError, OSError):
                    continue
                if not exes:
                    continue
                # Prefer an exe whose stem matches the folder name; else take first
                preferred = next(
                    (e for e in exes if os.path.splitext(e)[0].lower() == folder_stem),
                    exes[0],
                )
                exe_path = os.path.join(entry.path, preferred)
                idx.setdefault(folder_stem, exe_path)
                idx.setdefault(os.path.splitext(preferred)[0].lower(), exe_path)
        except (PermissionError, OSError):
            continue

    logger.debug(f"App index built: {len(idx)} entries.")
    return idx


def _registry_lookup(name: str) -> str | None:
    """Check HKLM and HKCU App Paths registry for a matching executable."""
    try:
        import winreg
    except ImportError:
        return None
    candidates = [name, name + ".exe", name.replace(" ", "") + ".exe"]
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for cand in candidates:
            try:
                key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{cand}"
                with winreg.OpenKey(hive, key_path) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val and os.path.exists(val):
                        return val
            except OSError:
                pass
    return None


def _discover_app(name: str) -> str | None:
    """
    Auto-discover an app by name. Search order:
      1. Session cache (instant)
      2. Start Menu + install dir index — exact stem match
      3. Start Menu + install dir index — fuzzy match (difflib, cutoff 0.62)
      4. Registry App Paths (HKLM + HKCU)
    Successful hits are cached for the rest of the session.
    """
    global _app_index
    key = name.strip().lower()

    if key in _app_cache:
        return _app_cache[key]

    if _app_index is None:
        _app_index = _build_app_index()

    # Exact match
    if key in _app_index:
        path = _app_index[key]
        _app_cache[key] = path
        logger.info(f"App discovered (exact): {name!r} → {path}")
        return path

    # Fuzzy match against the full index
    matches = difflib.get_close_matches(key, _app_index.keys(), n=1, cutoff=0.62)
    if matches:
        path = _app_index[matches[0]]
        _app_cache[key] = path
        logger.info(f"App discovered (fuzzy '{matches[0]}'): {name!r} → {path}")
        return path

    # Registry lookup
    reg = _registry_lookup(key)
    if reg:
        _app_cache[key] = reg
        logger.info(f"App discovered (registry): {name!r} → {reg}")
        return reg

    logger.debug(f"App not discovered: {name!r}")
    return None


MODE_COLORS: dict[str, str] = {
    "zen state":       "#00e5ff",
    "alpha state":     "#ff2200",
    "render state":    "#ffaa00",
    "gaming state":    "#00ff88",
    "dj pawnin state": "#ff006e",
}

APP_OPEN_DELAY     = 2.5
WEBSITE_OPEN_DELAY = 2.0
DEBOUNCE_SECONDS   = 8

# Regex for "for X minutes" timer suffix
_TIMER_RE = re.compile(r"for\s+(\d+)\s*min", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Global state
# ─────────────────────────────────────────────────────────────────────────────
_last_command:     str | None = None
_last_text:        str | None = None
_last_time:        float      = 0.0
_shutdown_pending: bool       = False


# ─────────────────────────────────────────────────────────────────────────────
# Speak async helper
# ─────────────────────────────────────────────────────────────────────────────
def _speak_async(text: str) -> None:
    # speak() already spawns its own daemon thread internally (see feedback.py)
    # so we just call it directly — no need to wrap in another thread.
    speak(text)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _clean(text: str) -> str:
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return " ".join(text.split())


def _extract_after(text: str, *remove: str) -> str:
    text = _clean(text)
    for w in sorted(remove, key=len, reverse=True):
        text = text.replace(w, "")
    return " ".join(text.split())


def _is_running(exe_name: str) -> bool:
    exe_lower = exe_name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == exe_lower:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def _close_file_explorer_windows() -> bool:
    try:
        subprocess.run(
            ["powershell", "-c",
             "(New-Object -ComObject Shell.Application).Windows() | "
             "Where-Object {$_.Name -eq 'File Explorer'} | "
             "ForEach-Object {$_.Quit()}"],
            capture_output=True, text=True,
        )
        return True
    except Exception as e:
        logger.debug(f"Explorer close error: {e}")
        return False


def _find_claude_exe() -> str | None:
    possible = ["claude.exe", "Claude.exe", "AnthropicClaude.exe", "claude-desktop.exe"]
    try:
        running = {p.info["name"].lower() for p in psutil.process_iter(["name"])
                   if p.info.get("name")}
    except Exception:
        return None
    for name in possible:
        if name.lower() in running:
            return name
    return None


def _kill(name: str) -> bool:
    if name.lower() in ("claude", "claude ai"):
        real_exe = _find_claude_exe()
        if not real_exe:
            return False
        exe = real_exe
    else:
        exe = APP_EXE.get(name.lower(), name if name.endswith(".exe") else name + ".exe")

    if not _is_running(exe):
        logger.debug(f"Kill skipped — not running: {exe}")
        return False

    try:
        result = subprocess.run(
            ["taskkill", "/im", exe, "/f", "/t"],
            capture_output=True, text=True,
        )
        success = result.returncode == 0
        if not success:
            logger.warning(f"taskkill failed for {exe}: {result.stderr.strip()}")
        return success
    except Exception as e:
        logger.error(f"Kill error {exe}: {e}")
        return False


def _exes_for_exception(name: str) -> set[str]:
    """Return exe filenames (lowercase) that fuzzy-match the given app name."""
    name_l = name.lower().strip()
    result: set[str] = set()
    for app_key, exe in APP_EXE.items():
        if name_l in app_key.lower() or app_key.lower() in name_l:
            result.add(exe.lower())
    return result


def _kill_all_smart(exceptions: list[str] | None = None) -> None:
    """Close every known running app. Skip any that fuzzy-match `exceptions`."""
    skip_exes: set[str] = set()
    for exc in (exceptions or []):
        skip_exes.update(_exes_for_exception(exc))
    if skip_exes:
        logger.debug(f"kill_all_smart: keeping {skip_exes}")

    closed = []
    for name, exe in APP_EXE.items():
        if exe.lower() == "explorer.exe":
            continue
        if exe.lower() in skip_exes:
            continue
        try:
            if _is_running(exe):
                subprocess.run(["taskkill", "/im", exe, "/f"], capture_output=True)
                closed.append(name)
        except Exception as e:
            logger.debug(f"Kill-all skip {exe}: {e}")

    if _close_file_explorer_windows():
        closed.append("file explorer")

    claude_exe = _find_claude_exe()
    if claude_exe and "claude.exe" not in skip_exes:
        try:
            subprocess.run(["taskkill", "/im", claude_exe, "/f"], capture_output=True)
            if "claude" not in closed:
                closed.append("claude")
        except Exception:
            pass

    closed = list(dict.fromkeys(closed))
    if closed:
        print(f"🧹 Closed: {', '.join(closed)}")
        logger.info(f"Kill-all: {closed}")
    else:
        print("🧹 Nothing was running.")


def _launch_path(path: str) -> None:
    """Execute a resolved path (.py, .lnk, or .exe)."""
    if path.endswith(".py"):
        subprocess.Popen(
            [sys.executable, path],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.startfile(path)


def _launch(name: str) -> None:
    try:
        # 1. Hardcoded registry (config + APP_PATHS)
        path = APP_PATHS.get(name.lower())
        if path and os.path.exists(path):
            _launch_path(path)
            logger.info(f"Launched (config): {name}")
            return

        # 2. Auto-discovery: Start Menu → fuzzy → registry → install dirs
        discovered = _discover_app(name)
        if discovered:
            _launch_path(discovered)
            logger.info(f"Launched (auto-discovered): {name} → {discovered}")
            print(f"  🔍 Found: {discovered}")
            return

        # 3. Last resort: shell "start <name>"
        subprocess.Popen(
            f"start {name}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"Launched via shell: {name}")

    except Exception as e:
        logger.error(f"Launch failed {name}: {e}")
        print(f"⚠️  Could not launch {name}: {e}")


def _open_website(url: str) -> None:
    try:
        webbrowser.open(url)
        logger.info(f"Opened: {url}")
    except Exception as e:
        logger.error(f"Website error {url}: {e}")


def _is_duplicate(command: str, text: str) -> bool:
    global _last_command, _last_text, _last_time
    now = time.time()
    if (
        command == _last_command
        and text == _last_text
        and (now - _last_time) < DEBOUNCE_SECONDS
    ):
        return True
    _last_command, _last_text, _last_time = command, text, now
    return False


def _apply_volume(level_percent) -> None:
    try:
        set_volume(int(level_percent) / 100)
    except Exception as e:
        logger.error(f"Volume apply failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Mode runner
# ─────────────────────────────────────────────────────────────────────────────
def _run_mode(mode_name: str) -> None:
    """
    mode_name must be the exact key from config.yaml e.g. "alpha state".
    This is guaranteed by route_command passing `payload` (the parsed mode key)
    instead of raw spoken text.
    """
    try:
        mode_cfg = config.get("modes", {}).get(mode_name)

        if not mode_cfg:
            print(f"❌ Mode not found: {mode_name!r}")
            logger.error(f"Mode key not in config: {mode_name!r}")
            speak("Mode not found")
            return

        bridge.emit("mode_started", {
            "mode":   mode_name,
            "volume": mode_cfg.get("volume", 50),
            "apps":   mode_cfg.get("apps") or [],
        })

        # ── shutdown ──────────────────────────────────────────────────────────────
        if mode_cfg.get("shutdown"):
            speak("Are you sure? Say confirm to shut down.")
            global _shutdown_pending
            _shutdown_pending = True
            return

        # ── GG / sound-only ───────────────────────────────────────────────────────
        has_apps     = bool(mode_cfg.get("apps"))
        has_websites = bool(mode_cfg.get("websites"))
        if mode_cfg.get("sound") and not has_apps and not has_websites:
            try:
                old_vol = get_volume()
                set_volume(0.8)
                play_sound_blocking(mode_cfg["sound"])
                speak("Good game")
                time.sleep(1)
                set_volume(old_vol)
            except Exception as e:
                logger.error(f"GG mode error: {e}")
            return

        color       = MODE_COLORS.get(mode_name, "#ffffff")
        mode_volume = mode_cfg.get("volume", _DEFAULTS.get("volume", 50))
        sound_file  = mode_cfg.get("sound")
        apps        = mode_cfg.get("apps") or []
        websites    = mode_cfg.get("websites") or []
        close_list  = mode_cfg.get("close") or []

        # ── STEP 1: cancel previous timer + stop DJ hotkeys ───────────────────────
        stop_dj_hotkeys()
        cancel_mode_timer()

        # ── STEP 2: close apps ────────────────────────────────────────────────────
        # FIX: speak fires async (non-blocking) so app killing happens in parallel.
        # We wait 0.4s so the "Clearing system" utterance starts before kill noise.
        speak("Clearing system")
        threading.Thread(target=fade_out_sound, args=(2.0,), daemon=True).start()
        time.sleep(0.4)

        if close_list:
            # Mode has an explicit close list in config — only kill those
            killed = []
            for app_name in close_list:
                try:
                    if _kill(app_name):
                        killed.append(app_name)
                except SystemExit:
                    pass   # never let a kill abort the sequence
                except Exception as e:
                    logger.warning(f"Kill failed for {app_name!r}: {e}")
            if killed:
                print(f"🧹 Closed: {', '.join(killed)}")
                logger.info(f"Closed for mode: {killed}")
            else:
                print("🧹 Nothing to close.")
        else:
            # No close list defined — close everything
            _kill_all_smart()

        time.sleep(0.6)

        # ── STEP 3: dim screen + announce + show popup simultaneously ─────────────
        try:
            old_brightness = dim_brightness(20)
        except Exception:
            old_brightness = None

        speak(f"Activating {mode_name}")
        # Small sleep so TTS utterance is queued before popup animation starts.
        # The _tts_lock in feedback.py queues it behind "Clearing system" so it
        # won't overlap — but we still give it a moment to start.
        time.sleep(0.3)

        try:
            if mode_name == "dj pawnin state":
                show_dj_pawnin_popup(phase="intro")
            else:
                show_mode_popup(mode_name, accent=color)
        except Exception as e:
            logger.error(f"Intro effect error: {e}")

        # ── STEP 4: wallpaper ─────────────────────────────────────────────────────
        wallpaper_id = mode_cfg.get("wallpaper", _DEFAULTS.get("wallpaper"))
        if wallpaper_id:
            try:
                set_wallpaper(wallpaper_id)
            except Exception as e:
                print(f"⚠️  Wallpaper error: {e}")

        # ── STEP 5: set volume + start sound ──────────────────────────────────────
        _apply_volume(mode_volume)
        if sound_file:
            try:
                play_sound(sound_file)
            except Exception as e:
                print(f"⚠️  Sound error: {e}")

        time.sleep(0.5)

        try:
            restore_brightness(old_brightness)
        except Exception:
            pass

        # ── STEP 6: open apps one by one ──────────────────────────────────────────
        for app in apps:
            speak(f"Opening {app}")
            _launch(app)
            print(f"  📂 Opening {app}...")
            time.sleep(APP_OPEN_DELAY)

        # ── STEP 7: open websites one by one ──────────────────────────────────────
        for site in websites:
            site_name = site.replace("https://", "").replace("www.", "").split("/")[0].split(".")[0]
            speak(f"Opening {site_name}")
            _open_website(site)
            print(f"  🌐 Opening {site_name}...")
            time.sleep(WEBSITE_OPEN_DELAY)

        # ── STEP 8: wait for intro sound to finish (with deadline) ────────────────
        if sound_file:
            import utils.feedback as _fb
            proc = _fb._current_process
            if proc is not None:
                print("⏳ Waiting for sound to finish...")
                try:
                    deadline = time.time() + 30
                    while proc.poll() is None and time.time() < deadline:
                        time.sleep(0.5)
                except Exception:
                    pass

        # ── STEP 9: activated confirmation popup + speech ─────────────────────────
        if mode_name == "dj pawnin state":
            speak("DJ Pawnin state activated. Let's go!")
            try:
                show_dj_pawnin_popup(phase="activated")
            except Exception as e:
                logger.error(f"Popup error: {e}")
        else:
            speak(f"{mode_name} activated. Environment ready.")
            try:
                show_mode_popup(mode_name, accent=color)
            except Exception as e:
                logger.error(f"Popup error: {e}")

        time.sleep(1.5)

        # ── STEP 10: restore mode volume ──────────────────────────────────────────
        _apply_volume(mode_volume)

        # ── STEP 11: start DJ hotkeys if DJ Pawnin ────────────────────────────────
        if mode_name == "dj pawnin state":
            tracks = mode_cfg.get("tracks") or []
            if tracks:
                start_dj_hotkeys(tracks)
            else:
                print("⚠️  No tracks in config — add tracks list to dj pawnin state.")

        # ── STEP 12: update server state + log history ────────────────────────────
        update_current_mode(mode_name)
        on_mode_start(mode_name)

        logger.info(f"Mode '{mode_name}' fully loaded.")

        bridge.emit("mode_ready", {
            "mode": mode_name,
        })

    except Exception as e:
        logger.error(f"Mode activation crashed: {e}", exc_info=True)
        speak(f"Mode activation failed: {str(e)[:50]}")


# ─────────────────────────────────────────────────────────────────────────────
# Public router
# ─────────────────────────────────────────────────────────────────────────────
def route_command(command: str | None, payload: str) -> None:
    """
    Parameters
    ----------
    command : str | None
        The command type returned by parse_command e.g. "activate", "open_app".
    payload : str
        The resolved payload from parse_command — for "activate" this is the
        exact mode key from config e.g. "alpha state", NOT the raw spoken text.

    FIX: previously called as route_command(command, raw_text) which passed
    the full raw utterance to _run_mode, causing config lookup to always fail
    and silently returning after speak("Mode not found") — meaning no TTS
    audible output, no app closing, nothing worked.

    Call site must be:
        command, payload = parse_command(raw_text)
        route_command(command, payload)          ← pass payload, not raw_text
    """
    global _shutdown_pending

    if command is None:
        return

    if _is_duplicate(command, payload):
        print("⚠️  Duplicate command — cooldown active.")
        return
    
    bridge.emit("command_recognized", {
        "command": command,
        "value":   payload,
    })

    # ── Shutdown confirmation ─────────────────────────────────────────────────
    if _shutdown_pending:
        if "confirm" in payload:
            speak("Shutting down. Goodbye.")
            try:
                os.system("shutdown /s /t 5")
            except Exception as e:
                logger.error(f"Shutdown error: {e}")
        else:
            speak("Shutdown cancelled.")
            print("🚫 Shutdown cancelled.")
        _shutdown_pending = False
        return

    # ── activate ──────────────────────────────────────────────────────────────
    if command == "activate":
        # payload is the clean mode key e.g. "alpha state"
        # Check for a timer suffix in the original spoken text if needed —
        # but parse_command already strips it, so payload is clean.
        # Timer is extracted from the raw text by the caller if required.
        _run_mode(payload)

    elif command == "activate_timed":
        # Used when caller detected a timer: payload = "mode_key|minutes"
        parts = payload.split("|", 1)
        if len(parts) == 2:
            mode_key, minutes_str = parts
            try:
                minutes = int(minutes_str)
            except ValueError:
                minutes = 0
            _run_mode(mode_key)
            if minutes > 0:
                fallback = "zen state"
                start_mode_timer(minutes, mode_key, fallback, route_command, speak)

                bridge.emit("timer_started", {
                    "mode":     mode_key,
                    "minutes":  minutes,
                    "fallback": fallback,
                })
        else:
            _run_mode(payload)

    elif command == "history":
        print_history()

    elif command == "cancel timer":
        cancel_mode_timer()
        speak("Timer cancelled.")

    elif command == "exit":
        speak("Are you sure? Say confirm to exit.")
        _shutdown_pending = True

    elif command == "open_app":
        app_name = _extract_after(payload, "open", "launch", "start")
        if not app_name:
            speak("Which app?")
            return
        speak(f"Opening {app_name}")
        _launch(app_name)

    elif command == "open_website":
        query = _extract_after(payload, "search", "go to", "open website", "open")
        if not query:
            speak("Where should I go?")
            return
        url = (
            f"https://{query}"
            if ("." in query and " " not in query)
            else f"https://www.google.com/search?q={query.replace(' ', '+')}"
        )
        speak(f"Opening {query}")
        _open_website(url)

    elif command == "close_app":
        app = _extract_after(payload, "close", "kill", "stop")
        if not app:
            speak("Which app?")
            return
        if _kill(app):
            speak(f"Closed {app}")
            print(f"✅ Closed {app}")
        else:
            speak(f"{app} is not running")
            print(f"ℹ️  {app} was not running")

    elif command == "close_all":
        exceptions: list[str] = []
        p = (payload or "").strip()
        if p.lower().startswith("except"):
            after = p[len("except"):].strip()
            exceptions = [x.strip() for x in after.split(",") if x.strip()]
        exc_msg = f" except {', '.join(exceptions)}" if exceptions else ""
        speak(f"Closing everything{exc_msg}")
        _kill_all_smart(exceptions)

    else:
        logger.warning(f"Unknown command: {command!r}")
        print(f"❓ Unknown command: {command!r}")