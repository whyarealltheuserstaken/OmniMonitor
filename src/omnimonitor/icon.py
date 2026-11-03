"""Tray icon: a ring gauge with the remaining % in the middle (Pillow only)."""

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


def render_icon(remaining: Optional[float], *, error: bool = False, demo: bool = False,
                size: int = 64) -> Image.Image:
    """remaining=None -> gray '?' (no data). error -> gray ring with '!'."""
    scale = 4  # supersample for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad, width = 3 * scale, 7 * scale
    box = [pad, pad, s - pad, s - pad]
    col = GRAY if (error or remaining is None) else color_for(remaining)
    d.ellipse(box, fill=(24, 24, 24, 235))
    d.arc(box, 0, 360, fill=(70, 70, 70, 255), width=width)
    if remaining is not None and not error:
        pct = max(0.0, min(100.0, remaining))
        if pct > 0:
            d.arc(box, -90, -90 + 360 * pct / 100.0, fill=col + (255,), width=width)
    if error:
        text = "!"
    elif remaining is None:
        text = "?"
    else:
        text = "%d" % round(max(0.0, min(100.0, remaining)))
    fsize = {1: 34, 2: 30, 3: 22}.get(len(text), 22) * scale
    font = _font(fsize)
    try:
        bb = d.textbbox((0, 0), text, font=font)
        tw, th, ox, oy = bb[2] - bb[0], bb[3] - bb[1], bb[0], bb[1]
    except AttributeError:  # very old Pillow
        tw, th = d.textsize(text, font=font)  # type: ignore[attr-defined]
        ox = oy = 0
    d.text(((s - tw) / 2 - ox, (s - th) / 2 - oy), text, font=font, fill=(245, 245, 245, 255))
    if demo:
        r = 9 * scale
        d.ellipse([s - 2 * r - scale, scale, s - scale, 2 * r + scale], fill=DEMO + (255,))
    img = img.resize((size, size), Image.LANCZOS)
    return img


def tray_size() -> int:
    # Windows tray scales down 64 well; macOS menu bar likes ~44 (22pt @2x).
    return 44 if sys.platform == "darwin" else 64
