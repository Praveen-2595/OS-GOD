import yaml
import sys
import os
import string as _string
from rapidfuzz import fuzz, process
from loguru import logger

# ─────────────────────────────────────────────────────────────────────────────
# Resource path (works both as .py and inside a PyInstaller .exe)
# ─────────────────────────────────────────────────────────────────────────────
def _resource(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, relative)


# ─────────────────────────────────────────────────────────────────────────────
# Load config
# ─────────────────────────────────────────────────────────────────────────────
try:
    with open(_resource("config.yaml"), "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)
except FileNotFoundError:
    logger.warning("config.yaml not found in parser — using empty config.")
    _config = {"modes": {}, "trigger_words": ["system", "nyra", "jarvis"]}

_MODES:         dict      = _config.get("modes", {}) or {}
TRIGGER_WORDS:  list[str] = _config.get("trigger_words", ["system", "nyra", "jarvis"])

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

IGNORE_WORDS = [
    "please", "can you", "hey", "then", "the", "and",
    "set", "now", "just", "go", "enter",
]

MODE_THRESHOLD = 72

MODE_ALIASES: dict[str, str] = {
    "gg":  "gg",
    "g g": "gg",
}

ACTIVATE_WORDS = [
    "activate", "activates", "switch to", "switch",
    "turn on", "enable", "start", "load", "go to",
]

# Timer pattern: "for X minutes" / "for X min"
import re as _re
_TIMER_RE = _re.compile(r"\bfor\s+\d+\s*min", _re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _strip_trigger(text: str) -> str | None:
    """Remove trigger word and return remainder, or None if not found."""
    for t in TRIGGER_WORDS:
        if t in text:
            return text.replace(t, "", 1).strip()
    return None


def _clean(text: str) -> str:
    """Strip punctuation and filler words."""
    text = text.translate(str.maketrans("", "", _string.punctuation))
    for w in IGNORE_WORDS:
        # word-boundary-safe replace
        text = text.replace(f" {w} ", " ")
        if text.startswith(f"{w} "):
            text = text[len(w):]
        if text.endswith(f" {w}"):
            text = text[: -len(w)]
    return " ".join(text.split())


def _strip_activate_words(text: str) -> str:
    """Remove 'activate', 'switch to', etc. before mode matching."""
    for w in sorted(ACTIVATE_WORDS, key=len, reverse=True):
        text = text.replace(w, " ")
    return " ".join(text.split())


def _normalize_mode_text(text: str) -> str:
    """
    Normalize spoken mode references:
      'zen mode' → 'zen state'
      'alpha.'   → 'alpha'
    """
    text = text.translate(str.maketrans("", "", _string.punctuation))
    text = text.replace("mode", "state")
    return text.strip()


def _strip_timer(text: str) -> str:
    """Remove 'for X min(utes)' clause so it doesn't confuse mode matching."""
    return _TIMER_RE.sub("", text).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────
def parse_command(raw_text: str) -> tuple[str | None, str]:
    """
    Returns (command, payload).

    Commands:
      "activate"     — switch to a mode  (payload = mode key from config)
      "open_app"     — open an app       (payload = app name string)
      "close_app"    — close an app      (payload = app name string)
      "close_all"    — close everything  (payload = "")
      "open_website" — open a URL        (payload = url/query string)
      "exit"         — exit OS GOD
      None           — not recognised
    """
    text = raw_text.lower().strip()

    # ── 1. trigger gate ───────────────────────────────────────────────────────
    remainder = _strip_trigger(text)
    if remainder is None:
        # Text mode: treat full input as command
        remainder = text

    remainder = _clean(remainder)
    remainder = _normalize_mode_text(remainder)
    logger.debug(f"Parser remainder after clean: {remainder!r}")

    # ── 2. hard-coded shortcuts ───────────────────────────────────────────────
    for alias, mode_key in MODE_ALIASES.items():
        if alias in remainder:
            logger.debug(f"Alias match: {alias!r} → {mode_key!r}")
            return "activate", mode_key

    # ── 3. close everything ───────────────────────────────────────────────────
    if (
        fuzz.partial_ratio("close all", remainder) > 70
        or fuzz.partial_ratio("close everything", remainder) > 70
        or fuzz.partial_ratio("reset system", remainder) > 70
    ):
        return "close_all", ""

    # ── 4. close specific app ─────────────────────────────────────────────────
    if remainder.startswith("close ") or remainder.startswith("kill "):
        return "close_app", remainder

    # ── 5. exit / quit ────────────────────────────────────────────────────────
    # BUG FIX: "shutdown" was here but "shutdown" is a valid mode name in config.
    # Only treat it as exit if it's exactly "shutdown" with no other context.
    # Mode matching in step 6 will correctly catch "activate shutdown".
    if any(w in remainder for w in ("stop osgod", "exit", "quit")):
        return "exit", remainder

    # ── 6. mode detection (fuzzy) ─────────────────────────────────────────────
    if _MODES:
        mode_keys = list(_MODES.keys())

        # strip activate words + timer clause before comparing
        query = _strip_activate_words(remainder)
        query = _strip_timer(query)
        query = query.replace("state", "").strip()

        if query:  # BUG FIX: skip fuzzy match on empty query (avoids false positives)
            stripped_keys = [m.replace("state", "").strip() for m in mode_keys]

            result = process.extractOne(
                query,
                stripped_keys,
                scorer=fuzz.partial_ratio,
            )

            if result:
                match, best_score, idx = result
                logger.debug(f"Fuzzy mode match: {match!r} score={best_score} → {mode_keys[idx]!r}")
                if best_score >= MODE_THRESHOLD:
                    # Return the full raw text as payload so router can extract timer info
                    return "activate", mode_keys[idx]
            else:
                logger.debug("Fuzzy mode match returned no results.")

    # ── 7. open app ───────────────────────────────────────────────────────────
    if any(remainder.startswith(w) for w in ("open ", "launch ", "start ")):
        return "open_app", remainder

    # ── 8. website ────────────────────────────────────────────────────────────
    if any(w in remainder for w in ("search", "go to", "open website")):
        return "open_website", remainder

    # ── 9. unrecognised ───────────────────────────────────────────────────────
    logger.debug(f"No command matched for: {remainder!r}")
    return None, remainder