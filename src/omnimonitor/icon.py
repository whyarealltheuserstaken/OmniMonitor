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


def color_for(remaining: Optional[float]) -> tuple:
    if remaining is None:
        return GRAY
    if remaining >= 50:
        return GREEN
    if remaining >= 20:
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
    scale = 4  # supersample for smooth edges
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
        d.ellipse(box, fill=(24, 24, 24, 235))
        d.arc(box, 0, 360, fill=(70, 70, 70, 255), width=width)
        if pct:
            d.arc(box, -90, -90 + 360 * pct / 100.0, fill=col + (255,), width=width)
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
    if style == "number":
        fsize = {1: 44, 2: 38, 3: 26}.get(len(text), 26) * scale
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
        r = 9 * scale
        d.ellipse([s - 2 * r - scale, scale, s - scale, 2 * r + scale], fill=DEMO + (255,))
    img = img.resize((size, size), Image.LANCZOS)
    return img


def tray_size() -> int:
    # Windows tray scales down 64 well; macOS menu bar likes ~44 (22pt @2x).
    return 44 if sys.platform == "darwin" else 64
