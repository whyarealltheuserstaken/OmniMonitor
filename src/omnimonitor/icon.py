"""Tray icon (Pillow only): ring | pie | bar | number styles, remaining or used %."""

from __future__ import annotations

import sys
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

GREEN, YELLOW, RED = (63, 185, 80), (210, 153, 34), (248, 81, 73)
GRAY, DEMO = (140, 140, 140), (120, 160, 255)
_FONT_CANDIDATES = [
    "DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "Helvetica.ttc",
    "/System/Library/Fonts/SFNSRounded.ttf", "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:\\Windows\\Fonts\\arialbd.ttf",
]


# Remaining-pool tray colors (Henry, 2026-11-05): green ≥ 40 %, amber 20–<40 %, red < 20 %.
# Same thresholds for macOS menu bar and Windows/Linux gauges.
OK_REMAINING = 40.0
WARN_REMAINING = 20.0


def color_for(remaining: Optional[float]) -> tuple:
    if remaining is None:
        return GRAY
    if remaining >= OK_REMAINING:
        return GREEN
    if remaining >= WARN_REMAINING:
        return YELLOW
    return RED


def _font(size: int):
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


STYLES = ("ring", "pie", "bar", "number")


def render_icon(remaining: Optional[float], *, error: bool = False, demo: bool = False,
                size: int = 64, style: str = "ring", show: str = "remaining") -> Image.Image:
    """remaining=None -> gray '?' (no data). error -> gray '!'.

    style: ring (default) | pie | bar | number. show: "remaining" or "used" - only the
    number (and the gauge fill) flip; the color always follows what is LEFT.
    """
    style = style if style in STYLES else "ring"
    scale = 8  # supersample for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, width = 3 * scale, 7 * scale
    box = [pad, pad, s - pad, s - pad]
    col = GRAY if (error or remaining is None) else color_for(remaining)
    pct = None if remaining is None or error else max(0.0, min(100.0, remaining))
    if pct is not None and show == "used":
        pct = 100.0 - pct
    if style == "ring":
        pad, width = 2 * s // 64, (9 if size >= 24 else 16) * s // 64
        box = [pad, pad, s - pad, s - pad]
        d.ellipse(box, fill=(28, 30, 34, 255))
        inset = width // 2
        rbox = [pad + inset, pad + inset, s - pad - inset, s - pad - inset]
        d.arc(rbox, 0, 360, fill=(255, 255, 255, 46), width=width)
        if pct:
            import math
            a0, a1 = -90, -90 + 360 * pct / 100.0
            d.arc(rbox, a0, a1, fill=col + (255,), width=width)
            r = (rbox[2] - rbox[0]) / 2 - width / 2
            c = s / 2
            for a in (a0, a1):
                px, py = c + r * math.cos(math.radians(a)), c + r * math.sin(math.radians(a))
                d.ellipse([px - width / 2, py - width / 2, px + width / 2, py + width / 2],
                          fill=col + (255,))
    elif style == "pie":
        d.ellipse(box, fill=(70, 70, 70, 255))
        if pct:
            d.pieslice(box, -90, -90 + 360 * pct / 100.0, fill=col + (255,))
        inner = 15 * scale
        d.ellipse([inner, inner, s - inner, s - inner], fill=(24, 24, 24, 235))
    elif style == "bar":
        d.rounded_rectangle(box, radius=10 * scale, fill=(24, 24, 24, 235))
        bh = 10 * scale
        top = s - pad - bh - 3 * scale
        d.rounded_rectangle([pad + 5 * scale, top, s - pad - 5 * scale, top + bh],
                            radius=4 * scale, fill=(70, 70, 70, 255))
        if pct:
            right = pad + 5 * scale + (s - 2 * pad - 10 * scale) * pct / 100.0
            d.rounded_rectangle([pad + 5 * scale, top, max(right, pad + 9 * scale), top + bh],
                                radius=4 * scale, fill=col + (255,))
    else:  # number
        d.rounded_rectangle(box, radius=12 * scale, fill=col + (255,) if pct is not None
                            else (24, 24, 24, 235))
    if error:
        text = "!"
    elif pct is None:
        text = "?"
    else:
        text = "%d" % round(pct)
    fsize = {1: 34, 2: 30, 3: 22}.get(len(text), 22) * scale
    if style == "ring":
        fsize = {1: 32, 2: 29, 3: 20}.get(len(text), 20) * s // 64
    if style == "number":
        fsize = {1: 44, 2: 38, 3: 26}.get(len(text), 26) * scale
    if style == "ring" and size < 24:
        text = ""  # digits are illegible at 16-20 px; the arc carries the value
    font = _font(fsize)
    try:
        bb = d.textbbox((0, 0), text, font=font)
        tw, th, ox, oy = bb[2] - bb[0], bb[3] - bb[1], bb[0], bb[1]
    except AttributeError:  # very old Pillow
        tw, th = d.textsize(text, font=font)  # type: ignore[attr-defined]
        ox = oy = 0
    ty = (s - th) / 2 - oy - (7 * scale if style == "bar" else 0)
    fg = (20, 20, 20, 255) if style == "number" and pct is not None else (245, 245, 245, 255)
    d.text(((s - tw) / 2 - ox, ty), text, font=font, fill=fg)
    if demo:
        r = 7 * s // 64
        m = s // 64
        d.ellipse([s - 2 * r - m, m, s - m, 2 * r + m], fill=(20, 20, 20, 255))
        d.ellipse([s - 2 * r + m, 3 * m, s - 3 * m, 2 * r - m], fill=DEMO + (255,))
    img = img.resize((size, size), Image.LANCZOS)
    return img


# ------------------------------------------------------------------ macOS menu bar
MENUBAR_PT = 22  # NSStatusBar thickness in points
_MAC_FONTS = ["/System/Library/Fonts/SFNSRounded.ttf", "/System/Library/Fonts/SFNS.ttf",
              "/System/Library/Fonts/SFNSText.ttf", "/System/Library/Fonts/Helvetica.ttc",
              "/Library/Fonts/Arial Bold.ttf", "DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def _mac_font(px: int):
    for name in _MAC_FONTS:
        try:
            f = ImageFont.truetype(name, px)
            try:  # SF is a variable font: ask for semibold where supported
                f.set_variation_by_name("Semibold")
            except Exception:  # noqa: BLE001
                pass
            return f
        except (OSError, IOError):
            continue
    return _font(px)


def menubar_state(remaining: Optional[float], error: bool = False) -> str:
    if error:
        return "error"
    if remaining is None:
        return "none"
    if remaining < WARN_REMAINING:
        return "critical"
    if remaining < OK_REMAINING:
        return "warn"
    return "ok"


def render_menubar(remaining: Optional[float], *, error: bool = False, demo: bool = False,
                   show: str = "remaining", text: bool = True, scale: int = 2):
    """Crisp macOS status-bar image. Returns (PIL image, is_template).

    Height is exactly 22pt * scale px (44 px at 2x), width fits the content. Drawn 8x
    supersampled, then LANCZOS-reduced.

    When remaining % is known (ok / warn / critical), the ring and optional % label are
    colored green / amber / red and ``is_template`` is False — NSImage templates are
    monochrome, so a colored gauge cannot be a template. The gauge is one stroke: a dim
    track and the colored arc share the same radius and width. There is no outline, no
    second ring, and no filled disc. Error and "no data" stay template (black + alpha).
    """
    state = menubar_state(remaining, error)
    # Colored states cannot be templates (macOS would discard the RGB).
    template = state in ("error", "none")
    ss = 8
    u = scale * ss  # pixels per point while drawing
    h = MENUBAR_PT * u
    pct = None if state in ("none", "error") else max(0.0, min(100.0, float(remaining)))
    shown = None if pct is None else (100.0 - pct if show == "used" else pct)
    label = "" if state == "error" else "–" if pct is None else "%d%%" % round(shown)
    font = _mac_font(int(12.5 * u))
    tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
    bb = tmp.textbbox((0, 0), label, font=font)
    tw = bb[2] - bb[0]
    ring_d = 15 * u
    gap = 3.5 * u
    # Pad so the centered stroke is not clipped. This is empty margin, not another ring.
    pad = int(1.6 * u)
    w = int(ring_d + 2 * pad + ((gap + tw + pad) if text and label else 0))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if template:
        ink, track = (0, 0, 0), (0, 0, 0, 80)
    else:
        ink = color_for(remaining)  # green / amber / red from remaining
        track = ink + (110,)
    import math
    x0, y0 = pad, (h - ring_d) / 2
    box = [x0, y0, x0 + ring_d, y0 + ring_d]
    stroke = int(2.4 * u)

    def _arc(fill, width, a0=0, a1=360, caps=False):
        d.arc(box, a0, a1, fill=fill, width=width)
        if caps and a1 != a0:
            r = ring_d / 2 - width / 2
            cx, cy = x0 + ring_d / 2, y0 + ring_d / 2
            for a in (a0, a1):
                px = cx + r * math.cos(math.radians(a))
                py = cy + r * math.sin(math.radians(a))
                d.ellipse([px - width / 2, py - width / 2, px + width / 2, py + width / 2],
                          fill=fill if len(fill) == 4 else fill + (255,))

    # One stroke only. A wider dark arc around this circle showed up as an inner black ring.
    _arc(track, stroke)
    if shown:
        a0, a1 = -90, -90 + 360 * shown / 100.0
        _arc(ink + (255,), stroke, a0, a1, caps=True)
    if state == "error" or (pct is None and not text):
        cx, cy = x0 + ring_d / 2, y0 + ring_d / 2
        d.rounded_rectangle([cx - 0.9 * u, cy - 4 * u, cx + 0.9 * u, cy + 1.2 * u],
                            radius=0.9 * u, fill=ink + (255,))
        d.ellipse([cx - 1 * u, cy + 2.3 * u, cx + 1 * u, cy + 4.3 * u], fill=ink + (255,))
    if demo:  # small solid dot badge, top right of the ring (DEMO blue when colored)
        r = 2.6 * u
        cx, cy = x0 + ring_d - r * 0.6, y0 + r * 0.6
        d.ellipse([cx - r - 1.2 * u, cy - r - 1.2 * u, cx + r + 1.2 * u, cy + r + 1.2 * u],
                  fill=(0, 0, 0, 0))
        dot = DEMO if not template else ink
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot + (255,))
    if text and label:
        asc, desc = font.getmetrics()
        ty = (h - (asc + desc)) / 2 + 0.3 * u
        tx = x0 + ring_d + gap - bb[0]
        if not template:
            # Single dark edge so the % stays readable on a light bar. No light halo.
            step = 0.4 * u
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                           (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7)):
                d.text((tx + dx * step, ty + dy * step), label, font=font, fill=(0, 0, 0, 170))
        d.text((tx, ty), label, font=font, fill=ink + (255,))
    out = img.resize((max(1, w // ss), MENUBAR_PT * scale), Image.LANCZOS)
    return out, template


def tray_size() -> int:
    # Windows tray scales down 64 well; macOS menu bar likes ~44 (22pt @2x).
    return 44 if sys.platform == "darwin" else 64
