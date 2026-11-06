"""Render docs/tray-icons.png and docs/menubar-ring.png (illustrative only)."""
from pathlib import Path

from PIL import Image, ImageDraw

from omnimonitor.icon import STYLES, render_icon, render_menubar

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
    render_menubar_sheet()


def render_menubar_sheet() -> None:
    """One ring on light and dark bars: green (≥40), amber (20–40), red (<20)."""
    samples = [(73, "73% left · green"), (30, "30% left · amber"), (12, "12% left · red")]
    bars = [((246, 246, 246, 255), "Light menu bar"), ((28, 28, 30, 255), "Dark menu bar")]
    pad, label_w, zoom = 16, 150, 3
    icons = []
    for rem, _cap in samples:
        ic, _tpl = render_menubar(rem, text=True)
        icons.append(ic)
    cell_w = max(ic.width for ic in icons) * zoom + pad
    cell_h = icons[0].height * zoom + 28
    w = label_w + len(samples) * cell_w + pad
    h = 28 + len(bars) * (cell_h + pad)
    sheet = Image.new("RGBA", (w, h), (20, 20, 20, 255))
    d = ImageDraw.Draw(sheet)
    for j, (_rem, cap) in enumerate(samples):
        d.text((label_w + j * cell_w, 6), cap, fill=(210, 210, 210, 255))
    for i, (bg, name) in enumerate(bars):
        y = 28 + i * (cell_h + pad)
        d.text((8, y + 8), name, fill=(220, 220, 220, 255))
        for j, ic in enumerate(icons):
            big = ic.resize((ic.width * zoom, ic.height * zoom), Image.NEAREST)
            tile = Image.new("RGBA", (big.width + 8, big.height + 8), bg)
            tile.alpha_composite(big, (4, 4))
            sheet.alpha_composite(tile, (label_w + j * cell_w, y))
    out = ROOT / "docs" / "menubar-ring.png"
    sheet.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
