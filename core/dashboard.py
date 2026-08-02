"""
core/dashboard.py — JARVIS Command Center (native PyQt6 desktop app).

A standalone Windows app that replaces the HTML dashboard. It connects over
HTTP to a running OS GOD Flask server (default http://localhost:5000) and gives
a full dark-themed control surface: live AI-core animation, gravity meter,
goals/tasks panels, an intel feed, quick mode buttons and a command bar.

Launch:  python launch_dashboard.py     (OS GOD must already be running)

All network I/O happens off the UI thread (DataFetcher QThread + per-command
daemon threads that emit Qt signals), so the UI never blocks. If the server is
down it shows an OFFLINE banner and keeps retrying every 10s.

Server contract (see utils/server.py):
  GET  /status?token=…        → {current_mode, dj_active, terminal_visible, volume}
  GET  /api/orbit?token=…     → {ok, score, band, pendingCount, tasks[], goals[]}
  POST /command  (X-Token)    → {"text": "<natural language>"}
"""

from __future__ import annotations

import os
import sys
import time
import math
import threading
from datetime import datetime, timedelta

try:
    import requests
except ImportError:                      # pragma: no cover
    requests = None

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QFrame, QPushButton,
    QLineEdit, QTextEdit, QScrollArea, QProgressBar, QCheckBox,
    QVBoxLayout, QHBoxLayout, QGridLayout,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QRadialGradient,
)


# ── Connection config ─────────────────────────────────────────────────────────
BASE_URL = os.environ.get("OSGOD_URL", "http://localhost:5000").rstrip("/")
TOKEN    = os.environ.get("OSGOD_TOKEN", "pawnin2025")
HEADERS  = {"X-Token": TOKEN, "Content-Type": "application/json"}

STATUS_INTERVAL = 10   # seconds between /status fetches
ORBIT_INTERVAL  = 30   # seconds between /api/orbit fetches


# ── Palette ───────────────────────────────────────────────────────────────────
C_BG     = "#000510"
C_PANEL  = "#060f1e"
C_BORDER = "#0a2a4a"
C_CYAN   = "#00d4ff"
C_BLUE   = "#0066ff"
C_GREEN  = "#00ff88"
C_AMBER  = "#ffaa00"
C_RED    = "#ff3344"
C_TEXT   = "#c0e8ff"
C_MUTED  = "#2a4a6a"

BAND_COLORS = {
    "safe":     C_GREEN,
    "rising":   "#9ee600",
    "warning":  C_AMBER,
    "danger":   "#ff7733",
    "critical": C_RED,
}

# Quick-action buttons → exact config mode names
MODE_BUTTONS = [
    ("ALPHA",     "alpha state"),
    ("ZEN",       "zen state"),
    ("GAMING",    "gaming state"),
    ("DJ",        "dj pawnin state"),
    ("DEVELOPER", "developer mode"),
    ("GG",        "gg"),
]

# Feed tag → colour
FEED_COLORS = {
    "INFO":   C_CYAN,
    "WARN":   C_AMBER,
    "JARVIS": "#4d9fff",
    "ORBIT":  C_GREEN,
    "SYSTEM": "#5a7a9a",
    "USER":   C_CYAN,
    "ERROR":  C_RED,
}


def _font(size: int, bold: bool = False, family: str = "Consolas") -> QFont:
    f = QFont(family, size)
    f.setBold(bold)
    return f


# ─────────────────────────────────────────────────────────────────────────────
# Background data fetcher — all network polling lives here, off the UI thread
# ─────────────────────────────────────────────────────────────────────────────
class DataFetcher(QThread):
    status_ready       = pyqtSignal(dict)
    orbit_ready        = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool)   # True=online, False=offline

    def __init__(self) -> None:
        super().__init__()
        self._running          = True
        self._connected: bool | None = None
        self._command_sent_at: float = 0.0

    def notify_command_sent(self) -> None:
        self._command_sent_at = time.time()

    def run(self) -> None:
        last_status = 0.0
        last_orbit  = 0.0
        while self._running:
            now = time.monotonic()
            if now - last_status >= STATUS_INTERVAL or last_status == 0:
                last_status = now
                self._poll_status()
            if now - last_orbit >= ORBIT_INTERVAL or last_orbit == 0:
                last_orbit = now
                self._poll_orbit()
            # Sleep in short slices so stop() is responsive.
            for _ in range(10):
                if not self._running:
                    break
                self.msleep(100)

    def _poll_status(self) -> None:
        if time.time() - self._command_sent_at < 45:
            return   # command in flight — keep last known state, don't flip to OFFLINE
        if requests is None:
            self._set_connected(False)
            return
        try:
            r = requests.get(f"{BASE_URL}/status", headers=HEADERS, timeout=4)
            r.raise_for_status()
            self._set_connected(True)
            self.status_ready.emit(r.json())
        except Exception:
            self._set_connected(False)

    def _poll_orbit(self) -> None:
        if requests is None:
            return
        try:
            r = requests.get(f"{BASE_URL}/api/orbit", headers=HEADERS, timeout=6)
            r.raise_for_status()
            self.orbit_ready.emit(r.json())
        except Exception:
            pass   # orbit is best-effort; connection state is driven by /status

    def _set_connected(self, ok: bool) -> None:
        if ok != self._connected:
            self._connected = ok
            self.connection_changed.emit(ok)

    def stop(self) -> None:
        self._running = False


# ─────────────────────────────────────────────────────────────────────────────
# Animated AI core — concentric rotating rings + orbiting dots + pulsing centre
# ─────────────────────────────────────────────────────────────────────────────
class AICore(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(280, 280)
        self._angle  = 0.0
        self._online = False
        self._timer  = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)   # ~30 fps

    def set_online(self, online: bool) -> None:
        self._online = online

    def _tick(self) -> None:
        self._angle += 0.5
        self.update()

    def paintEvent(self, _event) -> None:   # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2

        base   = QColor(C_CYAN) if self._online else QColor(C_MUTED)
        accent = QColor(C_BLUE) if self._online else QColor("#1a3050")

        # ── Inner glow (stacked translucent radial circles) ──────────────────
        pulse = (math.sin(math.radians(self._angle * 4)) + 1) / 2   # 0..1
        glow = QRadialGradient(QPointF(cx, cy), 120)
        g0 = QColor(base); g0.setAlpha(int(60 + 50 * pulse))
        g1 = QColor(base); g1.setAlpha(0)
        glow.setColorAt(0.0, g0)
        glow.setColorAt(1.0, g1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), 120, 120)

        # ── 4 concentric rings rotating at different speeds ──────────────────
        radii  = [118, 96, 72, 50]
        speeds = [1.0, -1.7, 2.4, -3.0]
        spans  = [70, 110, 60, 140]
        for r, spd, span in zip(radii, speeds, spans):
            pen = QPen(QColor(base), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            start = int((self._angle * spd) % 360)
            # Two opposing arcs per ring for a HUD look (Qt angles are 1/16°).
            rect = (int(cx - r), int(cy - r), int(r * 2), int(r * 2))
            p.drawArc(*rect, start * 16, span * 16)
            p.drawArc(*rect, (start + 180) * 16, span * 16)

        # ── 8 orbiting dots on the outer ring ────────────────────────────────
        orbit_r = 130
        for i in range(8):
            a = math.radians(self._angle * 1.3 + i * 45)
            dx = cx + orbit_r * math.cos(a)
            dy = cy + orbit_r * math.sin(a)
            dot = QColor(accent if i % 2 else base)
            dot.setAlpha(220)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot)
            p.drawEllipse(QPointF(dx, dy), 3.2, 3.2)

        # ── Pulsing centre circle ────────────────────────────────────────────
        cr = 16 + 5 * pulse
        core = QColor(base)
        core.setAlpha(int(150 + 90 * pulse))
        p.setBrush(core)
        p.setPen(QPen(QColor(base), 1))
        p.drawEllipse(QPointF(cx, cy), cr, cr)

        # Centre cross-hair tick
        p.setPen(QPen(QColor(C_TEXT), 1))
        p.drawLine(int(cx - 6), int(cy), int(cx + 6), int(cy))
        p.drawLine(int(cx), int(cy - 6), int(cx), int(cy + 6))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Gravity arc meter — big number + arc gauge + band label
# ─────────────────────────────────────────────────────────────────────────────
class GravityMeter(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self._value      = 0          # target 0..100
        self._disp       = 0.0        # animated value
        self._band       = "—"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_value(self, value: int, band: str) -> None:
        self._value = max(0, min(100, int(value)))
        self._band  = (band or "—")

    def _tick(self) -> None:
        diff = self._value - self._disp
        if abs(diff) > 0.3:
            self._disp += diff * 0.1
            self.update()
        elif self._disp != float(self._value):
            self._disp = float(self._value)
            self.update()

    def paintEvent(self, _event) -> None:   # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx   = w / 2
        cy   = h - 18
        r    = min(w / 2 - 20, h - 40)

        color = QColor(BAND_COLORS.get(self._band.lower(), C_CYAN))
        rect  = (int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # 180° background track
        p.setPen(QPen(QColor(C_BORDER), 8, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(*rect, 180 * 16, -180 * 16)

        # Value arc
        p.setPen(QPen(color, 8, cap=Qt.PenCapStyle.RoundCap))
        span = int(-180 * (self._disp / 100.0))
        p.drawArc(*rect, 180 * 16, span * 16)

        # Big number
        p.setPen(QColor(C_TEXT))
        p.setFont(_font(42, bold=True))
        p.drawText(int(cx - r), int(cy - r * 0.75), int(r * 2), int(r),
                   Qt.AlignmentFlag.AlignCenter, f"{int(self._disp)}")

        # % + band label
        p.setPen(color)
        p.setFont(_font(11, bold=True))
        p.drawText(0, int(cy - 14), w, 18,
                   Qt.AlignmentFlag.AlignCenter, self._band.upper())
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Small panel helpers
# ─────────────────────────────────────────────────────────────────────────────
def _panel(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Panel")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    if title:
        lbl = QLabel(title)
        lbl.setObjectName("PanelTitle")
        lbl.setFont(_font(8, bold=True))
        lay.addWidget(lbl)
    return frame, lay


def _kv_row(key: str) -> tuple[QHBoxLayout, QLabel]:
    row = QHBoxLayout()
    row.setSpacing(6)
    k = QLabel(key)
    k.setObjectName("KvKey")
    k.setFont(_font(9))
    v = QLabel("—")
    v.setFont(_font(9, bold=True))
    v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(k)
    row.addStretch()
    row.addWidget(v)
    return row, v


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────
class Dashboard(QMainWindow):
    # Emitted from worker threads → routed to the UI thread (queued connection).
    sig_feed = pyqtSignal(str, str)   # (tag, message)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("JARVIS Command Center")
        self.resize(1200, 750)
        self.setMinimumSize(980, 620)

        self._connected   = False
        self._current_mode = "None"
        self._start_time   = time.time()

        self.sig_feed.connect(self._add_feed)

        self._build_ui()
        self._start_clock()
        self._start_fetcher()

        self._add_feed("SYSTEM", "Command Center initialised.")

    # ── UI construction ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._build_header())
        root.addWidget(self._build_offline_banner())

        body = QGridLayout()
        body.setSpacing(12)
        body.addWidget(self._build_left(),   0, 0)
        body.addWidget(self._build_center(), 0, 1)
        body.addWidget(self._build_right(),  0, 2)
        body.setColumnStretch(0, 0)
        body.setColumnStretch(1, 1)
        body.setColumnStretch(2, 0)
        root.addLayout(body, stretch=1)

        root.addWidget(self._build_bottom_bar())

    # ── Header ──────────────────────────────────────────────────────────────
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Header")
        bar.setFixedHeight(58)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)

        logo = QLabel("⬡ JARVIS // OS GOD")
        logo.setObjectName("Logo")
        logo.setFont(_font(14, bold=True))
        lay.addWidget(logo)

        lay.addStretch()
        self._clock = QLabel("--:--:--")
        self._clock.setObjectName("Clock")
        self._clock.setFont(_font(16, bold=True))
        lay.addWidget(self._clock)
        lay.addStretch()

        self._sys_badge = QLabel("● CONNECTING")
        self._sys_badge.setObjectName("SysBadge")
        self._sys_badge.setFont(_font(9, bold=True))
        lay.addWidget(self._sys_badge)
        return bar

    def _build_offline_banner(self) -> QWidget:
        self._banner = QLabel("● OFFLINE — Start OS GOD first   (reconnecting every 10s…)")
        self._banner.setObjectName("Banner")
        self._banner.setFont(_font(10, bold=True))
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._banner.setFixedHeight(30)
        self._banner.hide()
        return self._banner

    # ── Left column ────────────────────────────────────────────────────────
    def _build_left(self) -> QWidget:
        col = QWidget()
        col.setFixedWidth(280)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # System status panel
        panel, pl = _panel("SYSTEM STATUS")
        r1, self._lbl_brain = _kv_row("BRAIN")
        r2, self._lbl_mode  = _kv_row("MODE")
        r3, self._lbl_uptime = _kv_row("SESSION")
        r4, self._lbl_wake  = _kv_row("WAKE WORD")
        for r in (r1, r2, r3, r4):
            pl.addLayout(r)
        lay.addWidget(panel)

        # Goals panel (scrollable)
        gpanel, gpl = _panel("ACTIVE GOALS")
        self._goals_area = QScrollArea()
        self._goals_area.setWidgetResizable(True)
        self._goals_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._goals_inner = QWidget()
        self._goals_layout = QVBoxLayout(self._goals_inner)
        self._goals_layout.setContentsMargins(0, 0, 0, 0)
        self._goals_layout.setSpacing(10)
        self._goals_layout.addStretch()
        self._goals_area.setWidget(self._goals_inner)
        gpl.addWidget(self._goals_area)
        lay.addWidget(gpanel, stretch=1)

        return col

    # ── Center column ─────────────────────────────────────────────────────────
    def _build_center(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # AI core
        core_wrap = QFrame()
        core_wrap.setObjectName("Panel")
        cwl = QVBoxLayout(core_wrap)
        cwl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ai_core = AICore()
        cwl.addWidget(self._ai_core, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(core_wrap)

        # Gravity meter
        gpanel, gpl = _panel("GRAVITY")
        self._gravity = GravityMeter()
        gpl.addWidget(self._gravity)
        lay.addWidget(gpanel)

        # Quick mode buttons
        mpanel, mpl = _panel("QUICK MODES")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._mode_btns: dict[str, QPushButton] = {}
        for label, mode in MODE_BUTTONS:
            btn = QPushButton(label)
            btn.setObjectName("ModeBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(False)
            btn.clicked.connect(lambda _checked, m=mode, l=label: self._activate_mode(m, l))
            self._mode_btns[mode] = btn
            row.addWidget(btn)
        mpl.addLayout(row)
        lay.addWidget(mpanel)

        lay.addStretch()
        return col

    # ── Right column ──────────────────────────────────────────────────────────
    def _build_right(self) -> QWidget:
        col = QWidget()
        col.setFixedWidth(280)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # Live intel feed
        fpanel, fpl = _panel("LIVE INTEL FEED")
        self._feed = QTextEdit()
        self._feed.setReadOnly(True)
        self._feed.setObjectName("Feed")
        fpl.addWidget(self._feed)
        lay.addWidget(fpanel, stretch=1)

        # Pending tasks
        tpanel, tpl = _panel(None)
        thead = QHBoxLayout()
        tlabel = QLabel("PENDING TASKS")
        tlabel.setObjectName("PanelTitle")
        tlabel.setFont(_font(8, bold=True))
        self._task_badge = QLabel("0")
        self._task_badge.setObjectName("Badge")
        self._task_badge.setFont(_font(8, bold=True))
        thead.addWidget(tlabel)
        thead.addStretch()
        thead.addWidget(self._task_badge)
        tpl.addLayout(thead)

        self._tasks_area = QScrollArea()
        self._tasks_area.setWidgetResizable(True)
        self._tasks_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tasks_inner = QWidget()
        self._tasks_layout = QVBoxLayout(self._tasks_inner)
        self._tasks_layout.setContentsMargins(0, 0, 0, 0)
        self._tasks_layout.setSpacing(4)
        self._tasks_layout.addStretch()
        self._tasks_area.setWidget(self._tasks_inner)
        tpl.addWidget(self._tasks_area)
        lay.addWidget(tpanel, stretch=1)

        return col

    # ── Bottom command bar ─────────────────────────────────────────────────────
    def _build_bottom_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Panel")
        bar.setFixedHeight(56)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(10)

        self._last_response = QLabel("Awaiting input…")
        self._last_response.setObjectName("LastResp")
        self._last_response.setFont(_font(9))
        self._last_response.setFixedWidth(260)
        self._last_response.setWordWrap(False)
        lay.addWidget(self._last_response)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Command Jarvis…")
        self._input.setFont(_font(10))
        self._input.returnPressed.connect(self._send_from_input)
        lay.addWidget(self._input, stretch=1)

        send = QPushButton("SEND")
        send.setObjectName("SendBtn")
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.clicked.connect(self._send_from_input)
        lay.addWidget(send)

        mic = QPushButton("🎤")
        mic.setObjectName("MicBtn")
        mic.setFixedWidth(46)
        mic.setCursor(Qt.CursorShape.PointingHandCursor)
        mic.clicked.connect(self._trigger_mic)
        lay.addWidget(mic)

        return bar

    # ── Timers / threads ───────────────────────────────────────────────────────
    def _start_clock(self) -> None:
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()

    def _tick_clock(self) -> None:
        self._clock.setText(datetime.now().strftime("%H:%M:%S"))
        up = int(time.time() - self._start_time)
        self._lbl_uptime.setText(str(timedelta(seconds=up)))

    def _start_fetcher(self) -> None:
        self._fetcher = DataFetcher()
        self._fetcher.status_ready.connect(self._on_status)
        self._fetcher.orbit_ready.connect(self._on_orbit)
        self._fetcher.connection_changed.connect(self._on_connection)
        self._fetcher.start()

    # ── Signal handlers (UI thread) ────────────────────────────────────────────
    def _on_connection(self, online: bool) -> None:
        self._connected = online
        self._ai_core.set_online(online)
        for btn in self._mode_btns.values():
            btn.setEnabled(online)
        if online:
            self._banner.hide()
            self._sys_badge.setText("● SYSTEM ONLINE")
            self._sys_badge.setStyleSheet(f"color: {C_GREEN};")
            self._lbl_brain.setText("● ONLINE")
            self._lbl_brain.setStyleSheet(f"color: {C_GREEN};")
            self._lbl_wake.setText("● ACTIVE")
            self._lbl_wake.setStyleSheet(f"color: {C_GREEN};")
            self._add_feed("INFO", "Connected to OS GOD server.")
        else:
            self._banner.show()
            self._sys_badge.setText("● OFFLINE")
            self._sys_badge.setStyleSheet(f"color: {C_RED};")
            self._lbl_brain.setText("● OFFLINE")
            self._lbl_brain.setStyleSheet(f"color: {C_RED};")
            self._lbl_wake.setText("● —")
            self._lbl_wake.setStyleSheet(f"color: {C_MUTED};")
            self._add_feed("WARN", "Lost connection — retrying every 10s.")

    def _on_status(self, data: dict) -> None:
        mode = data.get("current_mode") or "None"
        self._lbl_mode.setText(mode.upper())
        self._lbl_mode.setStyleSheet(f"color: {C_CYAN};")
        if mode != self._current_mode:
            self._current_mode = mode
            self._highlight_mode(mode)
            if mode and mode.lower() != "none":
                self._add_feed("SYSTEM", f"Mode → {mode}")

    def _on_orbit(self, data: dict) -> None:
        if not data.get("ok"):
            return
        score = int(data.get("score") or 0)
        band  = (data.get("band") or "—")
        self._gravity.set_value(score, band)

        # Tasks
        tasks = data.get("tasks") or []
        self._task_badge.setText(str(data.get("pendingCount", len(tasks))))
        self._rebuild_tasks(tasks)

        # Goals
        self._rebuild_goals(data.get("goals") or [])

        if band.lower() in ("warning", "danger", "critical"):
            self._add_feed("ORBIT", f"Gravity {score}% — {band.upper()}.")

    # ── Goals / tasks rendering ─────────────────────────────────────────────────
    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count() > 1:    # keep the trailing stretch
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild_goals(self, goals: list) -> None:
        self._clear_layout(self._goals_layout)
        if not goals:
            empty = QLabel("No active goals")
            empty.setObjectName("KvKey")
            empty.setFont(_font(9))
            self._goals_layout.insertWidget(0, empty)
            return
        for i, g in enumerate(goals):
            self._goals_layout.insertWidget(i, self._goal_widget(g))

    def _goal_widget(self, g: dict) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)

        title = QLabel(str(g.get("title") or "Untitled"))
        title.setFont(_font(9, bold=True))
        title.setWordWrap(True)
        v.addWidget(title)

        pct = int(g.get("completionPct") or 0)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        chunk = C_GREEN if pct >= 66 else (C_AMBER if pct >= 33 else C_RED)
        bar.setStyleSheet(
            f"QProgressBar{{background:{C_BG};border:1px solid {C_BORDER};border-radius:3px;}}"
            f"QProgressBar::chunk{{background:{chunk};border-radius:3px;}}"
        )
        v.addWidget(bar)

        meta = QLabel(f"{pct}%   ·   due {g.get('deadline') or '—'}")
        meta.setObjectName("KvKey")
        meta.setFont(_font(8))
        v.addWidget(meta)
        return w

    def _rebuild_tasks(self, tasks: list) -> None:
        self._clear_layout(self._tasks_layout)
        if not tasks:
            empty = QLabel("No pending tasks")
            empty.setObjectName("KvKey")
            empty.setFont(_font(9))
            self._tasks_layout.insertWidget(0, empty)
            return

        # Group by list (today / daily / quick / other), preserving order.
        groups: dict[str, list] = {}
        for t in tasks:
            groups.setdefault((t.get("list") or "other").lower(), []).append(t)

        idx = 0
        for group, items in groups.items():
            header = QLabel(group.upper())
            header.setObjectName("GroupLabel")
            header.setFont(_font(7, bold=True))
            self._tasks_layout.insertWidget(idx, header)
            idx += 1
            for t in items:
                cb = QCheckBox((t.get("text") or "").strip() or "—")
                cb.setFont(_font(9))
                cb.setEnabled(False)          # visual only
                cb.setObjectName("TaskItem")
                self._tasks_layout.insertWidget(idx, cb)
                idx += 1

    def _highlight_mode(self, mode: str) -> None:
        for m, btn in self._mode_btns.items():
            active = (m.lower() == mode.lower())
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ── Feed ────────────────────────────────────────────────────────────────────
    def _add_feed(self, tag: str, message: str) -> None:
        color = FEED_COLORS.get(tag.upper(), C_TEXT)
        ts    = datetime.now().strftime("%H:%M:%S")
        safe  = message.replace("<", "&lt;").replace(">", "&gt;")
        html = (
            f'<div style="margin-bottom:2px;">'
            f'<span style="color:{C_MUTED};">{ts}</span> '
            f'<span style="color:{color};font-weight:bold;">[{tag.upper()}]</span> '
            f'<span style="color:{C_TEXT};">{safe}</span></div>'
        )
        self._feed.append(html)
        sb = self._feed.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Commands ────────────────────────────────────────────────────────────────
    def _send_from_input(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.send_command(text)

    def _activate_mode(self, mode: str, label: str) -> None:
        self.send_command(f"activate {mode}")

    def send_command(self, text: str) -> None:
        """Send a natural-language command to the running OS GOD (off-thread)."""
        self._add_feed("USER", text)
        if requests is None:
            self._add_feed("ERROR", "'requests' not installed.")
            return
        if not self._connected:
            self._add_feed("WARN", "Not connected — command not sent.")
            return

        def _worker() -> None:
            try:
                requests.post(
                    f"{BASE_URL}/command",
                    json={"text": text},
                    headers=HEADERS,
                    timeout=30,
                )
                self.sig_feed.emit("JARVIS", f"Executing: {text}")
            except Exception as e:
                self.sig_feed.emit("ERROR", f"Command failed: {e}")

        self._fetcher.notify_command_sent()
        threading.Thread(target=_worker, daemon=True).start()
        self._last_response.setText(f"› {text}")

    def _trigger_mic(self) -> None:
        """Request a capture from the core, which owns the microphone."""
        if requests is None:
            self._add_feed("ERROR", "'requests' not installed.")
            return
        if not self._connected:
            self._add_feed("WARN", "Not connected — voice command not sent.")
            return

        def _worker() -> None:
            try:
                response = requests.post(f"{BASE_URL}/voice", headers=HEADERS, timeout=5)
                response.raise_for_status()
                self.sig_feed.emit("SYSTEM", "Listening — speak your command.")
            except Exception as e:
                self.sig_feed.emit("ERROR", f"Mic trigger failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    # ── Shutdown ────────────────────────────────────────────────────────────────
    def closeEvent(self, event) -> None:   # type: ignore[override]
        try:
            self._fetcher.stop()
            self._fetcher.wait(1500)
        except Exception:
            pass
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Stylesheet
# ─────────────────────────────────────────────────────────────────────────────
def _stylesheet() -> str:
    return f"""
QWidget {{
    background: {C_BG};
    color: {C_TEXT};
    font-family: Consolas;
}}
QFrame#Panel {{
    background: {C_PANEL};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
}}
QFrame#Header {{
    background: {C_PANEL};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
}}
QLabel {{ background: transparent; border: none; }}
QLabel#Logo  {{ color: {C_CYAN}; letter-spacing: 2px; }}
QLabel#Clock {{ color: {C_TEXT}; letter-spacing: 3px; }}
QLabel#SysBadge {{ color: {C_AMBER}; }}
QLabel#PanelTitle {{ color: {C_CYAN}; letter-spacing: 2px; }}
QLabel#GroupLabel {{ color: {C_MUTED}; letter-spacing: 2px; padding-top: 4px; }}
QLabel#KvKey {{ color: {C_MUTED}; letter-spacing: 1px; }}
QLabel#LastResp {{ color: {C_MUTED}; }}
QLabel#Banner {{
    background: rgba(255,51,68,0.12);
    color: {C_RED};
    border: 1px solid {C_RED};
    border-radius: 6px;
    letter-spacing: 1px;
}}
QLabel#Badge {{
    background: {C_BORDER};
    color: {C_CYAN};
    border-radius: 8px;
    padding: 1px 8px;
}}
QPushButton {{
    background: {C_PANEL};
    border: 1px solid {C_CYAN};
    color: {C_CYAN};
    padding: 8px 16px;
    border-radius: 4px;
    font-family: Consolas;
}}
QPushButton:hover {{ background: #0a2040; }}
QPushButton:pressed {{ background: #0d2a55; }}
QPushButton#ModeBtn {{
    padding: 10px 4px;
    font-weight: bold;
    border: 1px solid {C_BORDER};
    color: {C_TEXT};
}}
QPushButton#ModeBtn:hover {{ border-color: {C_CYAN}; color: {C_CYAN}; }}
QPushButton#ModeBtn:disabled {{ color: {C_MUTED}; border-color: #0a1a2a; background: {C_BG}; }}
QPushButton#ModeBtn[active="true"] {{
    border: 1px solid {C_CYAN};
    color: {C_CYAN};
    background: #0a2547;
}}
QPushButton#SendBtn {{ font-weight: bold; }}
QPushButton#MicBtn {{ font-size: 16px; padding: 6px; }}
QLineEdit {{
    background: {C_PANEL};
    border: 1px solid {C_CYAN};
    color: {C_TEXT};
    padding: 8px;
    border-radius: 4px;
    font-family: Consolas;
}}
QLineEdit:focus {{ border: 1px solid {C_GREEN}; }}
QProgressBar {{
    background: #0a1628;
    border: 1px solid {C_BORDER};
    border-radius: 4px;
    height: 6px;
}}
QProgressBar::chunk {{ background: {C_CYAN}; border-radius: 4px; }}
QScrollArea {{ border: none; background: transparent; }}
QTextEdit#Feed {{
    background: {C_BG};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    font-family: Consolas;
    font-size: 11px;
}}
QCheckBox#TaskItem {{ color: {C_TEXT}; spacing: 6px; padding: 2px 0; }}
QCheckBox#TaskItem::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {C_BORDER};
    border-radius: 3px;
    background: {C_PANEL};
}}
QScrollBar:vertical {{ background: {C_BG}; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {C_BORDER}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {C_CYAN}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS Command Center")
    app.setStyleSheet(_stylesheet())

    win = Dashboard()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
