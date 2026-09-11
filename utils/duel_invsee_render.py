"""Rendert einen Duel-Invsee-Inventar-Snapshot als PNG (für DMs).

Kein Zugriff auf echte Minecraft-Texturen nötig — einfache Kachel-Grafik mit
Item-Namen + Stückzahl, im selben Look wie die Website-Ansicht.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

TILE = 74
GAP = 6
COLS = 9
PAD = 16
BG = (20, 12, 17)
TILE_BG = (34, 22, 30)
TILE_BORDER = (70, 40, 55)
TEXT = (235, 225, 230)
GOLD = (240, 190, 90)


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def render_inventory_image(items: list[dict], opponent_ign: str) -> bytes:
    """items: [{"type": "minecraft:diamond_sword", "amount": 1, "group": "main"|"armor"|"offhand", "slot": int}, ...]"""
    shown = [i for i in items if i.get("type")]
    rows = max(1, (len(shown) + COLS - 1) // COLS) if shown else 1

    header_h = 44
    width = PAD * 2 + COLS * TILE + (COLS - 1) * GAP
    height = header_h + PAD * 2 + rows * TILE + max(0, rows - 1) * GAP

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    title_font = _font(22)
    label_font = _font(11)
    count_font = _font(14)

    draw.text((PAD, 12), f"Live-Inventar · {opponent_ign}", font=title_font, fill=GOLD)

    if not shown:
        draw.text((PAD, header_h + PAD), "Inventar ist leer.", font=label_font, fill=TEXT)
    else:
        for idx, item in enumerate(shown):
            col = idx % COLS
            row = idx // COLS
            x = PAD + col * (TILE + GAP)
            y = header_h + PAD + row * (TILE + GAP)
            draw.rounded_rectangle(
                [x, y, x + TILE, y + TILE], radius=10, fill=TILE_BG, outline=TILE_BORDER, width=2
            )
            name = str(item.get("type") or "").replace("minecraft:", "").replace("_", " ")
            _draw_wrapped(draw, name, x + 6, y + 8, TILE - 12, label_font, TEXT)
            amount = item.get("amount")
            if amount is not None:
                draw.text((x + 6, y + TILE - 20), f"×{amount}", font=count_font, fill=GOLD)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_wrapped(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, max_w: int, font, fill) -> None:
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        w = draw.textlength(candidate, font=font)
        if w <= max_w or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    for i, line in enumerate(lines[:3]):
        draw.text((x, y + i * 12), line, font=font, fill=fill)
