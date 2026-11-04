"""Render docs/tray-icons.png: every icon style at a few sample levels (illustrative only)."""
from pathlib import Path

from PIL import Image, ImageDraw

from omnimonitor.icon import STYLES, render_icon

ROOT = Path(__file__).resolve().parent.parent
LEVELS = [(85, False), (45, False), (12, False), (None, False), (None, True)]
CELL, PAD, LABEL = 64, 14, 70


def main() -> None:
    w = LABEL + len(LEVELS) * (CELL + PAD) + PAD
    h = len(STYLES) * (CELL + PAD) + PAD + 18
    img = Image.new("RGBA", (w, h), (24, 24, 24, 255))
    d = ImageDraw.Draw(img)
    for j, (lvl, err) in enumerate(LEVELS):
        txt = "error" if err else ("no data" if lvl is None else "%d%% left" % lvl)
        d.text((LABEL + PAD + j * (CELL + PAD), 4), txt, fill=(170, 170, 170, 255))
    for i, style in enumerate(STYLES):
        y = 18 + PAD + i * (CELL + PAD)
        d.text((8, y + CELL // 2 - 6), style, fill=(220, 220, 220, 255))
        for j, (lvl, err) in enumerate(LEVELS):
            ic = render_icon(lvl, error=err, size=CELL, style=style)
            img.alpha_composite(ic, (LABEL + PAD + j * (CELL + PAD), y))
    img.save(ROOT / "docs" / "tray-icons.png")
    print("saved docs/tray-icons.png")


if __name__ == "__main__":
    main()
