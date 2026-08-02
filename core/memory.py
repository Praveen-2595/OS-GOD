"""
core/memory.py — Persistent memory for OS GOD Jarvis.

Three SQLite tables:
  conversations — per-session message log (role, content, session_id)
  facts         — long-term key/value facts about the user
  mode_context  — usage stats per mode (use count, avg duration)

All public functions fail soft — exceptions are caught, logged, and never
propagate, so a DB error never crashes OS GOD.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import threading
from datetime import datetime
from loguru import logger


def _db_base() -> str:
    """Persistent data lives next to the exe (frozen) or CWD (dev)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(".")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
def _load_cfg() -> dict:
    try:
        import yaml
        cfg_path = os.path.join(
            getattr(sys, "_MEIPASS", os.path.abspath(".")), "config.yaml"
        )
        with open(cfg_path, "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("memory", {}) or {}
    except Exception:
        return {}


_CFG     = _load_cfg()
_ENABLED = bool(_CFG.get("enabled", True))
_DB_PATH = os.path.join(_db_base(), _CFG.get("db_path", "data/jarvis_memory.db"))
_RECENT  = int(_CFG.get("recent_messages", 10))

# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection | None:
    global _conn
    if not _ENABLED:
        return None
    if _conn is not None:
        return _conn
    try:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _create_tables(_conn)
        logger.info(f"Memory DB ready: {_DB_PATH}")
        return _conn
    except Exception as e:
        logger.error(f"Memory: could not open DB ({e})")
        return None


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            role        TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            session_id  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS facts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            key        TEXT    NOT NULL UNIQUE,
            value      TEXT    NOT NULL,
            updated_at TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mode_context (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            mode_name            TEXT    NOT NULL UNIQUE,
            last_used            TEXT    NOT NULL,
            use_count            INTEGER NOT NULL DEFAULT 0,
            avg_duration_minutes REAL    NOT NULL DEFAULT 0.0
        );
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def save_message(role: str, content: str, session_id: str) -> None:
    try:
        with _lock:
            conn = _get_conn()
            if conn is None:
                return
            conn.execute(
                "INSERT INTO conversations (timestamp, role, content, session_id) VALUES (?,?,?,?)",
                (datetime.utcnow().isoformat(), role, content, session_id),
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"memory.save_message: {e}")


def get_recent_messages(n: int = _RECENT) -> list[dict]:
    """Return the last n messages as [{'role': ..., 'content': ...}, ...]."""
    try:
        with _lock:
            conn = _get_conn()
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]
    except Exception as e:
        logger.debug(f"memory.get_recent_messages: {e}")
        return []


def save_fact(key: str, value: str) -> None:
    try:
        with _lock:
            conn = _get_conn()
            if conn is None:
                return
            conn.execute(
                """INSERT INTO facts (key, value, updated_at) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE
                   SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, datetime.utcnow().isoformat()),
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"memory.save_fact: {e}")


def get_fact(key: str) -> str | None:
    try:
        with _lock:
            conn = _get_conn()
            if conn is None:
                return None
            row = conn.execute(
                "SELECT value FROM facts WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.debug(f"memory.get_fact: {e}")
        return None


def get_all_facts() -> dict[str, str]:
    try:
        with _lock:
            conn = _get_conn()
            if conn is None:
                return {}
            rows = conn.execute("SELECT key, value FROM facts").fetchall()
            return {k: v for k, v in rows}
    except Exception as e:
        logger.debug(f"memory.get_all_facts: {e}")
        return {}


def update_mode_usage(mode_name: str, duration_minutes: float) -> None:
    try:
        with _lock:
            conn = _get_conn()
            if conn is None:
                return
            row = conn.execute(
                "SELECT use_count, avg_duration_minutes FROM mode_context WHERE mode_name=?",
                (mode_name,),
            ).fetchone()
            now = datetime.utcnow().isoformat()
            if row:
                count, avg = row
                new_count = count + 1
                new_avg   = (avg * count + duration_minutes) / new_count
                conn.execute(
                    """UPDATE mode_context
                       SET last_used=?, use_count=?, avg_duration_minutes=?
                       WHERE mode_name=?""",
                    (now, new_count, new_avg, mode_name),
                )
            else:
                conn.execute(
                    """INSERT INTO mode_context
                       (mode_name, last_used, use_count, avg_duration_minutes)
                       VALUES (?,?,?,?)""",
                    (mode_name, now, 1, duration_minutes),
                )
            conn.commit()
    except Exception as e:
        logger.debug(f"memory.update_mode_usage: {e}")


def get_mode_stats() -> list[dict]:
    try:
        with _lock:
            conn = _get_conn()
            if conn is None:
                return []
            rows = conn.execute(
                """SELECT mode_name, last_used, use_count, avg_duration_minutes
                   FROM mode_context ORDER BY use_count DESC"""
            ).fetchall()
            return [
                {
                    "mode": r[0],
                    "last_used": r[1],
                    "use_count": r[2],
                    "avg_duration_minutes": round(r[3], 1),
                }
                for r in rows
            ]
    except Exception as e:
        logger.debug(f"memory.get_mode_stats: {e}")
        return []
