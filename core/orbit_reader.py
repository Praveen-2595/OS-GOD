"""
core/orbit_reader.py — Orbit integration for OS GOD's "Jarvis" brain.

Pulls the user's live productivity state from the Orbit API and hands it to the
brain as a single combined snapshot:

    /api/gravity → {"score": 50, "band": "warning", "updatedAt": "..."}
    /api/tasks   → {"pendingCount": 13, "tasks": [{id, text, list}, ...]}
    /api/goals   → [{id, title, priority, deadline, status, completionPct}, ...]

Design notes:
  - One GET per endpoint, behind a short in-process cache (orbit.cache_ttl,
    default 60s) so the brain can call this on every utterance without
    hammering Orbit.
  - Fails soft. If Orbit is offline, the token is bad, or a request times out,
    fetch_orbit_summary() returns an ok=False snapshot with empty fields — the
    brain simply runs without Orbit context instead of crashing.
"""

import difflib
import os
import sys
import time
import yaml
from loguru import logger

try:
    import requests
except ImportError:                      # requests missing → degrade gracefully
    requests = None


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
def _resource(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, relative)


try:
    with open(_resource("config.yaml"), "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f) or {}
except FileNotFoundError:
    _config = {}

_ORBIT_CFG: dict = _config.get("orbit", {}) or {}

ENABLED   = bool(_ORBIT_CFG.get("enabled", True))
BASE_URL  = (_ORBIT_CFG.get("base_url") or "https://orbit-black.vercel.app").rstrip("/")
# Token: the ORBIT_TOKEN env var is primary (keep the secret out of config.yaml);
# the `orbit.token` config key is a fallback for local/dev convenience.
TOKEN     = os.environ.get("ORBIT_TOKEN") or (_ORBIT_CFG.get("token", "") or "")
CACHE_TTL = int(_ORBIT_CFG.get("cache_ttl", 60))
TIMEOUT   = 8     # seconds per request


# ─────────────────────────────────────────────────────────────────────────────
# Cache (in-process, TTL-bounded)
# ─────────────────────────────────────────────────────────────────────────────
_cache: dict | None = None
_cache_time: float  = 0.0


def _empty_summary() -> dict:
    return {
        "ok": False,
        "score": None,
        "band": None,
        "pendingCount": 0,
        "tasks": [],
        "goals": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────────────────────
def _get(endpoint: str):
    """GET /api/<endpoint> with the bearer token; returns parsed JSON."""
    url = f"{BASE_URL}/api/{endpoint}"
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post(endpoint: str, payload: dict):
    """POST /api/<endpoint> with JSON payload and bearer token; returns parsed JSON."""
    url = f"{BASE_URL}/api/{endpoint}"
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _invalidate_cache() -> None:
    """Force next fetch_orbit_summary() to hit the network."""
    global _cache, _cache_time
    _cache = None
    _cache_time = 0.0


def fetch_orbit_summary(force: bool = False) -> dict:
    """
    Return a combined Orbit snapshot:
        {ok, score, band, pendingCount, tasks: [...], goals: [...]}

    Cached for `orbit.cache_ttl` seconds. Fails soft — on any error (Orbit
    offline, bad token, timeout) returns an ok=False snapshot with empty
    fields so callers never have to handle exceptions.
    """
    global _cache, _cache_time

    if not ENABLED:
        return _empty_summary()

    now = time.time()
    if not force and _cache is not None and (now - _cache_time) < CACHE_TTL:
        return _cache

    if requests is None:
        logger.warning("Orbit: 'requests' not installed — skipping.")
        return _empty_summary()

    summary = _empty_summary()
    try:
        gravity = _get("gravity") or {}
        tasks   = _get("tasks") or {}
        goals   = _get("goals") or []

        summary["score"]        = gravity.get("score")
        summary["band"]         = gravity.get("band")
        summary["pendingCount"] = tasks.get("pendingCount", 0)
        summary["tasks"]        = tasks.get("tasks") or []
        summary["goals"]        = goals if isinstance(goals, list) else []
        summary["ok"]           = True

        _cache, _cache_time = summary, now
        logger.info(
            f"Orbit: gravity={summary['score']}% ({summary['band']}), "
            f"{summary['pendingCount']} pending tasks, {len(summary['goals'])} goals."
        )
    except Exception as e:
        logger.warning(f"Orbit unavailable ({e}) — brain runs without Orbit context.")
        return _empty_summary()

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Write operations
# ─────────────────────────────────────────────────────────────────────────────
def complete_task(task_text: str) -> dict:
    """Mark a task as complete in Orbit via fuzzy match on task_text."""
    if requests is None:
        return {"success": False, "message": "'requests' not installed."}
    try:
        summary = fetch_orbit_summary()
        tasks = summary.get("tasks") or []
        texts = [t.get("text", "") for t in tasks]
        matches = difflib.get_close_matches(task_text, texts, n=1, cutoff=0.6)
        if not matches:
            return {"success": False, "message": f"Task not found: {task_text}"}
        matched_text = matches[0]
        task = next(t for t in tasks if t.get("text") == matched_text)
        _post("tasks/complete", {"taskId": task["id"], "list": task.get("list", "today")})
        _invalidate_cache()
        return {"success": True, "task_text": matched_text, "message": f"Completed: {matched_text}"}
    except Exception as e:
        logger.warning(f"Orbit complete_task failed ({e})")
        return {"success": False, "message": str(e)}


def log_goal_progress(goal_title: str, hours: float) -> dict:
    """Log hours worked on a goal in Orbit via fuzzy match on goal_title."""
    if requests is None:
        return {"success": False, "message": "'requests' not installed."}
    try:
        summary = fetch_orbit_summary()
        goals = summary.get("goals") or []
        titles = [g.get("title", "") for g in goals]
        matches = difflib.get_close_matches(goal_title, titles, n=1, cutoff=0.6)
        if not matches:
            return {"success": False, "message": f"Goal not found: {goal_title}"}
        matched_title = matches[0]
        goal = next(g for g in goals if g.get("title") == matched_title)
        result = _post("goals/update", {"goalId": goal["id"], "logged_hours": hours})
        _invalidate_cache()
        new_pct = result.get("completionPct", goal.get("completionPct", "?"))
        return {"success": True, "goal_title": matched_title, "new_completion_pct": new_pct}
    except Exception as e:
        logger.warning(f"Orbit log_goal_progress failed ({e})")
        return {"success": False, "message": str(e)}


def add_task(text: str, list_type: str = "today") -> dict:
    """Add a new task to Orbit."""
    if requests is None:
        return {"success": False, "message": "'requests' not installed."}
    try:
        _post("tasks/add", {"text": text, "list": list_type})
        _invalidate_cache()
        return {"success": True, "task_text": text}
    except Exception as e:
        logger.warning(f"Orbit add_task failed ({e})")
        return {"success": False, "message": str(e)}
