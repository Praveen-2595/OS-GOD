"""
utils/server.py — OS GOD iPad/Phone Controller Server

Runs a Flask web server in a background thread.
Open http://YOUR-PC-IP:5000 on any device on your WiFi.
No app install needed — just a browser.
"""

import json
import threading
import socket
import yaml
import os
import sys
from loguru import logger

# ─────────────────────────────────────────────────────────────────────────────
# Flask import — graceful failure if not installed
# ─────────────────────────────────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, request, Response
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.error("Flask not installed — run: pip install flask")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
def _resource(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, relative)


try:
    with open(_resource("config.yaml"), "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)
except FileNotFoundError:
    _config = {"modes": {}}

PORT = 5000

# ─────────────────────────────────────────────────────────────────────────────
# Shared state — updated by router and main.py
# ─────────────────────────────────────────────────────────────────────────────
_current_mode:           str  = "None"
_dj_active:              bool = False
_minimize_terminal_fn         = None   # injected by main.py
_restore_terminal_fn          = None   # injected by main.py
_is_terminal_visible_fn       = None   # injected by main.py


def update_current_mode(mode_name: str) -> None:
    global _current_mode, _dj_active
    _current_mode = mode_name
    _dj_active    = (mode_name == "dj pawnin state")


# ─────────────────────────────────────────────────────────────────────────────
# Get local IP
# ─────────────────────────────────────────────────────────────────────────────
def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ─────────────────────────────────────────────────────────────────────────────
# Static visual metadata — keyed to your config.yaml mode names exactly
# ─────────────────────────────────────────────────────────────────────────────
MODE_META = {
    "alpha state":     {"color": "#ff2200", "glow": "255,34,0",   "emoji": "⚡", "tag": "ALPHA",  "desc": "Focus & grind"},
    "zen state":       {"color": "#00e5ff", "glow": "0,229,255",  "emoji": "🧘", "tag": "ZEN",    "desc": "Deep work"},
    "render state":    {"color": "#ffaa00", "glow": "255,170,0",  "emoji": "🎬", "tag": "RENDER", "desc": "Creative flow"},
    "dj pawnin state": {"color": "#ff006e", "glow": "255,0,110",  "emoji": "🎧", "tag": "DJ",     "desc": "Pawnin mode"},
    "gaming state":    {"color": "#4d96ff", "glow": "77,150,255", "emoji": "🎮", "tag": "GAMING", "desc": "Let's go"},
    "developer mode":  {"color": "#00c853", "glow": "0,200,83",   "emoji": "💻", "tag": "DEV",    "desc": "Build & code"},
    "gg":              {"color": "#ffd700", "glow": "255,215,0",  "emoji": "🏆", "tag": "GG",     "desc": "Victory"},
    "shutdown":        {"color": "#ff0000", "glow": "255,0,0",    "emoji": "💀", "tag": "OFF",    "desc": "Power down"},
}

MODE_ORDER = [
    "alpha state",
    "zen state",
    "render state",
    "dj pawnin state",
    "gaming state",
    "developer mode",
]


# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────
def create_app(route_fn, voice_fn=None):
    app = Flask(__name__)
    app.config["ENV"] = "production"

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    modes = _config.get("modes", {})

    mode_meta_js = json.dumps(MODE_META)

    dj_cfg    = modes.get("dj pawnin state", {}) or {}
    dj_tracks = dj_cfg.get("tracks", []) or []

    def _track_label(path: str) -> str:
        name = os.path.basename(path)
        for ext in (".wav", ".mp3", ".ogg", ".flac"):
            name = name.replace(ext, "")
        return name.replace("_", " ").replace("-", " ").title()

    # ── Build mode cards HTML ─────────────────────────────────────────────────
    mode_cards_html = ""
    for mode_name in MODE_ORDER:
        if mode_name not in modes:
            continue
        meta     = MODE_META.get(mode_name, {"color": "#888", "glow": "136,136,136", "emoji": "▶️", "tag": mode_name.upper(), "desc": ""})
        color    = meta["color"]
        glow     = meta["glow"]
        emoji    = meta["emoji"]
        tag      = meta["tag"]
        desc     = meta["desc"]
        mode_id  = mode_name.replace(" ", "-")
        mode_cfg = modes[mode_name] or {}
        vol      = mode_cfg.get("volume", _config.get("defaults", {}).get("volume", 50))
        app_cnt  = len(mode_cfg.get("apps", []))
        web_cnt  = len(mode_cfg.get("websites", []))

        mode_cards_html += (
            '<button class="mode-card" data-mode="' + mode_name + '" '
            'style="--c:' + color + ';--g:' + glow + '" '
            'onclick="activateMode(\'' + mode_id + '\')">'
            '<div class="card-glow"></div>'
            '<div class="card-body">'
            '<span class="card-emoji">' + emoji + '</span>'
            '<div class="card-info">'
            '<span class="card-tag">' + tag + '</span>'
            '<span class="card-desc">' + desc + '</span>'
            '</div></div>'
            '<div class="card-stats">'
            '<span>🔊 ' + str(vol) + '%</span>'
            '<span>📱 ' + str(app_cnt) + '</span>'
            '<span>🌐 ' + str(web_cnt) + '</span>'
            '</div>'
            '<div class="card-bar"></div>'
            '</button>\n'
        )

    # ── DJ track buttons ──────────────────────────────────────────────────────
    track_buttons_html = ""
    for i, track in enumerate(dj_tracks):
        label = _track_label(track)
        track_buttons_html += (
            '<button class="track-pill" onclick="djAction(\'track' + str(i + 1) + '\')">'
            '<span class="track-num">' + str(i + 1).zfill(2) + '</span>'
            '<span class="track-name">' + label + '</span>'
            '<span class="track-play">▶</span>'
            '</button>\n'
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Token auth — password page shown if wrong/missing token
    # ─────────────────────────────────────────────────────────────────────────
    ACCESS_TOKEN = os.environ.get("OSGOD_TOKEN", "pawnin2025")

    @app.before_request
    def check_token():
        if request.path == "/ping":
            return None
        token = request.args.get("token") or request.headers.get("X-Token", "")
        if token != ACCESS_TOKEN:
            return Response("""<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OS GOD</title>
<style>
body{background:#07070f;color:#ebebff;font-family:-apple-system,sans-serif;
display:flex;flex-direction:column;align-items:center;justify-content:center;
min-height:100vh;gap:18px}
h1{font-family:system-ui;font-size:2rem;letter-spacing:.2em}
input{background:#10101e;border:1px solid rgba(255,255,255,.1);border-radius:12px;
color:#ebebff;padding:14px 18px;font-size:1rem;width:250px;outline:none}
input:focus{border-color:rgba(123,97,255,.5)}
button{background:rgba(123,97,255,.1);border:1.5px solid rgba(123,97,255,.4);
border-radius:12px;color:#a07eff;padding:14px 28px;font-size:1rem;
font-weight:700;cursor:pointer;width:250px;letter-spacing:.08em}
button:active{background:rgba(123,97,255,.25)}
</style></head>
<body>
<h1>⚡ OS GOD</h1>
<input id="t" type="password" placeholder="Access token" autocomplete="current-password">
<button onclick="go()">ENTER</button>
<script>
function go(){
  var t=document.getElementById('t').value;
  window.location.href='/?token='+encodeURIComponent(t);
}
document.getElementById('t').addEventListener('keydown',function(e){
  if(e.key==='Enter') go();
});
</script>
</body></html>""", 401, {"Content-Type": "text/html"})

    @app.route("/ping")
    def ping():
        return "ok", 200

    # ─────────────────────────────────────────────────────────────────────────
    # API Routes
    # ─────────────────────────────────────────────────────────────────────────

    @app.route("/status")
    def status():
        # FIX: call the function references from the module level so they always
        # reflect the latest injected values set by main.py after server starts
        import utils.server as _self
        vis_fn = _self._is_terminal_visible_fn
        visible = vis_fn() if vis_fn else True

        # get current system volume
        try:
            from utils.feedback import get_volume
            vol = int(get_volume() * 100)
        except Exception:
            vol = 50

        return jsonify({
            "current_mode":     _current_mode,
            "dj_active":        _dj_active,
            "terminal_visible": visible,
            "volume":           vol,
        })

    @app.route("/activate/<path:mode_name>", methods=["POST"])
    def activate(mode_name: str):
        mode_name = mode_name.replace("-", " ")
        logger.info(f"[Controller] Activate: {mode_name}")
        print(f"📱 Controller: activate {mode_name!r}")
        threading.Thread(target=route_fn, args=("activate", mode_name), daemon=True).start()
        return jsonify({"status": "ok", "mode": mode_name})

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        confirmed = request.json.get("confirm", False) if request.json else False
        if not confirmed:
            return jsonify({"status": "error", "msg": "confirmation required"}), 400
        logger.info("[Controller] Shutdown confirmed")
        threading.Thread(target=route_fn, args=("activate", "shutdown"), daemon=True).start()
        return jsonify({"status": "ok"})

    @app.route("/gg", methods=["POST"])
    def gg():
        logger.info("[Controller] GG")
        threading.Thread(target=route_fn, args=("activate", "gg"), daemon=True).start()
        return jsonify({"status": "ok"})

    @app.route("/speak", methods=["POST"])
    def speak_endpoint():
        data = request.json or {}
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"status": "error", "msg": "no text provided"}), 400
        if len(text) > 300:
            return jsonify({"status": "error", "msg": "too long"}), 400
        logger.info(f"[Controller] Speak: {text!r}")
        print(f"📱 Controller speak: {text!r}")
        from utils.feedback import speak as _speak
        threading.Thread(target=_speak, args=(text,), daemon=True).start()
        return jsonify({"status": "ok"})

    @app.route("/command", methods=["POST"])
    def command_endpoint():
        """Natural-language command. Routed through the brain when available,
        otherwise through the offline fuzzy parser."""
        data = request.json or {}
        text = (data.get("text", "") or "").strip()
        if not text:
            return jsonify({"status": "error", "msg": "no text provided"}), 400
        if len(text) > 500:
            return jsonify({"status": "error", "msg": "too long"}), 400
        logger.info(f"[Controller] Command: {text!r}")
        print(f"📱 Controller command: {text!r}")

        def _run():
            try:
                import core.brain as _brain
                from utils.feedback import speak as _speak
                handled = _brain.is_available() and _brain.think(text.lower(), route_fn, speak_fn=_speak)
                if not handled:
                    from core.parser import parse_command
                    cmd, payload = parse_command(text.lower())
                    if cmd:
                        route_fn(cmd, payload)
                    else:
                        _speak("Sorry, I didn't understand that.")
            except Exception as e:
                logger.error(f"/command error: {e}")

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "ok"})

    @app.route("/voice", methods=["POST"])
    def voice_endpoint():
        """Ask the core process to capture one voice command from its mic."""
        if voice_fn is None:
            return jsonify({"status": "error", "msg": "voice capture is not ready"}), 503

        def _run_voice():
            try:
                voice_fn()
            except Exception as e:
                logger.error(f"Voice capture error: {e}")

        threading.Thread(target=_run_voice, daemon=True).start()
        return jsonify({"status": "ok"})

    @app.route("/volume", methods=["POST"])
    def volume_endpoint():
        data  = request.json or {}
        level = data.get("level")
        if level is None:
            return jsonify({"status": "error", "msg": "level required (0-100)"}), 400
        try:
            level = max(0, min(100, int(level)))
        except (ValueError, TypeError):
            return jsonify({"status": "error", "msg": "level must be 0-100"}), 400

        def _set():
            try:
                from utils.feedback import set_volume
                set_volume(level / 100)
                logger.info(f"[Controller] Volume set to {level}%")
                print(f"📱 Controller: volume → {level}%")
            except Exception as e:
                logger.error(f"Volume set error: {e}")

        threading.Thread(target=_set, daemon=True).start()
        return jsonify({"status": "ok", "volume": level})

    @app.route("/save-mode", methods=["POST"])
    def save_mode():
        data      = request.json or {}
        mode_name = data.get("name", "").strip().lower()
        if not mode_name:
            return jsonify({"status": "error", "msg": "mode name required"}), 400
        new_mode = {
            "volume":   int(data.get("volume", 50)),
            "apps":     [a.strip() for a in data.get("apps", "").split(",")     if a.strip()],
            "websites": [w.strip() for w in data.get("websites", "").split(",") if w.strip()],
            "close":    [c.strip() for c in data.get("close", "").split(",")    if c.strip()],
        }
        wp = data.get("wallpaper", "").strip()
        if wp:
            new_mode["wallpaper"] = wp
        try:
            cfg_path = _resource("config.yaml")
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg.setdefault("modes", {})[mode_name] = new_mode
            with open(cfg_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
            _config.setdefault("modes", {})[mode_name] = new_mode
            modes[mode_name] = new_mode
            logger.info(f"[Controller] Saved mode: {mode_name}")
            print(f"📱 New mode saved: {mode_name!r}")
            return jsonify({"status": "ok", "mode": mode_name})
        except Exception as e:
            logger.error(f"Save mode error: {e}")
            return jsonify({"status": "error", "msg": str(e)}), 500

    @app.route("/dj/<action>", methods=["POST"])
    def dj_control(action: str):
        import utils.hotkeys as _hk
        ctrl = _hk._dj_controller
        if ctrl is None:
            return jsonify({"status": "error", "msg": "DJ mode not active"}), 400
        if action == "next":
            threading.Thread(target=ctrl._next_track, daemon=True).start()
        elif action == "prev":
            threading.Thread(target=ctrl._prev_track, daemon=True).start()
        elif action == "stop":
            threading.Thread(target=ctrl._stop_track, daemon=True).start()
        elif action.startswith("track"):
            try:
                idx = int(action.replace("track", "")) - 1
                threading.Thread(target=ctrl._play_index, args=(idx,), daemon=True).start()
            except ValueError:
                return jsonify({"status": "error", "msg": "invalid track"}), 400
        else:
            return jsonify({"status": "error", "msg": "unknown action"}), 400
        return jsonify({"status": "ok", "action": action})

    # ── Terminal toggle endpoints — FIX: always re-read from module globals ───
    @app.route("/terminal/toggle", methods=["POST"])
    def terminal_toggle():
        import utils.server as _self
        vis_fn  = _self._is_terminal_visible_fn
        min_fn  = _self._minimize_terminal_fn
        rest_fn = _self._restore_terminal_fn

        if vis_fn is None:
            return jsonify({"status": "error", "msg": "terminal control not ready"}), 503

        visible = vis_fn()
        if visible:
            if min_fn:
                min_fn()
            return jsonify({"status": "ok", "visible": False})
        else:
            if rest_fn:
                rest_fn()
            return jsonify({"status": "ok", "visible": True})

    @app.route("/terminal/show", methods=["POST"])
    def terminal_show():
        import utils.server as _self
        if _self._restore_terminal_fn:
            _self._restore_terminal_fn()
        return jsonify({"status": "ok", "visible": True})

    @app.route("/terminal/hide", methods=["POST"])
    def terminal_hide():
        import utils.server as _self
        if _self._minimize_terminal_fn:
            _self._minimize_terminal_fn()
        return jsonify({"status": "ok", "visible": False})
   
    # ─────────────────────────────────────────────────────────────────────────
    # Main UI
    # ─────────────────────────────────────────────────────────────────────────
    _html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>OS GOD</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;500;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
/* ── Reset ────────────────────────────────────────────────────────── */
*, *::before, *::after {{
  box-sizing: border-box; margin: 0; padding: 0;
  -webkit-tap-highlight-color: transparent;
}}

:root {{
  --bg:        #07070f;
  --bg2:       #0c0c18;
  --surface:   #10101e;
  --surface2:  #14142a;
  --border:    rgba(255,255,255,0.055);
  --border2:   rgba(255,255,255,0.11);
  --text:      #ebebff;
  --muted:     rgba(180,180,220,0.38);
  --font-head: 'Bebas Neue', sans-serif;
  --font-body: 'DM Sans', sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
  --r:         16px;
  --r-sm:      10px;
}}

html {{ background: var(--bg); }}

body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-body);
  min-height: 100dvh;
  overflow-x: hidden;
  padding-bottom: 80px;
}}

body::after {{
  content: '';
  position: fixed; inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  opacity: 0.028; pointer-events: none; z-index: 9998;
}}

.blob {{
  position: fixed; border-radius: 50%;
  filter: blur(90px); pointer-events: none; z-index: 0;
  transition: background 1.4s ease;
}}
#blob-a {{ width:320px; height:320px; top:-80px; left:-80px; background:#6b21ff; opacity:0.14; }}
#blob-b {{ width:260px; height:260px; bottom:60px; right:-70px; background:#00e5ff; opacity:0.09; }}

.page {{
  position: relative; z-index: 1;
  max-width: 460px; margin: 0 auto; padding: 0 15px;
}}

header {{
  padding: 44px 0 28px;
  display: flex; flex-direction: column; align-items: center;
}}

.logo-wrap {{
  display: flex; align-items: baseline; gap: 10px;
}}

.logo {{
  font-family: var(--font-head);
  font-size: 3.8rem; letter-spacing: 0.14em; line-height: 1;
  background: linear-gradient(160deg, #fff 40%, rgba(255,255,255,0.35));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}}

.logo-ver {{
  font-family: var(--font-mono);
  font-size: 0.58rem; color: var(--muted); letter-spacing: 0.15em;
}}

.logo-sub {{
  font-family: var(--font-mono);
  font-size: 0.56rem; letter-spacing: 0.38em;
  color: var(--muted); margin-top: 5px; text-transform: uppercase;
}}

.status-pill {{
  display: inline-flex; align-items: center; gap: 8px;
  background: var(--surface); border: 1px solid var(--border2);
  border-radius: 999px; padding: 7px 16px 7px 10px;
  margin-top: 18px;
  font-family: var(--font-mono); font-size: 0.65rem;
  letter-spacing: 0.14em; color: var(--muted);
}}

.s-dot {{
  width: 8px; height: 8px; border-radius: 50%;
  background: #252535; flex-shrink: 0;
  transition: background 0.5s, box-shadow 0.5s;
}}

#mode-text {{ color: var(--text); letter-spacing: 0.1em; }}

.slabel {{
  font-family: var(--font-mono); font-size: 0.58rem;
  letter-spacing: 0.3em; color: var(--muted);
  text-transform: uppercase; margin: 26px 0 11px;
  display: flex; align-items: center; gap: 10px;
}}
.slabel::after {{
  content: ''; flex: 1; height: 1px; background: var(--border);
}}

.modes-grid {{
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 9px;
}}

.mode-card {{
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0; cursor: pointer; overflow: hidden;
  display: flex; flex-direction: column;
  transition: border-color 0.28s, transform 0.14s;
  -webkit-user-select: none;
  min-height: 118px;
}}

.mode-card:active {{ transform: scale(0.955); }}

.mode-card.active {{
  border-color: var(--c);
  box-shadow: 0 0 0 1px rgba(var(--g),0.14), 0 4px 24px -8px rgba(var(--g),0.32);
}}

.card-glow {{
  position: absolute; inset: 0;
  background: radial-gradient(ellipse at 20% 80%, rgba(var(--g),0.20) 0%, transparent 65%);
  opacity: 0; transition: opacity 0.3s; pointer-events: none;
}}
.mode-card.active .card-glow,
.mode-card:active .card-glow {{ opacity: 1; }}

.card-body {{
  position: relative; z-index: 1;
  flex: 1; padding: 14px 13px 5px;
  display: flex; align-items: flex-start; gap: 10px;
}}

.card-emoji {{
  font-size: 1.55rem; line-height: 1; flex-shrink: 0;
  filter: drop-shadow(0 0 6px rgba(var(--g),0.5));
  transition: filter 0.3s;
}}
.mode-card.active .card-emoji {{
  filter: drop-shadow(0 0 10px rgba(var(--g),0.85));
}}

.card-info {{ display: flex; flex-direction: column; gap: 3px; }}

.card-tag {{
  font-family: var(--font-head);
  font-size: 1.12rem; letter-spacing: 0.08em;
  color: var(--text); line-height: 1;
}}

.card-desc {{
  font-size: 0.63rem; color: var(--muted); letter-spacing: 0.03em;
}}

.card-stats {{
  position: relative; z-index: 1;
  padding: 5px 13px 11px;
  display: flex; gap: 10px;
  font-family: var(--font-mono); font-size: 0.57rem;
  color: var(--muted);
}}

.card-bar {{
  position: absolute; bottom: 0; left: 0; right: 0;
  height: 2.5px; background: var(--c);
  transform: scaleX(0); transform-origin: left;
  transition: transform 0.32s cubic-bezier(0.34,1.56,0.64,1);
}}
.mode-card.active .card-bar {{ transform: scaleX(1); }}

.action-row {{
  display: flex; gap: 9px; margin-top: 10px;
}}

.action-btn {{
  flex: 1; padding: 17px 10px;
  border-radius: var(--r);
  font-family: var(--font-head); font-size: 1.05rem;
  letter-spacing: 0.1em; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 7px;
  border: 1.5px solid transparent;
  transition: transform 0.14s, background 0.15s;
  -webkit-user-select: none;
}}
.action-btn:active {{ transform: scale(0.955); }}

.btn-gg {{
  background: rgba(0,255,136,0.055); border-color: rgba(0,255,136,0.22); color: #00ff88;
}}
.btn-gg:active {{ background: rgba(0,255,136,0.16); }}

.btn-shutdown {{
  background: rgba(255,40,40,0.055); border-color: rgba(255,40,40,0.22); color: #ff4d4d;
}}
.btn-shutdown:active {{ background: rgba(255,40,40,0.16); }}

.btn-terminal {{
  background: rgba(123,97,255,0.055); border-color: rgba(123,97,255,0.22); color: #a07eff;
  font-size: 0.78rem; letter-spacing: 0.06em;
}}
.btn-terminal:active {{ background: rgba(123,97,255,0.16); }}

/* ── Volume Panel ─────────────────────────────────────────────────── */
.vol-panel {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 16px 18px;
}}

.vol-header {{
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 14px;
}}

.vol-label {{
  font-family: var(--font-mono); font-size: 0.62rem;
  letter-spacing: 0.18em; color: var(--muted); text-transform: uppercase;
}}

.vol-value {{
  font-family: var(--font-head); font-size: 1.3rem;
  letter-spacing: 0.08em; color: var(--text);
}}

.vol-slider-wrap {{
  position: relative; margin-bottom: 14px;
}}

.vol-slider {{
  -webkit-appearance: none;
  appearance: none;
  width: 100%; height: 6px;
  border-radius: 999px;
  background: linear-gradient(
    to right,
    #a07eff var(--pct, 50%),
    rgba(255,255,255,0.08) var(--pct, 50%)
  );
  outline: none; cursor: pointer;
}}

.vol-slider::-webkit-slider-thumb {{
  -webkit-appearance: none;
  width: 22px; height: 22px; border-radius: 50%;
  background: #a07eff;
  box-shadow: 0 0 10px rgba(160,126,255,0.6);
  cursor: pointer; transition: transform 0.1s;
}}
.vol-slider:active::-webkit-slider-thumb {{
  transform: scale(1.2);
}}

.vol-presets {{
  display: flex; gap: 7px;
}}

.vol-preset {{
  flex: 1; padding: 10px 4px;
  border-radius: var(--r-sm);
  background: rgba(160,126,255,0.06);
  border: 1px solid rgba(160,126,255,0.18);
  color: #a07eff;
  font-family: var(--font-mono); font-size: 0.65rem;
  letter-spacing: 0.06em; cursor: pointer;
  transition: all 0.14s; -webkit-user-select: none;
  text-align: center;
}}
.vol-preset:active {{
  background: rgba(160,126,255,0.22);
  transform: scale(0.95);
}}

#dj-panel {{
  display: none;
  background: linear-gradient(145deg, #140818 0%, #0b0b18 100%);
  border: 1px solid rgba(255,0,110,0.22);
  border-radius: var(--r); padding: 18px; margin-top: 9px;
}}
#dj-panel.visible {{
  display: block;
  animation: fadeSlide 0.3s ease;
}}
@keyframes fadeSlide {{
  from {{ opacity:0; transform:translateY(-8px); }}
  to   {{ opacity:1; transform:translateY(0); }}
}}

.dj-header {{
  display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;
}}
.dj-title {{
  font-family: var(--font-head); font-size: 1.15rem;
  letter-spacing: 0.15em; color: #ff006e;
}}
.dj-hint {{
  font-family: var(--font-mono); font-size: 0.54rem;
  color: rgba(255,0,110,0.4); letter-spacing: 0.08em;
}}

.dj-transport {{
  display: flex; justify-content: center; gap: 13px; margin-bottom: 15px;
}}

.dj-btn {{
  width: 56px; height: 56px; border-radius: 50%;
  background: rgba(255,0,110,0.07); border: 1.5px solid rgba(255,0,110,0.28);
  color: #ff006e; font-size: 1.2rem; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.14s; -webkit-user-select: none;
}}
.dj-btn:active {{ background: rgba(255,0,110,0.22); transform: scale(0.88); }}

.tracks-list {{ display: flex; flex-direction: column; gap: 6px; }}

.track-pill {{
  display: flex; align-items: center; gap: 11px;
  background: rgba(255,0,110,0.04); border: 1px solid rgba(255,0,110,0.13);
  border-radius: var(--r-sm); padding: 11px 14px;
  cursor: pointer; transition: all 0.14s; -webkit-user-select: none; width: 100%;
}}
.track-pill:active {{ background: rgba(255,0,110,0.16); transform: scale(0.985); }}

.track-num {{
  font-family: var(--font-mono); font-size: 0.62rem;
  color: rgba(255,0,110,0.45); min-width: 22px;
}}
.track-name {{ flex: 1; font-size: 0.82rem; color: var(--text); font-weight: 500; text-align: left; }}
.track-play {{ font-size: 0.65rem; color: rgba(255,0,110,0.35); }}

.speak-panel {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); padding: 14px;
  display: flex; flex-direction: column; gap: 9px;
}}

.speak-ta {{
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text);
  font-family: var(--font-body); font-size: 0.9rem;
  line-height: 1.55; padding: 11px 13px; resize: none; width: 100%;
  transition: border-color 0.2s;
}}
.speak-ta:focus {{ outline: none; border-color: rgba(123,97,255,0.45); }}
.speak-ta::placeholder {{ color: var(--muted); }}

.speak-btn {{
  background: rgba(123,97,255,0.09); border: 1.5px solid rgba(123,97,255,0.3);
  border-radius: var(--r-sm); color: #a07eff;
  font-family: var(--font-head); font-size: 0.95rem;
  letter-spacing: 0.12em; padding: 13px; cursor: pointer; transition: all 0.14s;
}}
.speak-btn:active {{ background: rgba(123,97,255,0.22); transform: scale(0.98); }}

.create-panel {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--r); overflow: hidden; margin-bottom: 16px;
}}

.create-summary {{
  display: flex; align-items: center; gap: 9px;
  padding: 15px 16px; cursor: pointer; list-style: none;
  font-family: var(--font-mono); font-size: 0.65rem;
  letter-spacing: 0.18em; color: var(--muted);
  text-transform: uppercase; user-select: none; transition: color 0.2s;
}}
.create-summary:hover {{ color: var(--text); }}
.create-summary::marker,
.create-summary::-webkit-details-marker {{ display: none; }}

.create-icon {{ transition: transform 0.25s; display: inline-block; }}
details[open] .create-icon {{ transform: rotate(45deg); }}

.create-form {{
  padding: 0 14px 16px; display: flex; flex-direction: column; gap: 0;
  border-top: 1px solid var(--border);
}}

.form-field {{ display: flex; flex-direction: column; gap: 4px; padding-top: 10px; }}

.field-label {{
  font-family: var(--font-mono); font-size: 0.56rem;
  letter-spacing: 0.2em; color: var(--muted); text-transform: uppercase;
}}

.form-input {{
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: var(--r-sm); color: var(--text);
  font-family: var(--font-body); font-size: 0.85rem;
  padding: 10px 13px; width: 100%; resize: none; transition: border-color 0.2s;
}}
.form-input:focus {{ outline: none; border-color: rgba(0,229,255,0.38); }}
.form-input::placeholder {{ color: var(--muted); }}

.form-row {{ display: flex; gap: 8px; padding-top: 10px; }}
.form-row .form-field {{ flex: 1; padding-top: 0; }}

.save-btn {{
  background: rgba(0,229,255,0.07); border: 1.5px solid rgba(0,229,255,0.26);
  border-radius: var(--r-sm); color: #00e5ff;
  font-family: var(--font-head); font-size: 0.95rem;
  letter-spacing: 0.12em; padding: 13px; cursor: pointer;
  margin-top: 12px; transition: all 0.14s;
}}
.save-btn:active {{ background: rgba(0,229,255,0.18); transform: scale(0.98); }}

.modal-overlay {{
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.85);
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
  display: flex; align-items: center; justify-content: center;
  z-index: 9997; opacity: 0; pointer-events: none; transition: opacity 0.22s;
}}
.modal-overlay.visible {{ opacity: 1; pointer-events: all; }}

.modal {{
  background: #0e0e1c; border: 1px solid rgba(255,40,40,0.28);
  border-radius: 22px; padding: 36px 26px;
  text-align: center; max-width: 290px; width: 90%;
  transform: scale(0.86) translateY(12px);
  transition: transform 0.26s cubic-bezier(0.34,1.4,0.64,1);
}}
.modal-overlay.visible .modal {{ transform: scale(1) translateY(0); }}

.modal-icon {{ font-size: 2.5rem; margin-bottom: 12px; }}
.modal-title {{
  font-family: var(--font-head); font-size: 1.7rem;
  letter-spacing: 0.1em; color: #ff4d4d; margin-bottom: 8px;
}}
.modal-body {{
  font-size: 0.8rem; color: var(--muted); line-height: 1.65; margin-bottom: 26px;
}}
.modal-btns {{ display: flex; gap: 9px; }}

.modal-cancel {{
  flex: 1; padding: 13px; border-radius: 11px;
  background: var(--surface2); border: 1px solid var(--border2);
  color: var(--text); font-family: var(--font-body); font-size: 0.88rem; cursor: pointer;
}}
.modal-cancel:active {{ background: #1e1e30; }}

.modal-confirm {{
  flex: 1; padding: 13px; border-radius: 11px;
  background: rgba(255,40,40,0.09); border: 1.5px solid rgba(255,60,60,0.55);
  color: #ff4d4d; font-family: var(--font-head); font-size: 1rem;
  letter-spacing: 0.08em; cursor: pointer;
}}
.modal-confirm:active {{ background: rgba(255,40,40,0.22); }}

#toast {{
  position: fixed; bottom: 32px; left: 50%;
  transform: translateX(-50%) translateY(18px);
  background: rgba(16,16,30,0.95);
  border: 1px solid var(--border2);
  color: var(--text); padding: 10px 22px; border-radius: 999px;
  font-family: var(--font-mono); font-size: 0.7rem; letter-spacing: 0.07em;
  opacity: 0; pointer-events: none; z-index: 9999; white-space: nowrap;
  backdrop-filter: blur(10px);
  transition: all 0.32s cubic-bezier(0.34,1.56,0.64,1);
}}
#toast.show {{ opacity: 1; transform: translateX(-50%) translateY(0); }}
</style>
</head>
<body>

<div class="blob" id="blob-a"></div>
<div class="blob" id="blob-b"></div>

<div class="page">

  <header>
    <div class="logo-wrap">
      <span class="logo">OS GOD</span>
      <span class="logo-ver">v1.0</span>
    </div>
    <div class="logo-sub">System Controller</div>
    <div class="status-pill">
      <span class="s-dot" id="mode-dot"></span>
      <span id="mode-text">INITIALIZING</span>
    </div>
  </header>

  <div class="slabel">Modes</div>
  <div class="modes-grid">
    {mode_cards_html}
  </div>

  <div id="dj-panel">
    <div class="dj-header">
      <div class="dj-title">🎧 DJ PAWNIN</div>
      <div class="dj-hint">CTRL+1–5 · CTRL+0 STOP</div>
    </div>
    <div class="dj-transport">
      <button class="dj-btn" onclick="djAction('prev')">⏮</button>
      <button class="dj-btn" onclick="djAction('stop')">⏹</button>
      <button class="dj-btn" onclick="djAction('next')">⏭</button>
    </div>
    <div class="tracks-list">
      {track_buttons_html}
    </div>
  </div>

  <div class="slabel">Volume</div>
  <div class="vol-panel">
    <div class="vol-header">
      <span class="vol-label">System Volume</span>
      <span class="vol-value" id="vol-display">50%</span>
    </div>
    <div class="vol-slider-wrap">
      <input class="vol-slider" id="vol-slider" type="range"
             min="0" max="100" value="50"
             oninput="onVolSlider(this.value)"
             onchange="sendVolume(this.value)">
    </div>
    <div class="vol-presets">
      <button class="vol-preset" onclick="setVolPreset(25)">25%</button>
      <button class="vol-preset" onclick="setVolPreset(40)">40%</button>
      <button class="vol-preset" onclick="setVolPreset(60)">60%</button>
      <button class="vol-preset" onclick="setVolPreset(80)">80%</button>
      <button class="vol-preset" onclick="setVolPreset(100)">MAX</button>
    </div>
  </div>

  <div class="slabel">Quick Actions</div>
  <div class="action-row">
    <button class="action-btn btn-gg"       onclick="triggerGG()">🏆 GG</button>
    <button class="action-btn btn-shutdown" onclick="showShutdownModal()">💀 SHUTDOWN</button>
  </div>
  <div class="action-row" style="margin-top:9px">
    <button class="action-btn btn-terminal" id="terminal-btn" onclick="toggleTerminal()">
      💻 TERMINAL
    </button>
  </div>

  <div class="slabel">Ask OS GOD</div>
  <div class="speak-panel">
    <textarea class="speak-ta" id="command-text"
              placeholder="Tell OS GOD what to do… e.g. 'activate zen for 30 min', 'open spotify and set volume to 20'"
              rows="2" maxlength="500"></textarea>
    <button class="speak-btn" onclick="sendCommand()">SEND</button>
  </div>

  <div class="slabel">Speak</div>
  <div class="speak-panel">
    <textarea class="speak-ta" id="speak-text"
              placeholder="Type something for OS GOD to say…"
              rows="2" maxlength="300"></textarea>
    <button class="speak-btn" onclick="sendSpeak()">SPEAK IT</button>
  </div>

  <div class="slabel">Customize</div>
  <details class="create-panel">
    <summary class="create-summary">
      <span class="create-icon">✦</span>
      Create New Mode
    </summary>
    <div class="create-form">
      <div class="form-row">
        <div class="form-field">
          <span class="field-label">Mode Name</span>
          <input class="form-input" id="cm-name" type="text" placeholder="chill state">
        </div>
        <div class="form-field">
          <span class="field-label">Volume %</span>
          <input class="form-input" id="cm-volume" type="number" placeholder="50" min="0" max="100" value="50">
        </div>
      </div>
      <div class="form-field">
        <span class="field-label">Wallpaper Engine ID</span>
        <input class="form-input" id="cm-wallpaper" type="text" placeholder="optional">
      </div>
      <div class="form-field">
        <span class="field-label">Apps to Open</span>
        <textarea class="form-input" id="cm-apps" placeholder="notion, discord, opera gx" rows="2"></textarea>
      </div>
      <div class="form-field">
        <span class="field-label">Websites</span>
        <textarea class="form-input" id="cm-websites" placeholder="https://…, https://…" rows="2"></textarea>
      </div>
      <div class="form-field">
        <span class="field-label">Apps to Close</span>
        <textarea class="form-input" id="cm-close" placeholder="whatsapp, notepad" rows="2"></textarea>
      </div>
      <button class="save-btn" onclick="saveMode()">SAVE MODE</button>
    </div>
  </details>

</div>

<div class="modal-overlay" id="shutdown-modal">
  <div class="modal">
    <div class="modal-icon">💀</div>
    <div class="modal-title">SHUTDOWN?</div>
    <div class="modal-body">Your PC will power off in 5 seconds.<br>This cannot be undone.</div>
    <div class="modal-btns">
      <button class="modal-cancel"  onclick="hideShutdownModal()">Cancel</button>
      <button class="modal-confirm" onclick="confirmShutdown()">CONFIRM</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
  const META = {mode_meta_js};

  // preserve token across all requests
  const _token = new URLSearchParams(window.location.search).get('token') || '';
  const _qs    = _token ? '?token=' + encodeURIComponent(_token) : '';

  let _tt;
  function showToast(msg) {{
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(_tt);
    _tt = setTimeout(() => t.classList.remove('show'), 2400);
  }}

  function activateMode(modeId) {{
    const name = modeId.replace(/-/g, ' ');
    const m = META[name] || {{}};
    showToast((m.emoji || '▶') + '  Activating ' + (m.tag || name) + '…');
    fetch('/activate/' + modeId + _qs, {{method:'POST'}})
      .catch(() => showToast('⚠  Connection error'));
  }}

  function djAction(action) {{
    fetch('/dj/' + action + _qs, {{method:'POST'}})
      .catch(() => showToast('⚠  DJ error'));
  }}

  function triggerGG() {{
    showToast('🏆  GG — well played!');
    fetch('/gg' + _qs, {{method:'POST'}})
      .catch(() => showToast('⚠  Connection error'));
  }}

  function showShutdownModal() {{
    document.getElementById('shutdown-modal').classList.add('visible');
  }}
  function hideShutdownModal() {{
    document.getElementById('shutdown-modal').classList.remove('visible');
  }}
  function confirmShutdown() {{
    hideShutdownModal();
    showToast('💀  Shutting down…');
    fetch('/shutdown' + _qs, {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{confirm:true}})
    }}).catch(() => showToast('⚠  Connection error'));
  }}
  document.getElementById('shutdown-modal').addEventListener('click', function(e) {{
    if (e.target === this) hideShutdownModal();
  }});

  // ── Terminal toggle — FIX: read actual state from server before toggling ──
  function toggleTerminal() {{
    fetch('/terminal/toggle' + _qs, {{method:'POST'}})
      .then(r => r.json())
      .then(d => {{
        updateTerminalBtn(d.visible);
        showToast(d.visible ? '💻  Terminal shown' : '💻  Terminal hidden');
      }})
      .catch(() => showToast('⚠  Connection error'));
  }}

  function updateTerminalBtn(visible) {{
    const btn = document.getElementById('terminal-btn');
    if (btn) btn.textContent = visible ? '💻 HIDE TERMINAL' : '💻 SHOW TERMINAL';
  }}

  function sendCommand() {{
    const el = document.getElementById('command-text');
    const text = el.value.trim();
    if (!text) {{ showToast('Type a command first'); return; }}
    fetch('/command' + _qs, {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{text}})
    }})
    .then(() => {{
      showToast('🧠  On it…');
      el.value = '';
    }})
    .catch(() => showToast('⚠  Connection error'));
  }}

  function sendSpeak() {{
    const text = document.getElementById('speak-text').value.trim();
    if (!text) {{ showToast('Type something first'); return; }}
    fetch('/speak' + _qs, {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{text}})
    }})
    .then(() => {{
      showToast('🗣  Speaking…');
      document.getElementById('speak-text').value = '';
    }})
    .catch(() => showToast('⚠  Connection error'));
  }}

  // ── Volume controls ───────────────────────────────────────────────────────
  let _volDebounce;

  function onVolSlider(val) {{
    val = parseInt(val);
    document.getElementById('vol-display').textContent = val + '%';
    // update slider fill
    document.getElementById('vol-slider').style.setProperty('--pct', val + '%');
  }}

  function sendVolume(val) {{
    val = parseInt(val);
    clearTimeout(_volDebounce);
    _volDebounce = setTimeout(() => {{
      fetch('/volume' + _qs, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{level: val}})
      }})
      .then(() => showToast('🔊  Volume → ' + val + '%'))
      .catch(() => showToast('⚠  Volume error'));
    }}, 150);
  }}

  function setVolPreset(val) {{
    const slider = document.getElementById('vol-slider');
    slider.value = val;
    onVolSlider(val);
    sendVolume(val);
  }}

  function saveMode() {{
    const name = document.getElementById('cm-name').value.trim();
    if (!name) {{ showToast('Enter a mode name'); return; }}
    fetch('/save-mode' + _qs, {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{
        name,
        volume:    document.getElementById('cm-volume').value    || '50',
        wallpaper: document.getElementById('cm-wallpaper').value || '',
        apps:      document.getElementById('cm-apps').value      || '',
        websites:  document.getElementById('cm-websites').value  || '',
        close:     document.getElementById('cm-close').value     || '',
      }})
    }})
    .then(r => r.json())
    .then(d => {{
      if (d.status === 'ok') {{
        showToast('✅  Mode saved: ' + d.mode);
        ['cm-name','cm-volume','cm-wallpaper','cm-apps','cm-websites','cm-close']
          .forEach(id => document.getElementById(id).value = '');
      }} else {{
        showToast('⚠  ' + d.msg);
      }}
    }})
    .catch(() => showToast('⚠  Connection error'));
  }}

  function pollStatus() {{
    fetch('/status' + _qs)
      .then(r => r.json())
      .then(data => {{
        const mode = data.current_mode;
        const m    = META[mode] || {{}};
        const col  = m.color || '#444466';
        const glow = m.glow  || '68,68,102';

        const dot = document.getElementById('mode-dot');
        dot.style.background = col;
        dot.style.boxShadow  = `0 0 8px 2px rgba(${{glow}},0.75)`;
        document.getElementById('mode-text').textContent = (m.tag || mode).toUpperCase();

        document.getElementById('blob-a').style.background = col;

        document.querySelectorAll('.mode-card').forEach(b => b.classList.remove('active'));
        const active = document.querySelector(`.mode-card[data-mode="${{mode}}"]`);
        if (active) active.classList.add('active');

        const djp = document.getElementById('dj-panel');
        if (data.dj_active) djp.classList.add('visible');
        else djp.classList.remove('visible');

        // update terminal button
        updateTerminalBtn(data.terminal_visible);

        // sync volume slider with system (only if user isn't dragging)
        if (data.volume !== undefined && document.activeElement !== document.getElementById('vol-slider')) {{
          const slider = document.getElementById('vol-slider');
          slider.value = data.volume;
          onVolSlider(data.volume);
        }}
      }})
      .catch(() => {{}});
  }}

  pollStatus();
  setInterval(pollStatus, 3000);
</script>
</body>
</html>"""

    @app.route("/")
    def index():
        return _html

    # ── Jarvis Dashboard ──────────────────────────────────────────────────────
    @app.route("/dashboard")
    def dashboard():
        path = _resource("templates/jarvis_dashboard.html")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return Response(f.read(), mimetype="text/html")
        except FileNotFoundError:
            return Response("<h1>Dashboard not found</h1><p>templates/jarvis_dashboard.html missing.</p>", 404, {"Content-Type": "text/html"})

    @app.route("/api/orbit")
    def api_orbit():
        try:
            from core import orbit_reader
            data = orbit_reader.fetch_orbit_summary()
            return jsonify(data)
        except Exception as e:
            logger.error(f"/api/orbit error: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    return app


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def start_server(route_fn, voice_fn=None) -> None:
    if not FLASK_AVAILABLE:
        print("⚠️  Flask not installed — run: pip install flask")
        return

    ip  = get_local_ip()
    app = create_app(route_fn, voice_fn=voice_fn)

    def _run():
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)

    threading.Thread(target=_run, daemon=True).start()

    print(f"\n{'─'*50}")
    print(f"  📱 Controller Server LIVE")
    print(f"  Open on iPad/Phone:")
    print(f"  http://{ip}:{PORT}/?token=pawnin2025")
    print(f"{'─'*50}\n")
    logger.info(f"Controller server at http://{ip}:{PORT}")
