import os
import sys
from loguru import logger

# ─────────────────────────────────────────────────────────────────────────────
# Logger setup — call once at import
# Creates logs/ folder automatically. Rotates at 5MB. Keeps last 3 files.
# ─────────────────────────────────────────────────────────────────────────────

_LOG_DIR  = os.path.join(os.path.abspath("."), "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "osgod.log")

os.makedirs(_LOG_DIR, exist_ok=True)

# Remove default loguru handler (prints everything to stderr unformatted)
logger.remove()

# Console — INFO and above, clean format, coloured
logger.add(
    sys.stdout,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    colorize=True,
)

# File — DEBUG and above, full format, auto-rotation
logger.add(
    _LOG_FILE,
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
    rotation="5 MB",
    retention=3,
    encoding="utf-8",
)

logger.info(f"Logger initialised — writing to {_LOG_FILE}")
