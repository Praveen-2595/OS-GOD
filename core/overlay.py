"""
core/overlay.py — Jarvis Command Center

Interactive always-on-top frameless window. Replaces the terminal for daily use.
Right side of screen, vertically centered, 400×700 resizable.

Thread-safe communication:
  - bg threads   → _pending list (lock-protected) → Qt poll timer → feed widget
  - overlay input → _cmd_queue → main.py worker thread → process_command()
  - brain.py     → brain._response_hook = add_feed_message (set by main.py)
"""

from __future__ import annotations

import math
import queue
import time
import threading
import urllib.request
import json
from datetime import datetime
from loguru import logger

from PyQt6.QtWidgets import (
    QWidget, QApplication, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QScrollArea,
    QLabel, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt6.QtGui  import (
    QPainter, QColor, QPen, QFont, QFontMetrics,
    QTextCursor, QPolygon,
)


# ── Thread-safe API (importable from any module) ──────────────────────────────
_pending      : list[tuple[str, str, str]] = []   # (role, text, hh:mm)
_pending_lock = threading.Lock()
_cmd_queue    : queue.Queue[str] = queue.Queue()


def add_feed_message(role: str, text: str) -> None:
    """Thread-safe. Call from brain.py, main.py, or any thread."""
    if not text:
        return
    ts = datetime.now().strftime("%H:%M")
    with _pending_lock:
        _pending.append((role, text.strip(), ts))


def get_command_queue() -> queue.Queue[str]:
    """main.py worker reads commands from this queue."""
    return _cmd_queue


# ── Color palette ─────────────────────────────────────────────────────────────
_C_BG     = QColor(0x02, 0x08, 0x18)
_C_HDR    = QColor(0x0a, 0x16, 0x28)
_C_BORDER = QColor(6, 214, 245, 38)
_C_CYAN   = QColor(6, 214, 245)
_C_BLUE   = QColor(14, 165, 233)
_C_TEXT   = QColor(224, 242, 254)
_C_MUTED  = QColor(100, 116, 139)
_C_SAFE   = QColor(34, 197, 94)
_C_WARN   = QColor(245, 158, 11)
_C_DANGER = QColor(249, 115, 22)
_C_CRIT   = QColor(239, 68, 68)

_BAND_COLORS: dict[str, QColor] = {
    "safe":     _C_SAFE,
    "rising":   QColor(132, 204, 22),
    "warning":  _C_WARN,
    "danger":   _C_DANGER,
    "critical": _C_CRIT,
}

# role → (badge html, text hex color)
_ROLE_FMT: dict[str, tuple[str, str]] = {
    "user":   ('<b style="color:#06d6f5;">[YOU]</b>',    "#e0f2fe"),
    "jarvis": ('<b style="color:#7dd3fc;">[JARVIS]</b>', "#c7e8f9"),
    "orbit":  ('<b style="color:#fbbf24;">[ORBIT]</b>',  "#fbbf24"),
    "system": ('<b style="color:#475569;">[SYS]</b>',    "#64748b"),
}


def _font(size: int, bold: bool = False) -> QFont:
    f = QFont("Consolas", size)
    f.setBold(bold)
    return f


# ── Header widget ─────────────────────────────────────────────────────────────
class _Header(QWidget):
    """Draggable title bar with hex logo, mode badge, brain dot, window buttons."""

    minimize_clicked = pyqtSignal()
    close_clicked    = pyqtSignal()

    _H    = 44
    _BW   = 22   # window button width

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedHeight(self._H)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._drag_pos : QPoint | None = None
        self._mode      : str   = "—"
        self._brain     : bool  = False
        self._pulse     : float = 0.0
        self._mode_flash: float = 0.0

    # called from Qt main thread only
    def set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode_flash = 1.0
        self._mode = mode
        self.update()

    def set_brain(self, online: bool, pulse: float) -> None:
        self._brain = online
        self._pulse = pulse
        self.update()

    def decay_flash(self) -> None:
        if self._mode_flash > 0:
            self._mode_flash = max(0.0, self._mode_flash - 0.025)

    def paintEvent(self, _) -> None:   # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_C_HDR)
        p.drawRect(0, 0, W, H)

        # Subtle scanlines
        p.setBrush(QColor(6, 214, 245, 6))
        for i in range(0, H, 4):
            p.drawRect(0, i, W, 1)

        # Bottom border
        p.setPen(QPen(_C_BORDER, 1))
        p.drawLine(0, H - 1, W, H - 1)

        # ── Hex icon ──────────────────────────────────────────────────────────
        cx, cy, r = 20, H // 2, 10
        p.setPen(QPen(_C_CYAN, 1.5))
        p.setBrush(QColor(6, 214, 245, 22))
        pts = QPolygon([
            QPoint(int(cx + r * math.cos(math.radians(a))),
                   int(cy + r * math.sin(math.radians(a))))
            for a in range(30, 421, 60)
        ])
        p.drawPolygon(pts)

        # ── JARVIS title ──────────────────────────────────────────────────────
        p.setFont(_font(11, bold=True))
        p.setPen(_C_CYAN)
        p.drawText(38, 0, 75, H, Qt.AlignmentFlag.AlignVCenter, "JARVIS")

        # ── Mode badge ────────────────────────────────────────────────────────
        badge_x = 118
        badge_txt = self._mode[:14] if self._mode else "—"
        fm = QFontMetrics(_font(8))
        bw = fm.horizontalAdvance(badge_txt) + 16
        by = (H - 20) // 2
        alpha = max(30, int(self._mode_flash * 160))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(6, 214, 245, alpha))
        p.drawRoundedRect(badge_x, by, bw, 20, 10, 10)
        p.setFont(_font(8))
        p.setPen(_C_CYAN if self._mode_flash > 0.2 else _C_MUTED)
        p.drawText(badge_x, by, bw, 20, Qt.AlignmentFlag.AlignCenter, badge_txt)

        # ── Brain dot + label ─────────────────────────────────────────────────
        buttons_right = self._BW * 2 + 8
        dot_x = W - buttons_right - 58
        dot_r = 4
        dot_a = int(80 + 175 * self._pulse)
        dot_c = QColor(0x22, 0xc5, 0x5e, dot_a) if self._brain \
                else QColor(0xef, 0x44, 0x44, 210)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot_c)
        p.drawEllipse(dot_x, H // 2 - dot_r, dot_r * 2, dot_r * 2)
        p.setFont(_font(8))
        p.setPen(_C_MUTED)
        p.drawText(dot_x + dot_r * 2 + 4, 0, 44, H,
                   Qt.AlignmentFlag.AlignVCenter,
                   "BRAIN" if self._brain else "OFFLINE")

        # ── Window buttons (─  ×) ─────────────────────────────────────────────
        bx = W - self._BW * 2 - 4
        for label, color in [("─", _C_MUTED), ("×", QColor(239, 68, 68))]:
            p.setFont(_font(10))
            p.setPen(color)
            p.drawText(bx, 0, self._BW, H, Qt.AlignmentFlag.AlignCenter, label)
            bx += self._BW

        p.end()

    def _btn_rects(self) -> tuple:
        from PyQt6.QtCore import QRect
        W = self.width()
        mini  = QRect(W - self._BW * 2 - 4, 0, self._BW, self._H)
        close = QRect(W - self._BW     - 4, 0, self._BW, self._H)
        return mini, close

    def mousePressEvent(self, ev) -> None:   # type: ignore[override]
        if ev.button() == Qt.MouseButton.LeftButton:
            mini, close = self._btn_rects()
            p = ev.pos()
            if close.contains(p):
                self.close_clicked.emit()
                return
            if mini.contains(p):
                self.minimize_clicked.emit()
                return
            self._drag_pos = ev.globalPosition().toPoint() - self.window().pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None:    # type: ignore[override]
        if self._drag_pos is not None:
            self.window().move(ev.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, ev) -> None: # type: ignore[override]
        self._drag_pos = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)


# ── Orbit status bar ──────────────────────────────────────────────────────────
class _OrbitBar(QWidget):
    """Animated gravity bar + task/goal counts. All painting via QPainter."""

    _H = 52

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedHeight(self._H)
        self._gravity      = 0
        self._disp_gravity = 0.0
        self._band         = "unknown"
        self._tasks        = 0
        self._goals        = 0

    def set_data(self, gravity: int, band: str, tasks: int, goals: int) -> None:
        self._gravity = gravity
        self._band    = band.lower()
        self._tasks   = tasks
        self._goals   = goals
        self.update()

    def smooth_tick(self) -> None:
        diff = self._gravity - self._disp_gravity
        if abs(diff) > 0.2:
            self._disp_gravity += diff * 0.12
            self.update()
        elif self._disp_gravity != float(self._gravity):
            self._disp_gravity = float(self._gravity)
            self.update()

    def paintEvent(self, _) -> None:   # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        PAD  = 10

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(14, 165, 233, 8))
        p.drawRect(0, 0, W, H)

        p.setPen(QPen(_C_BORDER, 1))
        p.drawLine(0, H - 1, W, H - 1)

        band_c = _BAND_COLORS.get(self._band, _C_BLUE)
        y = 7

        # Gravity row
        p.setFont(_font(8))
        p.setPen(_C_MUTED)
        p.drawText(PAD, y, 58, 15, Qt.AlignmentFlag.AlignVCenter, "GRAVITY")
        p.setFont(_font(9, bold=True))
        p.setPen(band_c)
        p.drawText(PAD + 58, y, W - PAD * 2 - 58, 15,
                   Qt.AlignmentFlag.AlignVCenter,
                   f"{self._gravity}%  {self._band.upper()}")
        y += 17

        # Progress bar
        bw, bh = W - PAD * 2, 5
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 12))
        p.drawRoundedRect(PAD, y, bw, bh, 2, 2)
        fill = int(bw * max(0.0, min(100.0, self._disp_gravity)) / 100.0)
        if fill > 0:
            p.setBrush(band_c)
            p.drawRoundedRect(PAD, y, fill, bh, 2, 2)
        y += bh + 6

        # Tasks + Goals
        p.setFont(_font(8))
        for label, val, off in [("TASKS", self._tasks, 0), ("GOALS", self._goals, 130)]:
            p.setPen(_C_MUTED)
            p.drawText(PAD + off, y, 48, 13, Qt.AlignmentFlag.AlignVCenter, label)
            p.setPen(_C_TEXT)
            p.drawText(PAD + off + 48, y, 70, 13,
                       Qt.AlignmentFlag.AlignVCenter, str(val))

        p.end()


# ── Stylesheets ───────────────────────────────────────────────────────────────
_SS_FEED = """
QTextEdit {
    background-color: #020818;
    color: #e0f2fe;
    border: none;
    font-family: Consolas;
    font-size: 9px;
    padding: 8px 10px;
    selection-background-color: rgba(6,214,245,0.2);
}
QScrollBar:vertical {
    background: #020818; width: 6px; margin: 0;
}
QScrollBar::handle:vertical {
    background: rgba(6,214,245,0.22); border-radius: 3px; min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

_SS_INPUT = """
QLineEdit {
    background-color: #0a1628;
    color: #e0f2fe;
    border: 1px solid rgba(6,214,245,0.18);
    border-radius: 4px;
    font-family: Consolas;
    font-size: 10px;
    padding: 6px 8px;
    selection-background-color: rgba(6,214,245,0.25);
}
QLineEdit:focus {
    border: 1px solid rgba(6,214,245,0.55);
    background-color: #0d1f38;
}
"""

_SS_SEND = """
QPushButton {
    background-color: rgba(6,214,245,0.08);
    color: #06d6f5;
    border: 1px solid rgba(6,214,245,0.3);
    border-radius: 4px;
    font-family: Consolas;
    font-size: 9px;
    font-weight: bold;
    padding: 6px 10px;
}
QPushButton:hover { background-color: rgba(6,214,245,0.18); }
QPushButton:pressed { background-color: rgba(6,214,245,0.3); }
"""

_SS_MIC = """
QPushButton {
    background-color: rgba(14,165,233,0.06);
    color: #475569;
    border: 1px solid rgba(14,165,233,0.18);
    border-radius: 4px;
    font-size: 13px;
    padding: 4px 6px;
}
QPushButton:hover { background-color: rgba(14,165,233,0.15); color: #0ea5e9; }
QPushButton[active="true"] {
    background-color: rgba(14,165,233,0.28);
    color: #0ea5e9;
    border-color: #0ea5e9;
}
"""

_SS_MODE_BTN = """
QPushButton {
    background-color: rgba(14,165,233,0.05);
    color: #64748b;
    border: 1px solid rgba(6,214,245,0.1);
    border-radius: 10px;
    font-family: Consolas;
    font-size: 8px;
    padding: 4px 10px;
    min-width: 38px;
}
QPushButton:hover {
    background-color: rgba(6,214,245,0.12);
    color: #06d6f5;
    border-color: rgba(6,214,245,0.4);
}
QPushButton[active="true"] {
    background-color: rgba(6,214,245,0.18);
    color: #06d6f5;
    border: 1px solid #06d6f5;
}
"""

_SS_ACTIONS = """
QScrollArea { background: transparent; border: none; }
QWidget#inner { background: transparent; }
QScrollBar:horizontal {
    background: #020818; height: 3px;
}
QScrollBar::handle:horizontal {
    background: rgba(6,214,245,0.2); border-radius: 1px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


# ── Main Command Center ────────────────────────────────────────────────────────
class JarvisCommandCenter(QWidget):
    """Jarvis Command Center — interactive always-on-top window."""

    MAX_LINES = 50

    def __init__(
        self,
        config: dict | None = None,
        on_wake_fn=None,
    ) -> None:
        super().__init__()
        cfg              = config or {}
        self._cfg        = cfg.get("overlay", {}) or {}
        self._on_wake    = on_wake_fn
        self._mic_active = False
        self._mic_done   = False
        self._modes      = list((cfg.get("modes", {})) or {})
        self._mode_btns  : list[QPushButton] = []
        self._feed_lines = 0

        # Data staging area — written by bg thread, consumed in Qt anim tick
        self._data_brain : bool            = False
        self._data_mode  : str             = "—"
        self._data_orbit : tuple | None    = None   # (gravity, band, tasks, goals)

        self._setup_window()
        self._build_ui()
        self._setup_timers()
        self._fetch_data()
        add_feed_message("system", "Jarvis Command Center online.")

    # ── window ────────────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumSize(350, 500)
        self.resize(400, 700)
        self.setWindowOpacity(0.97)
        self.setStyleSheet("background-color: #020818;")

        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + screen.width() - self.width() - 10
        y = screen.y() + (screen.height() - self.height()) // 2
        self.move(x, max(screen.y() + 4, y))

    def paintEvent(self, _) -> None:   # type: ignore[override]
        p = QPainter(self)
        p.setPen(QPen(_C_BORDER, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        p.end()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        self._header = _Header(self)
        self._header.minimize_clicked.connect(self._do_minimize)
        self._header.close_clicked.connect(self.hide)
        root.addWidget(self._header)

        # Orbit bar
        self._orbit = _OrbitBar(self)
        root.addWidget(self._orbit)

        # Live feed
        self._feed = QTextEdit(self)
        self._feed.setReadOnly(True)
        self._feed.setStyleSheet(_SS_FEED)
        self._feed.setFont(_font(9))
        root.addWidget(self._feed, stretch=1)

        # Quick actions
        root.addWidget(self._build_actions())

        # Input bar
        root.addWidget(self._build_input())

    def _build_actions(self) -> QWidget:
        wrap = QWidget()
        wrap.setFixedHeight(40)
        wrap.setStyleSheet(
            "background-color:#020818;"
            "border-top:1px solid rgba(6,214,245,0.08);"
        )

        scroll = QScrollArea()
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(_SS_ACTIONS)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)

        inner = QWidget()
        inner.setObjectName("inner")
        row = QHBoxLayout(inner)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)

        for mode in self._modes:
            lbl = mode.upper().replace(" STATE", "").replace(" MODE", "")
            btn = QPushButton(lbl)
            btn.setStyleSheet(_SS_MODE_BTN)
            btn.clicked.connect(lambda _checked, m=mode: self._activate_mode(m))
            self._mode_btns.append(btn)
            row.addWidget(btn)
        row.addStretch()

        scroll.setWidget(inner)

        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
        return wrap

    def _build_input(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet(
            "background-color:#0a1628;"
            "border-top:1px solid rgba(6,214,245,0.12);"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)

        prompt = QLabel(">")
        prompt.setStyleSheet(
            "color:#06d6f5; font-family:Consolas; font-size:11px;"
            "font-weight:bold; background:transparent;"
        )
        prompt.setFixedWidth(14)
        row.addWidget(prompt)

        self._input = QLineEdit()
        self._input.setStyleSheet(_SS_INPUT)
        self._input.setFont(_font(10))
        self._input.setPlaceholderText("speak or type a command...")
        self._input.returnPressed.connect(self._send_command)
        row.addWidget(self._input, stretch=1)

        send = QPushButton("SEND")
        send.setStyleSheet(_SS_SEND)
        send.setFixedWidth(52)
        send.clicked.connect(self._send_command)
        row.addWidget(send)

        self._mic_btn = QPushButton("🎤")
        self._mic_btn.setStyleSheet(_SS_MIC)
        self._mic_btn.setFixedWidth(36)
        self._mic_btn.clicked.connect(self._toggle_mic)
        row.addWidget(self._mic_btn)

        return bar

    # ── timers ────────────────────────────────────────────────────────────────

    def _setup_timers(self) -> None:
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_timer.start(33)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_messages)
        self._poll_timer.start(100)

        interval_ms = int(self._cfg.get("update_interval", 30)) * 1000
        self._data_timer = QTimer(self)
        self._data_timer.timeout.connect(self._fetch_data)
        self._data_timer.start(interval_ms)

    def _anim_tick(self) -> None:
        pulse = (math.sin(time.time() * 3.0) + 1.0) / 2.0
        self._header.set_brain(self._data_brain, pulse)
        self._header.decay_flash()
        self._orbit.smooth_tick()

        # Consume pending orbit data (written by bg thread)
        if self._data_orbit is not None:
            self._orbit.set_data(*self._data_orbit)
            self._data_orbit = None

        # Consume pending mode update
        if self._data_mode != self._header._mode:
            self._header.set_mode(self._data_mode)

    # ── data fetch (background thread) ────────────────────────────────────────

    def _fetch_data(self) -> None:
        threading.Thread(target=self._fetch_thread, daemon=True).start()

    def _fetch_thread(self) -> None:
        # Orbit — in-process
        try:
            from core import orbit_reader
            s = orbit_reader.fetch_orbit_summary()
            if s.get("ok"):
                goals = len(s.get("goals") or [])
                self._data_orbit = (
                    int(s.get("score", 0)),
                    (s.get("band") or "unknown"),
                    int(s.get("pendingCount", 0)),
                    goals,
                )
                band = (s.get("band") or "").lower()
                if band in ("warning", "danger", "critical"):
                    add_feed_message(
                        "orbit",
                        f"Gravity {s.get('score')}% — {band.upper()}. "
                        f"{s.get('pendingCount', 0)} tasks pending."
                    )
        except Exception as e:
            logger.debug(f"overlay: orbit: {e}")

        # Brain / mode from Flask /status
        try:
            with urllib.request.urlopen(
                "http://localhost:5000/status?token=pawnin2025", timeout=1
            ) as r:
                data = json.loads(r.read())
            self._data_brain = True
            self._data_mode  = (data.get("current_mode") or "—").upper()
        except Exception:
            self._data_brain = False

    # ── feed ──────────────────────────────────────────────────────────────────

    def _poll_messages(self) -> None:
        # Reset mic button if voice capture finished
        if self._mic_done:
            self._mic_done = False
            self._mic_btn.setProperty("active", "false")
            self._mic_btn.setStyle(self._mic_btn.style())
            self._mic_active = False

        with _pending_lock:
            msgs = _pending[:]
            _pending.clear()
        for role, text, ts in msgs:
            self._append(role, text, ts)

    def _append(self, role: str, text: str, ts: str) -> None:
        # Prune excess lines
        if self._feed_lines >= self.MAX_LINES:
            cur = self._feed.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor, 8
            )
            cur.removeSelectedText()
            self._feed_lines = max(0, self._feed_lines - 8)

        badge, txt_color = _ROLE_FMT.get(
            role,
            ('<b style="color:#475569;">[?]</b>', "#64748b")
        )
        html = (
            f'<span style="color:#1e3a5f;">{ts}</span> '
            f'{badge} '
            f'<span style="color:{txt_color};">'
            f'{text.replace("<", "&lt;").replace(">", "&gt;")}'
            f'</span><br>'
        )
        self._feed.moveCursor(QTextCursor.MoveOperation.End)
        self._feed.insertHtml(html)
        sb = self._feed.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._feed_lines += 1

    # ── interaction ───────────────────────────────────────────────────────────

    def _send_command(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        add_feed_message("user", text)
        _cmd_queue.put(text)

    def _toggle_mic(self) -> None:
        if self._mic_active or self._on_wake is None:
            return
        self._mic_active = True
        self._mic_btn.setProperty("active", "true")
        self._mic_btn.setStyle(self._mic_btn.style())

        def _listen() -> None:
            try:
                self._on_wake()
            finally:
                self._mic_done = True   # poll timer will reset button in Qt thread

        threading.Thread(target=_listen, daemon=True).start()

    def _activate_mode(self, mode: str) -> None:
        _cmd_queue.put(f"activate {mode}")
        add_feed_message("user", f"activate {mode}")

    def _do_minimize(self) -> None:
        self.showMinimized()

    # ── public API (called from main.py) ──────────────────────────────────────

    def toggle(self) -> None:
        """Alt+H handler."""
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show()
            self.showNormal()
            self.raise_()
            self._fetch_data()

    def notify_mode_change(self, mode: str) -> None:
        """Call from main.py set_mode() so header badge + feed stay in sync."""
        self._data_mode = mode.upper()
        add_feed_message("system", f"Mode → {mode}")
        # Refresh active highlight on mode buttons
        for btn in self._mode_btns:
            is_active = (
                btn.text().lower() in mode.lower()
                or mode.lower().startswith(btn.text().lower())
            )
            btn.setProperty("active", "true" if is_active else "false")
            btn.setStyle(btn.style())
