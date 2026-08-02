"""
core/brain.py — Hybrid LLM brain for OS GOD (the "Jarvis" layer).

ONLINE  (brain.enabled = true in config.yaml AND ANTHROPIC_API_KEY is set):
    Free-form natural language is routed through Claude (Anthropic Messages API)
    with tool-use. The model decides which actions to take and calls tools that
    map onto the existing router/feedback layer — so the router stays the "hands"
    and this module is the "brain".

OFFLINE / no key / API error:
    think() returns False, signalling the caller to fall back to the deterministic
    fuzzy parser (core.parser.parse_command + core.router.route_command).

Design notes:
  - Manual tool-use loop (not the beta tool_runner) for full control over which
    tools execute and how results are logged.
  - effort="low" + no extended thinking by default → snappy, cheap responses
    suited to an interactive voice assistant. Bump `effort` in config for harder
    reasoning.
  - Short-term conversation memory: the last few text turns are kept in-process so
    follow-ups ("now close it") have context. (Long-term memory = a later phase.)
"""

import os
import sys
import yaml
from datetime import date, datetime
from loguru import logger

from core import orbit_reader, memory


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

_BRAIN_CFG: dict = _config.get("brain", {}) or {}
_MODE_KEYS: list[str] = list((_config.get("modes", {}) or {}).keys())

ENABLED     = bool(_BRAIN_CFG.get("enabled", True))
MODEL       = _BRAIN_CFG.get("model", "claude-opus-4-8")
EFFORT      = _BRAIN_CFG.get("effort", "low")
MAX_TOKENS  = 1024
MAX_TURNS   = 6     # safety cap on the tool-use loop per utterance
MAX_HISTORY = 12    # short-term memory: last N stored messages (text turns only)

_client = None
_client_tried = False
_history: list[dict] = []   # short-term in-session memory (text turns only)
_session_id: str = "default"  # set from main.py on startup via brain._session_id = ...
_response_hook = None         # set by main.py: brain._response_hook = overlay.add_feed_message


# ─────────────────────────────────────────────────────────────────────────────
# Client (lazy, fail-soft)
# ─────────────────────────────────────────────────────────────────────────────
def _get_client():
    """Return an Anthropic client, or None if the brain can't run (→ fallback)."""
    global _client, _client_tried
    if _client_tried:
        return _client
    _client_tried = True

    if not ENABLED:
        logger.info("Brain disabled in config — using offline parser.")
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("Brain: ANTHROPIC_API_KEY not set — using offline parser.")
        return None

    try:
        import anthropic
        _client = anthropic.Anthropic()
        logger.info(f"Brain online — model={MODEL}, effort={EFFORT}")
        print(f"🧠 Brain online (Claude {MODEL})")
    except Exception as e:
        logger.warning(f"Brain unavailable ({e}) — using offline parser.")
        _client = None
    return _client


def is_available() -> bool:
    return _get_client() is not None


def reset_memory() -> None:
    """Clear short-term conversation memory."""
    _history.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Orbit context (the "Jarvis" productivity layer)
# ─────────────────────────────────────────────────────────────────────────────
def _days_left(deadline: str) -> str:
    """Human phrase for how far off a YYYY-MM-DD deadline is."""
    try:
        d = datetime.strptime(deadline, "%Y-%m-%d").date()
        delta = (d - date.today()).days
        if delta > 0:
            return f"{delta}d left"
        if delta == 0:
            return "due today"
        return f"{abs(delta)}d overdue"
    except Exception:
        return deadline or "no deadline"


def _orbit_data(s: dict) -> str:
    """Format an Orbit snapshot into the four status lines used by the prompt
    and the get_orbit_status tool."""
    goals = s.get("goals") or []
    if goals:
        goals_summary = "; ".join(
            f"{g.get('title', '?')} ({g.get('completionPct', 0)}% done, "
            f"due {g.get('deadline', '?')} — {_days_left(g.get('deadline', ''))})"
            for g in goals
        )
    else:
        goals_summary = "none set"

    tasks = s.get("tasks") or []
    if tasks:
        groups: dict[str, list[str]] = {}
        for t in tasks:
            groups.setdefault(t.get("list", "other"), []).append(
                (t.get("text") or "").strip()
            )
        task_list = " | ".join(
            f"{lst}: " + "; ".join(items) for lst, items in groups.items()
        )
    else:
        task_list = "no pending tasks"

    return (
        f"- Gravity meter: {s.get('score')}% ({s.get('band') or 'unknown'})\n"
        f"- Pending tasks: {s.get('pendingCount', 0)} tasks\n"
        f"- Active goals: {goals_summary}\n"
        f"- Task list: {task_list}"
    )


def _orbit_block() -> str:
    """The Jarvis/Orbit context appended to the system prompt before every
    Claude call. Empty string when Orbit is unavailable, so the base OS GOD
    prompt keeps working offline."""
    s = orbit_reader.fetch_orbit_summary()
    if not s.get("ok"):
        return ""
    return (
        "\n\n"
        "You are Jarvis, a personal AI assistant for Dj Pawin.\n"
        "Current Orbit status:\n"
        f"{_orbit_data(s)}\n\n"
        "If the user seems idle or unproductive, proactively call it out "
        "in a friendly but firm Jarvis tone. Be specific — reference actual "
        "task names and deadlines. Example: 'Sir, your startup launch is in "
        "22 days and at 0% — want me to pull up the marketing video task?'\n\n"
        "IMPORTANT: Never activate modes, open apps, or execute any tool "
        "automatically based on Orbit data alone. Only comment or warn "
        "proactively. Always wait for explicit user instruction before "
        "taking any system action."
    )


# ─────────────────────────────────────────────────────────────────────────────
# System prompt + tools
# ─────────────────────────────────────────────────────────────────────────────
def _system_prompt() -> str:
    modes = ", ".join(_MODE_KEYS) if _MODE_KEYS else "(none configured)"
    base = (
        "You are OS GOD, a voice-controlled assistant for a Windows PC. "
        "Translate the user's natural language into actions by calling tools.\n\n"
        f"Available modes (use the EXACT name): {modes}.\n"
        "A 'mode' reconfigures the whole PC at once — apps, websites, wallpaper, "
        "volume, and sound.\n\n"
        "Guidelines:\n"
        "- Call a tool only when the user wants an action. For questions or "
        "chit-chat, just reply in one short, natural sentence.\n"
        "- For minor choices (which of two equivalent options), pick a sensible "
        "one and act — don't ask.\n"
        "- If the user names a mode that isn't in the list, pick the closest match.\n"
        "- Keep spoken replies short: one sentence is ideal. The reply is read "
        "aloud, so avoid lists, markdown, or long explanations.\n\n"
        "SAFETY RULE: For destructive actions (close_everything, shutdown, or "
        "close_app for more than 2 apps), always confirm with the user first "
        "before executing. Say 'Sir, shall I close everything?' and wait for an "
        "explicit yes/no. Never execute destructive actions from a single "
        "ambiguous voice command — if the request is unclear or could be "
        "background noise, ask for confirmation instead of acting."
    )
    # Persistent facts — always injected so Jarvis remembers user preferences
    facts = memory.get_all_facts()
    if facts:
        facts_text = "\n".join(f"  {k}: {v}" for k, v in facts.items())
        base += f"\n\nKnown facts about user:\n{facts_text}"

    # Cross-session history — only on cold start (no in-memory history yet)
    if not _history:
        recent = memory.get_recent_messages(6)
        if recent:
            hist_text = "\n".join(
                f"  {m['role']}: {m['content']}" for m in recent
            )
            base += f"\n\nRecent conversation history (previous session):\n{hist_text}"

    return base + _orbit_block()


TOOLS = [
    {
        "name": "activate_mode",
        "description": (
            "Switch the PC into a named mode (closes/opens apps, sets wallpaper, "
            "volume and sound). Call when the user wants to start, activate, switch "
            "to, or enter a mode."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "Exact mode name from the available list.",
                },
                "minutes": {
                    "type": "integer",
                    "description": "Optional. Auto-switch away after this many minutes.",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "open_app",
        "description": "Open/launch a desktop application by name (e.g. spotify, discord, notion).",
        "input_schema": {
            "type": "object",
            "properties": {"app": {"type": "string", "description": "App name."}},
            "required": ["app"],
        },
    },
    {
        "name": "close_app",
        "description": "Close/quit/kill a single running application by name.",
        "input_schema": {
            "type": "object",
            "properties": {"app": {"type": "string", "description": "App name."}},
            "required": ["app"],
        },
    },
    {
        "name": "close_everything",
        "description": (
            "Close all running apps. Pass 'except' to keep specific apps open. "
            "Example: close_everything(except=['vs code', 'opera gx']) keeps those "
            "two open while closing everything else."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "except": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "App names to keep open (e.g. ['vs code', 'discord']).",
                },
            },
        },
    },
    {
        "name": "open_website",
        "description": "Open a website or run a web search. Pass a full URL or a search query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A URL or a search query."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "set_volume",
        "description": "Set the Windows system volume to a percentage (0-100).",
        "input_schema": {
            "type": "object",
            "properties": {"level": {"type": "integer", "description": "0 to 100."}},
            "required": ["level"],
        },
    },
    {
        "name": "get_mode_history",
        "description": "Look up the user's recent mode usage (what modes ran and for how long).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_orbit_status",
        "description": (
            "Fetch the user's current Orbit productivity status — gravity/focus "
            "meter and band, count of pending tasks, the task list, and active "
            "goals with their completion percent and deadlines. Call this when "
            "the user asks about their tasks, goals, deadlines, focus score, "
            "progress, or what they should work on next."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_action",
        "description": (
            "Type text, search, or navigate in the active browser window. "
            "Use 'search' to open a new tab and search. "
            "Use 'type' to type text into whichever field is currently focused (does NOT press Enter). "
            "Use 'go_to_url' to navigate the browser to a specific URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "type", "go_to_url"],
                    "description": "The browser action to perform.",
                },
                "text": {
                    "type": "string",
                    "description": "The text to type or search query.",
                },
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to (only for go_to_url).",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "music_control",
        "description": "Control YouTube Music playback in the browser.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "next", "previous", "volume_up", "volume_down", "shuffle"],
                    "description": "The playback action to perform.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "remember_fact",
        "description": (
            "Save a long-term fact about the user for future reference. "
            "Use when the user shares a preference, habit, or personal detail "
            "(e.g. 'I prefer dark themes', 'I wake up at 6 AM')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Short snake_case identifier (e.g. 'preferred_theme', 'wake_time').",
                },
                "value": {
                    "type": "string",
                    "description": "The value to store.",
                },
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall_facts",
        "description": "Retrieve all stored long-term facts about the user.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "open_and_play",
        "description": (
            "Open a URL in the browser and automatically start playback after the "
            "page loads. Use when the user says 'open YouTube Music and play', "
            "'play music', or asks to open a media URL and start playing immediately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to open (e.g. https://music.youtube.com).",
                },
                "delay": {
                    "type": "number",
                    "description": "Seconds to wait for page load before pressing play. Default: 3.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "complete_task",
        "description": (
            "Mark a task as complete in Orbit. "
            "Call when the user says they finished, completed, or did a task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_text": {
                    "type": "string",
                    "description": "Partial or full task name to fuzzy-match against the task list.",
                },
            },
            "required": ["task_text"],
        },
    },
    {
        "name": "log_goal_progress",
        "description": (
            "Log hours worked on a goal in Orbit. "
            "Call when the user reports time spent on a goal or project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_title": {
                    "type": "string",
                    "description": "Partial or full goal title to fuzzy-match.",
                },
                "hours": {
                    "type": "number",
                    "description": "Number of hours worked.",
                },
            },
            "required": ["goal_title", "hours"],
        },
    },
    {
        "name": "add_task",
        "description": (
            "Add a new task to Orbit. "
            "Call when the user wants to add, create, or put a task on their list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The task text to add.",
                },
                "list": {
                    "type": "string",
                    "enum": ["today", "daily", "quick"],
                    "description": "Which list to add to. Default: 'today'.",
                },
            },
            "required": ["text"],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool executor — maps tool calls onto the existing router/feedback layer
# ─────────────────────────────────────────────────────────────────────────────
def _exec_tool(name: str, inp: dict, route_fn, set_mode_fn) -> str:
    try:
        if name == "activate_mode":
            mode = (inp.get("mode") or "").strip()
            if not mode:
                return "No mode was specified."
            minutes = inp.get("minutes")
            if minutes:
                route_fn("activate_timed", f"{mode}|{int(minutes)}")
                if set_mode_fn:
                    set_mode_fn(mode)
                return f"Activated {mode} for {int(minutes)} minutes."
            route_fn("activate", mode)
            if set_mode_fn:
                set_mode_fn(mode)
            return f"Activated {mode}."

        if name == "open_app":
            app = (inp.get("app") or "").strip()
            if not app:
                return "No app specified."
            route_fn("open_app", f"open {app}")
            return f"Opening {app}."

        if name == "close_app":
            app = (inp.get("app") or "").strip()
            if not app:
                return "No app specified."
            route_fn("close_app", f"close {app}")
            return f"Closing {app}."

        if name == "close_everything":
            exceptions: list[str] = [
                s.strip() for s in (inp.get("except") or []) if s.strip()
            ]
            payload = ("except " + ",".join(exceptions)) if exceptions else ""
            route_fn("close_all", payload)
            exc_msg = f" (keeping {', '.join(exceptions)})" if exceptions else ""
            return f"Closing everything{exc_msg}."

        if name == "open_website":
            query = (inp.get("query") or "").strip()
            if not query:
                return "No website or query specified."
            route_fn("open_website", f"open {query}")
            return f"Opening {query}."

        if name == "set_volume":
            level = max(0, min(100, int(inp.get("level", 50))))
            from utils.feedback import set_volume
            set_volume(level / 100)
            return f"Volume set to {level} percent."

        if name == "get_mode_history":
            from utils.timer import get_history
            rows = get_history(5)
            if not rows:
                return "No mode history yet."
            return "; ".join(
                f"{r.get('mode')} for {r.get('duration_minutes')} min" for r in rows
            )

        if name == "get_orbit_status":
            s = orbit_reader.fetch_orbit_summary()
            if not s.get("ok"):
                return "Orbit is offline — I can't read your tasks and goals right now."
            return "Current Orbit status:\n" + _orbit_data(s)

        if name == "browser_action":
            from core import browser
            action = (inp.get("action") or "").strip()
            if action == "search":
                query = (inp.get("text") or "").strip()
                if not query:
                    return "No search query provided."
                return browser.search_in_browser(query)
            if action == "type":
                text = (inp.get("text") or "").strip()
                if not text:
                    return "No text provided."
                return browser.type_in_browser(text)
            if action == "go_to_url":
                url = (inp.get("url") or inp.get("text") or "").strip()
                if not url:
                    return "No URL provided."
                return browser.go_to_url(url)
            return f"Unknown browser action: {action!r}"

        if name == "music_control":
            from core import browser
            action = (inp.get("action") or "").strip()
            if not action:
                return "No action specified."
            return browser.music_control(action)

        if name == "remember_fact":
            key   = (inp.get("key")   or "").strip()
            value = (inp.get("value") or "").strip()
            if not key or not value:
                return "Both key and value are required."
            memory.save_fact(key, value)
            return f"Remembered: {key} = {value}"

        if name == "recall_facts":
            facts = memory.get_all_facts()
            if not facts:
                return "No facts stored yet."
            return "\n".join(f"{k}: {v}" for k, v in facts.items())

        if name == "open_and_play":
            from core import browser
            url = (inp.get("url") or "").strip()
            if not url:
                return "No URL provided."
            delay = float(inp.get("delay") or 3.0)
            return browser.open_and_play(url, delay)

        if name == "complete_task":
            task_text = (inp.get("task_text") or "").strip()
            if not task_text:
                return "No task text specified."
            result = orbit_reader.complete_task(task_text)
            return result.get("message") or (
                f"Completed: {result['task_text']}" if result.get("success")
                else "Could not complete task."
            )

        if name == "log_goal_progress":
            goal_title = (inp.get("goal_title") or "").strip()
            hours = float(inp.get("hours") or 0)
            if not goal_title:
                return "No goal title specified."
            result = orbit_reader.log_goal_progress(goal_title, hours)
            if result.get("success"):
                return (
                    f"Logged {hours}h on '{result['goal_title']}'. "
                    f"Completion: {result['new_completion_pct']}%."
                )
            return result.get("message") or "Could not log goal progress."

        if name == "add_task":
            text = (inp.get("text") or "").strip()
            if not text:
                return "No task text specified."
            list_type = (inp.get("list") or "today").strip()
            result = orbit_reader.add_task(text, list_type)
            if result.get("success"):
                return f"Added '{text}' to {list_type} list."
            return result.get("message") or "Could not add task."

        return f"Unknown tool: {name}"

    except Exception as e:
        logger.error(f"Brain tool '{name}' failed: {e}")
        return f"Error running {name}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Public entry — think()
# ─────────────────────────────────────────────────────────────────────────────
def think(text: str, route_fn, set_mode_fn=None, speak_fn=None) -> bool:
    """
    Route `text` through Claude with tool-use.

    Returns
    -------
    True  — handled by the brain (actions executed and/or a reply produced).
    False — brain unavailable or errored; caller should fall back to the parser.

    Parameters
    ----------
    route_fn    : core.router.route_command (the action/tool layer)
    set_mode_fn : optional callback to keep external mode-tracking in sync
    speak_fn    : optional TTS function (utils.feedback.speak) for spoken replies
    """
    client = _get_client()
    if client is None:
        return False

    messages = _history + [{"role": "user", "content": text}]
    used_tool = False
    final_text = ""

    try:
        for _ in range(MAX_TURNS):
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=_system_prompt(),
                tools=TOOLS,
                messages=messages,
            )

            if resp.stop_reason == "tool_use":
                used_tool = True
                messages.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        logger.info(f"Brain tool: {block.name} {block.input!r}")
                        out = _exec_tool(block.name, block.input, route_fn, set_mode_fn)
                        results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": out,
                        })
                messages.append({"role": "user", "content": results})
                continue

            # end_turn (or any non-tool stop) — collect the spoken reply
            final_text = "".join(
                b.text for b in resp.content if b.type == "text"
            ).strip()
            break

    except Exception as e:
        logger.error(f"Brain error ({e}) — falling back to offline parser.")
        return False

    # ── update short-term memory (store the text turn only; keep it light) ──────
    _history.append({"role": "user", "content": text})
    if final_text:
        _history.append({"role": "assistant", "content": final_text})
    if len(_history) > MAX_HISTORY:
        del _history[:-MAX_HISTORY]

    # ── persist to SQLite for cross-session recall ─────────────────────────────
    memory.save_message("user", text, _session_id)
    if final_text:
        memory.save_message("assistant", final_text, _session_id)

    # ── output ─────────────────────────────────────────────────────────────────
    if final_text:
        print(f"🤖 {final_text}")
        if _response_hook:
            try:
                _response_hook("jarvis", final_text)
            except Exception:
                pass
        # Only speak conversational replies. When a tool ran, the router already
        # gives audio feedback ("Opening X", "Activating Y") — speaking the
        # brain's confirmation too would be redundant.
        if not used_tool and speak_fn:
            speak_fn(final_text)

    return True
