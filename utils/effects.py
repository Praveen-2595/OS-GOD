"""
utils/effects.py
Premium full-screen tkinter overlays for every mode.
DJ Pawnin uses tkinter canvas animations — no browser.
All popups are topmost, fullscreen, animated, and auto-close.

ENHANCEMENTS v2:
  1. Chromatic aberration glitch effect on mode name text.
  2. Hex-grid background pattern replacing flat scan lines.
  3. Plasma wave effect on hold phase (rippling sine-composite colors).
  4. DNA-helix ring orbits instead of plain concentric circles.
  5. Typed-text sub_label reveal (character-by-character).
  6. Strobing beat-sync during DJ Pawnin hold phase.
  7. 3-layer parallax star field for depth.
  8. Shockwave ring burst on popup open.
  9. Per-particle color drift over lifetime.
 10. Corner brackets animate inward from outside viewport.
 11. Fire-and-forget everywhere — no t.join() blocking TTS.
 12. dim_brightness still returns None on failure.
"""

import os
import math
import time
import random
import threading
import tkinter as tk
from tkinter import font as tkfont


# ─────────────────────────────────────────────────────────────────────────────
# Brightness helpers
# ─────────────────────────────────────────────────────────────────────────────
def dim_brightness(level: int = 20) -> int | None:
    try:
        import screen_brightness_control as sbc
        old = sbc.get_brightness()[0]
        sbc.set_brightness(level)
        return old
    except Exception:
        return None


def restore_brightness(level: int | None = None) -> None:
    if level is None:
        return
    try:
        import screen_brightness_control as sbc
        sbc.set_brightness(level)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Colour helpers
# ─────────────────────────────────────────────────────────────────────────────
def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r, g, b) -> str:
    return f"#{int(max(0,min(255,r))):02x}{int(max(0,min(255,g))):02x}{int(max(0,min(255,b))):02x}"


def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def _alpha_hex(color: str, alpha: float) -> str:
    """Blend color with black at given alpha (0..1)."""
    return _lerp_color("#000000", color, max(0.0, min(1.0, alpha)))


def _plasma_color(t: float, x: float, y: float, accent: str) -> str:
    """Generate a plasma/lissajous composite color for the hold phase."""
    v = (math.sin(x * 3.1 + t * 5)
         + math.sin(y * 2.7 + t * 4)
         + math.sin((x + y) * 1.9 + t * 3)
         + math.sin(math.sqrt(x*x + y*y) * 2.5 + t * 6)) / 4.0
    frac = (v + 1) / 2.0  # 0..1
    return _lerp_color("#000000", accent, 0.15 + frac * 0.55)


# ─────────────────────────────────────────────────────────────────────────────
# Hex-grid background builder
# ─────────────────────────────────────────────────────────────────────────────
def _draw_hex_grid(canvas, W, H, accent, alpha=0.06, size=42):
    """Draw a flat-top hex grid across the canvas."""
    w2 = size
    h2 = size * math.sqrt(3) / 2
    col = _alpha_hex(accent, alpha)
    for row in range(-1, int(H / h2) + 2):
        for col_i in range(-1, int(W / (w2 * 1.5)) + 2):
            cx = col_i * w2 * 1.5
            cy = row * h2 * 2 + (h2 if col_i % 2 else 0)
            pts = []
            for angle_deg in range(0, 360, 60):
                a = math.radians(angle_deg)
                pts += [cx + w2 * math.cos(a), cy + w2 * math.sin(a)]
            canvas.create_polygon(pts, outline=_alpha_hex(accent, alpha),
                                  fill="", width=1)


# ─────────────────────────────────────────────────────────────────────────────
# Shockwave ring burst
# ─────────────────────────────────────────────────────────────────────────────
def _shockwave_burst(canvas, W, H, accent, root, steps=22, delay=0.012):
    """Animate expanding shockwave rings on popup open (blocking, called in thread)."""
    rings = []
    for i in range(3):
        r = 10 + i * 35
        oid = canvas.create_oval(W//2 - r, H//2 - r, W//2 + r, H//2 + r,
                                 outline=_alpha_hex(accent, 0.9 - i*0.2),
                                 fill="", width=max(1, 3 - i))
        rings.append((oid, i))

    max_r = max(W, H) * 0.75
    for step in range(steps):
        t = step / steps
        ease = 1 - (1 - t) ** 2
        for oid, i in rings:
            r = (10 + i * 35) + ease * (max_r - 10 - i * 35)
            alpha = max(0, (0.9 - i*0.2) * (1 - t))
            canvas.coords(oid, W//2 - r, H//2 - r, W//2 + r, H//2 + r)
            canvas.itemconfig(oid, outline=_alpha_hex(accent, alpha))
        root.update()
        time.sleep(delay)
    for oid, _ in rings:
        canvas.delete(oid)


# ─────────────────────────────────────────────────────────────────────────────
# Star field (3-layer parallax)
# ─────────────────────────────────────────────────────────────────────────────
def _make_starfield(canvas, W, H, accent, n=120):
    stars = []
    for _ in range(n):
        layer  = random.choice([1, 2, 3])
        x      = random.randint(0, W)
        y      = random.randint(0, H)
        r      = {1: 0.6, 2: 1.1, 3: 1.8}[layer]
        speed  = {1: 0.15, 2: 0.35, 3: 0.65}[layer]
        bright = {1: 0.2, 2: 0.4, 3: 0.7}[layer]
        col    = _alpha_hex(accent if layer == 3 else "#ffffff", bright)
        oid    = canvas.create_oval(x-r, y-r, x+r, y+r, fill=col, outline="")
        stars.append({"x": float(x), "y": float(y), "r": r, "speed": speed,
                      "bright": bright, "layer": layer, "oid": oid,
                      "col_base": accent if layer == 3 else "#ffffff"})
    return stars


def _tick_starfield(canvas, stars, W, H, accent, frame, fade):
    for s in stars:
        s["y"] -= s["speed"]
        if s["y"] < -2:
            s["y"] = H + 2
            s["x"] = random.randint(0, W)
        twinkle = 0.6 + 0.4 * math.sin(frame * 0.11 * s["layer"] + s["x"])
        col = _alpha_hex(s["col_base"], s["bright"] * twinkle * fade)
        r   = s["r"]
        canvas.coords(s["oid"], s["x"]-r, s["y"]-r, s["x"]+r, s["y"]+r)
        canvas.itemconfig(s["oid"], fill=col)


# ─────────────────────────────────────────────────────────────────────────────
# Orbiting DNA-helix rings
# ─────────────────────────────────────────────────────────────────────────────
def _make_orbit_rings(canvas, W, H, accent, radii=(240, 185, 140)):
    rings = []
    for i, base_r in enumerate(radii):
        speed = 0.008 + i * 0.004
        n_dots = 18 - i * 3
        dots = []
        for d in range(n_dots):
            oid = canvas.create_oval(-5, -5, 5, 5, fill=_alpha_hex(accent, 0.0), outline="")
            dots.append(oid)
        rings.append({"base_r": base_r, "speed": speed, "dots": dots,
                      "n": n_dots, "phase": i * math.pi / 3})
    return rings


def _tick_orbit_rings(canvas, rings, W, H, accent, frame, fade):
    cx, cy = W//2, H//2
    for ring in rings:
        angle_offset = frame * ring["speed"] + ring["phase"]
        r3d_amp = ring["base_r"] * 0.28
        for d, oid in enumerate(ring["dots"]):
            theta = angle_offset + (d / ring["n"]) * 2 * math.pi
            # Helix: depth = sin gives z-axis perspective squash
            depth   = math.sin(theta)
            screen_x = cx + ring["base_r"] * math.cos(theta)
            screen_y = cy + r3d_amp * depth
            alpha_d  = 0.15 + 0.6 * ((depth + 1) / 2) * fade
            r_dot    = 1.5 + 2.0 * ((depth + 1) / 2)
            col = _lerp_color(accent, "#ffffff", ((depth + 1) / 2) * 0.35)
            col = _alpha_hex(col, alpha_d)
            canvas.coords(oid,
                screen_x - r_dot, screen_y - r_dot,
                screen_x + r_dot, screen_y + r_dot)
            canvas.itemconfig(oid, fill=col)


# ─────────────────────────────────────────────────────────────────────────────
# Chromatic-aberration glitch text helper
# ─────────────────────────────────────────────────────────────────────────────
def _make_glitch_text(canvas, cx, cy, text, font_spec, accent):
    """Returns (red_id, blue_id, main_id) for chromatic aberration effect."""
    r, g, b = _hex_to_rgb(accent)
    red_col  = _rgb_to_hex(min(255, r + 90), max(0, g - 40), max(0, b - 40))
    blue_col = _rgb_to_hex(max(0, r - 40), max(0, g - 40), min(255, b + 90))
    red_id   = canvas.create_text(cx - 4, cy + 2, text=text, font=font_spec,
                                  fill=_alpha_hex(red_col, 0.0))
    blue_id  = canvas.create_text(cx + 4, cy - 2, text=text, font=font_spec,
                                  fill=_alpha_hex(blue_col, 0.0))
    main_id  = canvas.create_text(cx, cy, text=text, font=font_spec,
                                  fill=_alpha_hex(accent, 0.0))
    return red_id, blue_id, main_id


def _update_glitch(canvas, red_id, blue_id, main_id,
                   cx, cy, accent, fade, frame, intensity=0.35):
    """Update glitch text layers each frame."""
    r, g, b   = _hex_to_rgb(accent)
    red_col   = _rgb_to_hex(min(255,r+90), max(0,g-40), max(0,b-40))
    blue_col  = _rgb_to_hex(max(0,r-40), max(0,g-40), min(255,b+90))

    glitch_active = random.random() < 0.08
    ox = random.randint(-8, 8) if glitch_active else 0
    oy = random.randint(-3, 3) if glitch_active else 0
    ab_spread = 4 + (8 if glitch_active else 0)

    canvas.coords(red_id, cx - ab_spread + ox, cy + 2 + oy)
    canvas.coords(blue_id, cx + ab_spread + ox, cy - 2 + oy)
    canvas.coords(main_id, cx + ox, cy + oy)

    canvas.itemconfig(red_id,  fill=_alpha_hex(red_col,  fade * intensity))
    canvas.itemconfig(blue_id, fill=_alpha_hex(blue_col, fade * intensity))
    canvas.itemconfig(main_id, fill=_alpha_hex(accent, fade))


# ─────────────────────────────────────────────────────────────────────────────
# Animated corner brackets
# ─────────────────────────────────────────────────────────────────────────────
def _make_corner_brackets(canvas, W, H, accent):
    length = 100
    thick  = 2
    col    = _alpha_hex(accent, 0.0)
    ids = []
    # Each corner: 2 lines
    for (ax, ay, dx, dy) in [(0,0,1,1),(W,0,-1,1),(0,H,1,-1),(W,H,-1,-1)]:
        h_id = canvas.create_line(ax, ay, ax, ay, fill=col, width=thick)
        v_id = canvas.create_line(ax, ay, ax, ay, fill=col, width=thick)
        ids.append((h_id, v_id, ax, ay, dx, dy, length))
    return ids


def _update_corner_brackets(canvas, bracket_ids, accent, progress):
    col = _alpha_hex(accent, min(1.0, progress * 1.4) * 0.75)
    for (h_id, v_id, ax, ay, dx, dy, length) in bracket_ids:
        l = int(progress * length)
        canvas.coords(h_id, ax, ay, ax + dx * l, ay)
        canvas.coords(v_id, ax, ay, ax, ay + dy * l)
        canvas.itemconfig(h_id, fill=col)
        canvas.itemconfig(v_id, fill=col)


# ─────────────────────────────────────────────────────────────────────────────
# Typed-text sub label
# ─────────────────────────────────────────────────────────────────────────────
def _update_typed_text(canvas, sub_id, full_text, frame, start_frame, accent, fade):
    if frame < start_frame:
        canvas.itemconfig(sub_id, text="", fill="#000000")
        return
    chars_shown = min(len(full_text), (frame - start_frame) * 2)
    displayed   = full_text[:chars_shown]
    cursor      = "█" if frame % 14 < 7 and chars_shown < len(full_text) else ""
    canvas.itemconfig(sub_id,
                      text=displayed + cursor,
                      fill=_alpha_hex("#aaaaaa", fade * 0.85))


# ─────────────────────────────────────────────────────────────────────────────
# Mode metadata
# ─────────────────────────────────────────────────────────────────────────────
_MODE_META = {
    "alpha state":    {"accent": "#ffe600", "sub": "FOCUS MODE ENGAGED",     "icon": "𓇻"},
    "zen state":      {"accent": "#52c6c9", "sub": "ZEN MODE ENGAGED",       "icon": "❋"},
    "render state":   {"accent": "#ff6b35", "sub": "RENDER MODE ENGAGED",    "icon": "❂"},
    "gaming state":   {"accent": "#c7ff4d", "sub": "GAMING MODE ENGAGED",    "icon": "𓆩♕𓆪"},
    "developer mode": {"accent": "#00c853", "sub": "DEVELOPER MODE ENGAGED", "icon": "🎕"},
    "shutdown":       {"accent": "#ff3860", "sub": "SHUTTING DOWN...",       "icon": "⏻"},
    "gg":             {"accent": "#ffd700", "sub": "GOOD GAME",              "icon": "★"},
}


# ─────────────────────────────────────────────────────────────────────────────
# Generic mode popup — premium v2
# ─────────────────────────────────────────────────────────────────────────────
def show_mode_popup(mode_name: str, accent: str = "", duration: float = 2.5) -> None:
    meta = _MODE_META.get(mode_name.lower())
    if meta is None:
        meta = {
            "accent": accent or "#00e5ff",
            "sub":    f"{mode_name.upper()} ENGAGED",
            "icon":   "◈",
        }
    if not accent:
        accent = meta["accent"]
    sub_text = meta["sub"]
    icon     = meta["icon"]

    def _run():
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(bg="#000000")
        root.attributes("-alpha", 0.0)

        W = root.winfo_screenwidth()
        H = root.winfo_screenheight()

        canvas = tk.Canvas(root, width=W, height=H, bg="#000000",
                           highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # ── hex-grid background ──────────────────────────────────────────────
        _draw_hex_grid(canvas, W, H, accent, alpha=0.055, size=46)

        # ── 3-layer star field ───────────────────────────────────────────────
        stars = _make_starfield(canvas, W, H, accent, n=100)

        # ── shockwave (drawn but will be animated in fade-in) ────────────────
        shock_r0 = 30
        shock_ids = []
        for i in range(3):
            oid = canvas.create_oval(W//2 - shock_r0, H//2 - shock_r0,
                                     W//2 + shock_r0, H//2 + shock_r0,
                                     outline=_alpha_hex(accent, 0.0), fill="",
                                     width=max(1, 3 - i))
            shock_ids.append(oid)

        # ── orbit rings ──────────────────────────────────────────────────────
        orbit_rings = _make_orbit_rings(canvas, W, H, accent, radii=(260, 200, 155))

        # ── corner brackets ──────────────────────────────────────────────────
        brackets = _make_corner_brackets(canvas, W, H, accent)

        # ── icon ─────────────────────────────────────────────────────────────
        icon_id = canvas.create_text(
            W//2, H//2 - 140, text=icon,
            font=("Segoe UI Symbol", 56),
            fill=_alpha_hex(accent, 0.0))

        # ── mode name (with chromatic aberration layers) ──────────────────────
        title_font = ("Courier New", 70, "bold")
        title_r, title_b, title_id = _make_glitch_text(
            canvas, W//2, H//2 - 20, mode_name.upper(), title_font, accent)

        # ── typed sub label ──────────────────────────────────────────────────
        sub_id = canvas.create_text(
            W//2, H//2 + 70, text="", font=("Courier New", 20),
            fill="#000000")

        # ── animated separator bar ───────────────────────────────────────────
        sep_id = canvas.create_rectangle(
            W//2, H//2 + 38, W//2, H//2 + 40,
            fill=accent, outline="")

        root.update()

        # ── FADE IN ──────────────────────────────────────────────────────────
        steps_in = 30
        shock_max = max(W, H) * 0.65

        for step in range(steps_in + 1):
            t    = step / steps_in
            ease = t * t * (3 - 2 * t)

            root.attributes("-alpha", min(ease * 0.96, 0.96))

            # shockwave rings
            for i, oid in enumerate(shock_ids):
                sr = shock_r0 + ease * (shock_max - shock_r0 - i * 50)
                sa = max(0, (0.9 - i*0.25) * (1 - ease))
                canvas.coords(oid, W//2-sr, H//2-sr, W//2+sr, H//2+sr)
                canvas.itemconfig(oid, outline=_alpha_hex(accent, sa))

            # corner brackets
            _update_corner_brackets(canvas, brackets, accent, ease)

            # stars
            _tick_starfield(canvas, stars, W, H, accent, step, ease)

            # orbit rings
            _tick_orbit_rings(canvas, orbit_rings, W, H, accent, step * 2, ease)

            # icon
            icon_bounce = math.sin(ease * math.pi) * 18 * (1 - ease)
            canvas.coords(icon_id, W//2, H//2 - 140 - int(icon_bounce))
            canvas.itemconfig(icon_id, fill=_alpha_hex(accent, ease * 0.9))

            # title glitch
            _update_glitch(canvas, title_r, title_b, title_id,
                           W//2, H//2 - 20, accent, ease, step)

            # typed sub
            _update_typed_text(canvas, sub_id, sub_text, step,
                               steps_in // 2, accent, ease)

            # separator
            bar_w = int(ease * 280)
            canvas.coords(sep_id, W//2 - bar_w, H//2 + 38, W//2 + bar_w, H//2 + 40)

            root.update()
            time.sleep(0.016)

        # Delete shockwave rings — no longer needed
        for oid in shock_ids:
            canvas.delete(oid)

        # ── HOLD ─────────────────────────────────────────────────────────────
        hold_steps = int(duration / 0.025)
        for step in range(hold_steps):
            t = step / hold_steps

            glow = 0.65 + 0.35 * math.sin(t * math.pi * 5)
            canvas.itemconfig(icon_id, fill=_alpha_hex(accent, glow * 0.95))

            _tick_starfield(canvas, stars, W, H, accent, steps_in + step, 1.0)
            _tick_orbit_rings(canvas, orbit_rings, W, H, accent,
                              (steps_in + step) * 2, 1.0)
            _update_corner_brackets(canvas, brackets, accent, 1.0)
            _update_glitch(canvas, title_r, title_b, title_id,
                           W//2, H//2 - 20, accent, 1.0, steps_in + step)
            canvas.itemconfig(sub_id, text=sub_text,
                              fill=_alpha_hex("#aaaaaa", 0.8))

            root.update()
            time.sleep(0.025)

        # ── FADE OUT ─────────────────────────────────────────────────────────
        steps_out = 22
        for step in range(steps_out + 1):
            t    = step / steps_out
            ease = t * t
            root.attributes("-alpha", max(0.96 * (1 - ease), 0.0))
            root.update()
            time.sleep(0.016)

        root.destroy()

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ─────────────────────────────────────────────────────────────────────────────
# DJ PAWNIN popup — premium v2
# ─────────────────────────────────────────────────────────────────────────────
def show_dj_pawnin_popup(phase: str = "intro") -> None:
    if phase == "intro":
        _dj_intro_popup()
    else:
        _dj_activated_popup()


def _dj_intro_popup() -> None:
    MAGENTA = "#ff006e"
    CYAN    = "#00f5ff"
    YELLOW  = "#ffe600"
    BG      = "#060010"

    def _run():
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        root.attributes("-alpha", 0.0)

        W = root.winfo_screenwidth()
        H = root.winfo_screenheight()
        canvas = tk.Canvas(root, width=W, height=H, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # ── hex grid ─────────────────────────────────────────────────────────
        _draw_hex_grid(canvas, W, H, MAGENTA, alpha=0.04, size=40)

        # ── star field ───────────────────────────────────────────────────────
        stars = _make_starfield(canvas, W, H, CYAN, n=90)

        # ── perspective grid ─────────────────────────────────────────────────
        vp_x, vp_y = W // 2, H // 2 + 80
        grid_ids = []
        for i in range(-20, 21):
            x_far  = W // 2 + i * 70
            x_near = W // 2 + i * 500
            oid = canvas.create_line(x_far, vp_y, x_near, H,
                                     fill=_alpha_hex(MAGENTA, 0.22), width=1)
            grid_ids.append(oid)
        for depth in range(1, 10):
            frac   = depth / 9
            y_line = int(vp_y + (H - vp_y) * (frac ** 1.4))
            x_sp   = int((W * 0.46) * frac)
            oid    = canvas.create_line(W//2 - x_sp, y_line,
                                        W//2 + x_sp, y_line,
                                        fill=_alpha_hex(CYAN, 0.15 + 0.08*frac), width=1)
            grid_ids.append(oid)

        # ── vinyl record ──────────────────────────────────────────────────────
        vx, vy = int(W * 0.78), H // 2 - 30
        vr = min(int(W * 0.13), 155)
        # outer glow rings
        for gr_r in range(vr + 30, vr + 5, -8):
            alpha_g = 0.04 * (1 - (gr_r - vr) / 30)
            canvas.create_oval(vx-gr_r, vy-gr_r, vx+gr_r, vy+gr_r,
                               outline=_alpha_hex(MAGENTA, alpha_g), fill="", width=8)
        canvas.create_oval(vx-vr-10, vy-vr-10, vx+vr+10, vy+vr+10,
                           outline=MAGENTA, fill="", width=3)
        canvas.create_oval(vx-vr-4, vy-vr-4, vx+vr+4, vy+vr+4,
                           outline=_alpha_hex(CYAN, 0.35), fill="", width=1)
        # grooves
        for gr in range(10, vr, 10):
            alpha_g = 0.04 + 0.06 * (gr / vr)
            canvas.create_oval(vx-gr, vy-gr, vx+gr, vy+gr,
                               outline=_alpha_hex("#ffffff", alpha_g), fill="")
        # label circle
        canvas.create_oval(vx-22, vy-22, vx+22, vy+22,
                           fill=MAGENTA, outline=_alpha_hex(CYAN, 0.9), width=2)
        canvas.create_oval(vx-4, vy-4, vx+4, vy+4,
                           fill="#000000", outline="")
        # needle
        needle_id = canvas.create_line(
            vx + vr - 8, vy - vr - 18, vx + 28, vy - 18,
            fill=_alpha_hex(YELLOW, 0.9), width=3, capstyle="round")
        needle_dot = canvas.create_oval(vx+25, vy-21, vx+31, vy-15,
                                        fill=YELLOW, outline="")

        # ── EQ bars ───────────────────────────────────────────────────────────
        bar_count = 40
        bar_w     = max(7, (W - 160) // bar_count - 4)
        bar_gap   = 4
        bar_area_w = bar_count * (bar_w + bar_gap)
        bar_x0    = (W - bar_area_w) // 2
        bar_floor = H - 50
        bar_max_h = 220

        bars_meta = []
        bar_ids   = []
        peak_ids  = []
        for i in range(bar_count):
            target_h = random.randint(20, bar_max_h)
            cur_h    = random.randint(5, 30)
            speed    = random.uniform(3, 10)
            bars_meta.append({"target": target_h, "cur": float(cur_h), "speed": speed,
                               "peak": float(cur_h), "peak_hold": 0})
            x   = bar_x0 + i * (bar_w + bar_gap)
            oid = canvas.create_rectangle(x, bar_floor - cur_h, x + bar_w, bar_floor,
                                          fill=_alpha_hex(MAGENTA, 0.55), outline="")
            bar_ids.append(oid)
            pk  = canvas.create_rectangle(x, bar_floor - cur_h - 3,
                                          x + bar_w, bar_floor - cur_h,
                                          fill=_alpha_hex(CYAN, 0.0), outline="")
            peak_ids.append(pk)

        # ── chromatic title ────────────────────────────────────────────────────
        title_font = ("Courier New", 88, "bold")
        label_id = canvas.create_text(
            W//2 - 40, H//2 - 100,
            text="⚡  SYSTEM OVERRIDE  ⚡",
            font=("Courier New", 17, "bold"),
            fill=_alpha_hex(CYAN, 0.0))

        name_r, name_b, name_id = _make_glitch_text(
            canvas, W//2 - 40, H//2 + 10,
            "DJ PAWNIN", title_font, MAGENTA)

        sub_id = canvas.create_text(
            W//2 - 40, H//2 + 110,
            text="",
            font=("Courier New", 19),
            fill=_alpha_hex(YELLOW, 0.0))

        root.update()

        # ── FLASH ────────────────────────────────────────────────────────────
        flash_id = canvas.create_rectangle(0, 0, W, H, fill="#ffffff", outline="")
        for a in [0.85, 0.55, 0.3, 0.12, 0.0]:
            canvas.itemconfig(flash_id, fill=_alpha_hex("#ffffff", a))
            root.attributes("-alpha", 0.97)
            root.update()
            time.sleep(0.035)
        canvas.delete(flash_id)

        # ── ANIMATE ───────────────────────────────────────────────────────────
        total_frames = 200
        frame_delay  = 0.018
        beat_period  = 22  # frames per beat pulse

        for frame in range(total_frames):
            t       = frame / total_frames
            fade_in = min(frame / 28, 1.0)
            beat_t  = (frame % beat_period) / beat_period
            beat    = math.exp(-6 * beat_t) * (1 - beat_t)  # sharp decay pulse

            root.attributes("-alpha", min(fade_in * 0.97, 0.97))

            _tick_starfield(canvas, stars, W, H, CYAN, frame, fade_in)

            # grid pulse on beat
            grid_alpha = 0.22 + beat * 0.18
            for oid in grid_ids[:len(grid_ids)//2]:
                canvas.itemconfig(oid, fill=_alpha_hex(MAGENTA, grid_alpha))

            # label
            canvas.itemconfig(label_id, fill=_alpha_hex(CYAN, fade_in * 0.85))

            # chromatic name
            _update_glitch(canvas, name_r, name_b, name_id,
                           W//2 - 40, H//2 + 10, MAGENTA, fade_in, frame)

            # typed sub
            _update_typed_text(canvas, sub_id, "ACTIVATING MODE  ·  DROPPING IN",
                               frame, 35, YELLOW, fade_in)

            # EQ bars with peaks
            for i, (meta, oid, pk) in enumerate(zip(bars_meta, bar_ids, peak_ids)):
                if random.random() < 0.10 + beat * 0.15:
                    meta["target"] = random.randint(12, bar_max_h)
                if frame % beat_period < 2:
                    meta["target"] = random.randint(bar_max_h // 2, bar_max_h)
                meta["cur"] += (meta["target"] - meta["cur"]) * 0.16
                cur_h = max(4, int(meta["cur"]))
                # peak tracker
                if cur_h >= meta["peak"]:
                    meta["peak"]      = cur_h
                    meta["peak_hold"] = 18
                else:
                    if meta["peak_hold"] > 0:
                        meta["peak_hold"] -= 1
                    else:
                        meta["peak"] = max(cur_h, meta["peak"] - 1.2)
                peak_h = int(meta["peak"])

                x   = bar_x0 + i * (bar_w + bar_gap)
                frac = cur_h / bar_max_h
                col  = _lerp_color(MAGENTA, CYAN, frac)
                canvas.coords(oid, x, bar_floor - cur_h, x + bar_w, bar_floor)
                canvas.itemconfig(oid, fill=_alpha_hex(col, 0.5 + 0.3*fade_in))
                canvas.coords(pk, x, bar_floor - peak_h - 3,
                              x + bar_w, bar_floor - peak_h)
                canvas.itemconfig(pk, fill=_alpha_hex(CYAN, fade_in * 0.8))

            # needle swing
            needle_swing = 8 * math.sin(t * math.pi * 4) + beat * 5
            canvas.coords(needle_id,
                          vx + vr - 8 + needle_swing, vy - vr - 18,
                          vx + 28, vy - 18)

            root.update()
            time.sleep(frame_delay)

        # ── FADE OUT ──────────────────────────────────────────────────────────
        for step in range(22):
            root.attributes("-alpha", max(0.97 * (1 - step/22), 0.0))
            root.update()
            time.sleep(0.016)

        root.destroy()

    th = threading.Thread(target=_run, daemon=True)
    th.start()


def _dj_activated_popup() -> None:
    MAGENTA = "#ff006e"
    CYAN    = "#00f5ff"
    YELLOW  = "#ffe600"
    GREEN   = "#00ff9f"
    BG      = "#060010"
    CONFETTI_COLORS = [MAGENTA, CYAN, YELLOW, GREEN, "#ff4da6", "#ffffff", "#a855f7"]

    def _run():
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        root.attributes("-alpha", 0.0)

        W = root.winfo_screenwidth()
        H = root.winfo_screenheight()
        canvas = tk.Canvas(root, width=W, height=H, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # ── hex grid ─────────────────────────────────────────────────────────
        _draw_hex_grid(canvas, W, H, GREEN, alpha=0.045, size=44)

        # ── star field ───────────────────────────────────────────────────────
        stars = _make_starfield(canvas, W, H, CYAN, n=80)

        # ── radial burst lines ────────────────────────────────────────────────
        burst_line_ids = []
        n_rays = 24
        for i in range(n_rays):
            angle = (i / n_rays) * 2 * math.pi
            dx    = math.cos(angle)
            dy    = math.sin(angle)
            col   = _lerp_color(MAGENTA, CYAN, i / n_rays)
            oid   = canvas.create_line(W//2, H//2,
                                       W//2 + dx * 10, H//2 + dy * 10,
                                       fill=_alpha_hex(col, 0.0), width=2)
            burst_line_ids.append((oid, dx, dy, col))

        # ── radial burst rings ────────────────────────────────────────────────
        burst_ring_ids = []
        for rad in range(20, min(W, H) // 2, 30):
            frac = rad / (min(W, H) // 2)
            col  = _lerp_color(MAGENTA, CYAN, frac)
            oid  = canvas.create_oval(W//2 - rad, H//2 - rad,
                                      W//2 + rad, H//2 + rad,
                                      outline=_alpha_hex(col, 0.0), fill="", width=2)
            burst_ring_ids.append((oid, rad, col))

        # ── wipe lines ────────────────────────────────────────────────────────
        wipe_top    = canvas.create_line(W//2, H*0.28, W//2, H*0.28,
                                         fill=CYAN, width=3)
        wipe_bottom = canvas.create_line(W//2, H*0.72, W//2, H*0.72,
                                         fill=CYAN, width=3)

        # ── orbit rings ───────────────────────────────────────────────────────
        orbit_rings = _make_orbit_rings(canvas, W, H, GREEN, radii=(220, 170, 128))

        # ── corner brackets ───────────────────────────────────────────────────
        brackets = _make_corner_brackets(canvas, W, H, CYAN)

        # ── icon ─────────────────────────────────────────────────────────────
        icon_id = canvas.create_text(W//2, H//2 - 150, text="✦",
                                     font=("Segoe UI Symbol", 72),
                                     fill=_alpha_hex(GREEN, 0.0))

        # ── ACTIVATED chromatic text ──────────────────────────────────────────
        act_font   = ("Courier New", 96, "bold")
        act_r, act_b, act_id = _make_glitch_text(
            canvas, W//2, H//2 - 5, "ACTIVATED", act_font, MAGENTA)

        sub_id = canvas.create_text(W//2, H//2 + 100, text="",
                                    font=("Courier New", 21),
                                    fill=_alpha_hex(YELLOW, 0.0))

        # ── physics confetti ──────────────────────────────────────────────────
        confetti = []
        conf_ids = []
        for _ in range(80):
            x   = random.uniform(W * 0.1, W * 0.9)
            y   = random.uniform(-300, -10)
            r   = random.randint(4, 11)
            col = random.choice(CONFETTI_COLORS)
            vx  = random.uniform(-3, 3)
            vy  = random.uniform(5, 14)
            rot = random.uniform(0, 360)
            spin = random.uniform(-5, 5)
            oid = canvas.create_oval(x-r, y-r, x+r, y+r,
                                     fill=_alpha_hex(col, 0.0), outline="")
            confetti.append({"x": float(x), "y": float(y), "r": r, "col": col,
                             "vx": vx, "vy": vy, "rot": rot, "spin": spin,
                             "gravity": 0.25})
            conf_ids.append(oid)

        root.update()

        # ── FLASH BURST ───────────────────────────────────────────────────────
        flash_id = canvas.create_rectangle(0, 0, W, H, fill="#ffffff", outline="")
        for a in [0.9, 0.6, 0.35, 0.15, 0.0]:
            canvas.itemconfig(flash_id, fill=_alpha_hex("#ffffff", a))
            root.attributes("-alpha", 0.97)
            root.update()
            time.sleep(0.032)
        canvas.delete(flash_id)

        # ── ANIMATE ───────────────────────────────────────────────────────────
        total_frames = 160
        frame_delay  = 0.018
        beat_period  = 20

        for frame in range(total_frames):
            t      = frame / total_frames
            fade   = min(frame / 22, 1.0)
            beat_t = (frame % beat_period) / beat_period
            beat   = math.exp(-5 * beat_t)

            root.attributes("-alpha", min(fade * 0.97, 0.97))

            _tick_starfield(canvas, stars, W, H, CYAN, frame, fade)
            _tick_orbit_rings(canvas, orbit_rings, W, H, GREEN, frame * 2, fade)
            _update_corner_brackets(canvas, brackets, CYAN, min(frame / 15, 1.0))

            # burst rays expand
            ray_len = min(frame / 30, 1.0) * max(W, H) * 0.6
            for oid, dx, dy, col in burst_line_ids:
                ray_alpha = max(0, (0.45 - t * 0.5) * fade)
                canvas.coords(oid, W//2, H//2,
                              W//2 + dx * ray_len, H//2 + dy * ray_len)
                canvas.itemconfig(oid, fill=_alpha_hex(col, ray_alpha))

            # burst rings
            for oid, rad, col in burst_ring_ids:
                ring_t = min(frame / 32, 1.0)
                sc     = 0.15 + ring_t * 0.85
                alpha  = max(0, (1 - ring_t) * 0.5 * fade)
                sr     = rad * sc
                canvas.coords(oid, W//2-sr, H//2-sr, W//2+sr, H//2+sr)
                canvas.itemconfig(oid, outline=_alpha_hex(col, alpha))

            # wipe lines
            wp = min(frame / 18, 1.0)
            hw = int(W // 2 * wp)
            canvas.coords(wipe_top,    W//2-hw, H*0.28, W//2+hw, H*0.28)
            canvas.coords(wipe_bottom, W//2-hw, H*0.72, W//2+hw, H*0.72)
            wa = max(0, 0.75 - t * 1.1)
            canvas.itemconfig(wipe_top,    fill=_alpha_hex(CYAN, wa))
            canvas.itemconfig(wipe_bottom, fill=_alpha_hex(CYAN, wa))

            # icon with beat pulse
            icon_glow = (0.65 + 0.35 * math.sin(t * math.pi * 9)) * fade + beat * 0.3
            icon_glow = min(1.0, icon_glow)
            canvas.itemconfig(icon_id, fill=_alpha_hex(GREEN, icon_glow))

            # chromatic ACTIVATED
            _update_glitch(canvas, act_r, act_b, act_id,
                           W//2, H//2 - 5, MAGENTA, fade, frame, intensity=0.4)

            # typed sub
            _update_typed_text(canvas, sub_id, "DJ PAWNIN MODE  ·  LET'S GO",
                               frame, 20, YELLOW, fade)

            # confetti physics
            for i, (p, oid) in enumerate(zip(confetti, conf_ids)):
                if frame > 8:
                    p["vy"] += p["gravity"]
                    p["y"]  += p["vy"]
                    p["x"]  += p["vx"]
                    p["vx"] *= 0.995
                visible = 0 < p["y"] < H + 40
                alpha_c = fade if visible else 0.0
                canvas.coords(oid,
                    p["x"] - p["r"], p["y"] - p["r"],
                    p["x"] + p["r"], p["y"] + p["r"])
                canvas.itemconfig(oid, fill=_alpha_hex(p["col"], alpha_c))

            root.update()
            time.sleep(frame_delay)

        # ── FADE OUT ──────────────────────────────────────────────────────────
        for step in range(22):
            root.attributes("-alpha", max(0.97 * (1 - step/22), 0.0))
            root.update()
            time.sleep(0.016)

        root.destroy()

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    # Note: fire-and-forget for intro; activated phase intentionally
    # joins only if caller needs to wait. Remove th.join() if not needed.