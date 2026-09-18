"""
spawner_panel_image.py
======================

Rendert das Spawner-Handel-Panel als Bild (Karten pro Spawner mit Ankauf-/
Verkaufspreis, "STOP" = Richtung deaktiviert). Rein Pillow, keine externen
Spawner-Icons kommen aus assets/spawners/*.png (Zuordnung über den Namen,
siehe _ICON_FILES); für Spawner ohne Icon wird ein Würfel in Mob-Farbe
gezeichnet. Schrift kommt aus dem System (DejaVu/Arial) bzw. Pillows
eingebautem Font. Farbschema: Pink.

Perspektive der Karten ist die des Kunden:
  DU ERHÄLTST = Ankauf-Preis des Shops (Kunde verkauft an uns)
  DU ZAHLST   = Verkauf-Preis des Shops (Kunde kauft von uns)
"""
from __future__ import annotations

import io
import math
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from utils.price import format_compact_number

WIDTH = 900
MARGIN = 40
CARD_W, CARD_H = 190, 270
CARD_GAP_X, CARD_GAP_Y = 20, 24
COLS = 4
MAX_SPAWNERS = 16

GOLD = (255, 204, 51, 255)
GOLD_DARK = (120, 80, 0, 255)
WHITE = (255, 240, 248, 255)
MUTED = (222, 165, 200, 255)
RED = (255, 90, 100, 255)
PILL_RED = (150, 30, 50, 255)
PILL_GREEN = (25, 120, 70, 255)
PILL_GREY = (100, 60, 92, 255)
CARD_FILL = (58, 20, 52, 235)
CARD_BORDER = (255, 105, 180, 255)
BG_TOP, BG_BOTTOM = (62, 16, 50), (26, 7, 24)
ICON_HEIGHT = 112

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "spawners"
# (Substring im kleingeschriebenen Spawner-Namen, Datei in assets/spawners)
_ICON_FILES: tuple[tuple[str, str], ...] = (
    ("skeleton", "skeleton.png"),
    ("creeper", "creeper.png"),
    ("golem", "iron_golem.png"),
    ("blaze", "blaze.png"),
    ("spider", "spider.png"),
    ("cow", "cow.png"),
    ("piglin", "zombie_piglin.png"),
)

_FONT_CANDIDATES = ("DejaVuSans-Bold.ttf", "arialbd.ttf", "LiberationSans-Bold.ttf")

# Mob-Farben (Substring-Match auf den kleingeschriebenen Spawner-Namen).
_MOB_COLORS: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("skeleton", (215, 215, 220)),
    ("creeper", (76, 175, 80)),
    ("golem", (232, 220, 200)),
    ("blaze", (255, 193, 7)),
    ("cow", (150, 105, 60)),
    ("spider", (150, 30, 40)),
    ("piglin", (229, 143, 160)),
    ("zombie", (74, 143, 74)),
    ("enderman", (122, 63, 191)),
    ("pig", (242, 166, 181)),
    ("witch", (110, 60, 140)),
    ("slime", (110, 200, 90)),
    ("wither", (60, 60, 70)),
    ("guardian", (80, 170, 170)),
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # sehr alte Pillow-Version ohne skalierbaren Default
        return ImageFont.load_default()


def _mob_color(name: str) -> tuple[int, int, int]:
    low = name.lower()
    for key, color in _MOB_COLORS:
        if key in low:
            return color
    hue = zlib.crc32(low.encode()) % 360  # stabile Farbe für unbekannte Namen
    from colorsys import hsv_to_rgb

    r, g, b = hsv_to_rgb(hue / 360, 0.55, 0.85)
    return int(r * 255), int(g * 255), int(b * 255)


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in color) + (255,)  # type: ignore[return-value]


def _text_w(draw: ImageDraw.ImageDraw, text: str, font, stroke: int = 0) -> int:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return box[2] - box[0]


def _center_text(
    draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, font, fill,
    stroke: int = 0, stroke_fill=None,
) -> None:
    w = _text_w(draw, text, font, stroke)
    draw.text((cx - w / 2, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def _spaced_text(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, font, fill, spacing: int) -> None:
    widths = [_text_w(draw, ch, font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + spacing


def _iso_cube(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, w: float, h: float,
    top, left, right, outline=None, line_w: int = 2,
) -> None:
    """Isometrischer Würfel: Mittelpunkt (cx, cy), halbe Breite w, halbe Höhe h."""
    t = (cx, cy - h)
    ur = (cx + w, cy - h / 2)
    lr = (cx + w, cy + h / 2)
    b = (cx, cy + h)
    ll = (cx - w, cy + h / 2)
    ul = (cx - w, cy - h / 2)
    c = (cx, cy)
    if top is not None:
        draw.polygon([t, ur, c, ul], fill=top, outline=outline)
    if left is not None:
        draw.polygon([ul, c, b, ll], fill=left, outline=outline)
    if right is not None:
        draw.polygon([c, ur, lr, b], fill=right, outline=outline)
    if outline is not None:
        for a, z in ((t, ur), (ur, lr), (lr, b), (b, ll), (ll, ul), (ul, t), (ul, c), (c, ur), (c, b)):
            draw.line([a, z], fill=outline, width=line_w)


def _draw_spawner_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color: tuple[int, int, int]) -> None:
    w, h = size * 0.5, size * 0.5
    # Mob im Käfig: kleiner farbiger Würfel innen ...
    _iso_cube(draw, cx, cy, w * 0.62, h * 0.62, _shade(color, 1.05), _shade(color, 0.8), _shade(color, 0.6))
    # ... Käfig (dunkle, halbtransparente Flächen + helle Kanten) darüber.
    _iso_cube(
        draw, cx, cy, w, h,
        (90, 90, 120, 50), (50, 50, 70, 60), (30, 30, 45, 80),
        outline=(120, 120, 150, 255), line_w=2,
    )


@lru_cache(maxsize=32)
def _load_icon(filename: str) -> Optional[Image.Image]:
    try:
        icon = Image.open(ASSETS_DIR / filename).convert("RGBA")
    except OSError:
        return None
    width = max(1, round(icon.width * ICON_HEIGHT / icon.height))
    return icon.resize((width, ICON_HEIGHT), Image.LANCZOS)


def _icon_for(name: str) -> Optional[Image.Image]:
    low = name.lower()
    for key, filename in _ICON_FILES:
        if key in low:
            return _load_icon(filename)
    return None


def _price_text(value: Optional[float]) -> str:
    if value is None:
        return "STOP"
    text = format_compact_number(float(value)).upper()
    return text


def _pill(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, fill, font, w: int = 150, h: int = 28) -> None:
    draw.rounded_rectangle([cx - w // 2, y, cx + w // 2, y + h], radius=h // 2, fill=fill, outline=(255, 255, 255, 60))
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (box[2] - box[0]) / 2, y + (h - (box[3] - box[1])) / 2 - box[1]), text, font=font, fill=WHITE)


def _draw_card(img: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, spawner: dict, fonts: dict) -> None:
    draw.rounded_rectangle([x, y, x + CARD_W, y + CARD_H], radius=14, fill=CARD_FILL, outline=CARD_BORDER, width=2)
    cx = x + CARD_W // 2
    name = str(spawner["name"]).upper()
    if len(name) > 14:
        name = name[:13] + "."
    _center_text(draw, cx, y + 16, name, fonts["name"], WHITE)

    icon = _icon_for(str(spawner["name"]))
    if icon is not None:
        img.paste(icon, (cx - icon.width // 2, y + 108 - icon.height // 2), icon)
    else:
        _draw_spawner_icon(draw, cx, y + 108, 92, _mob_color(str(spawner["name"])))

    buy, sell = spawner.get("buy_price"), spawner.get("sell_price")  # Shop-Ankauf / Shop-Verkauf
    col_l, col_r = x + CARD_W // 4 + 4, x + 3 * CARD_W // 4 - 4
    for col_x, label, value in ((col_l, "DU ERHÄLTST", buy), (col_r, "DU ZAHLST", sell)):
        _center_text(draw, col_x, y + 168, label, fonts["label"], MUTED)
        if value is None:
            _center_text(draw, col_x, y + 191, "STOP", fonts["stop"], RED, stroke=1, stroke_fill=(60, 0, 10, 255))
        else:
            _center_text(draw, col_x, y + 190, _price_text(value), fonts["price"], GOLD, stroke=1, stroke_fill=GOLD_DARK)

    if buy is not None and sell is not None:
        pill, color = "ANKAUF & VERKAUF", PILL_GREEN
    elif buy is not None:
        pill, color = "KEIN VERKAUF", PILL_RED
    elif sell is not None:
        pill, color = "KEIN ANKAUF", PILL_RED
    else:
        pill, color = "PAUSIERT", PILL_GREY
    _pill(draw, cx, y + CARD_H - 40, pill, color, fonts["pill"], w=CARD_W - 40)


def _background(height: int) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, height))
    px = ImageDraw.Draw(img)
    top, bottom = BG_TOP, BG_BOTTOM
    for y in range(height):
        t = y / max(1, height - 1)
        px.line([(0, y), (WIDTH, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    beams = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))
    bd = ImageDraw.Draw(beams)
    bd.polygon([(120, 0), (300, 0), (520, height * 0.7), (40, height * 0.7)], fill=(255, 170, 215, 28))
    bd.polygon([(600, 0), (780, 0), (900, height * 0.7), (470, height * 0.7)], fill=(255, 170, 215, 22))
    return Image.alpha_composite(img, beams).convert("RGB")


def render_spawner_panel(spawners: list[dict], brand: str = "TXTEMPIRE") -> bytes:
    """PNG-Bytes des Panels. spawners: Zeilen aus der spawners-Tabelle
    (name, buy_price, sell_price)."""
    items = spawners[:MAX_SPAWNERS]
    rows = max(1, math.ceil(len(items) / COLS))
    grid_top = 330
    grid_bottom = grid_top + rows * CARD_H + (rows - 1) * CARD_GAP_Y
    height = grid_bottom + 190

    img = _background(height)
    draw = ImageDraw.Draw(img, "RGBA")
    fonts = {
        "brand": _font(20),
        "title": _font(84),
        "sub": _font(24),
        "name": _font(20),
        "label": _font(12),
        "price": _font(28),
        "stop": _font(26),
        "pill": _font(13),
        "badge": _font(15),
        "banner": _font(19),
    }

    # Header: Diamant, Marke, Titel, Untertitel
    cx = WIDTH // 2
    draw.polygon([(cx, 40), (cx + 22, 66), (cx, 96), (cx - 22, 66)], fill=(255, 120, 190, 255), outline=(255, 215, 235, 255))
    draw.polygon([(cx - 22, 66), (cx + 22, 66), (cx, 96)], fill=(215, 65, 150, 255))
    _spaced_text(draw, cx, 120, f"{brand}  ·  SPAWNER", fonts["brand"], MUTED, 5)
    _center_text(draw, cx, 160, "SPAWNER HANDEL", fonts["title"], GOLD, stroke=3, stroke_fill=GOLD_DARK)
    _center_text(draw, cx, 268, "Kaufen & verkaufen — schnell, sicher, per Ticket", fonts["sub"], WHITE)

    if not items:
        _center_text(draw, cx, grid_top + 90, "Noch keine Spawner konfiguriert", fonts["sub"], MUTED)
    for row in range(rows):
        row_items = items[row * COLS:(row + 1) * COLS]
        row_w = len(row_items) * CARD_W + (len(row_items) - 1) * CARD_GAP_X
        x0 = (WIDTH - row_w) // 2
        y = grid_top + row * (CARD_H + CARD_GAP_Y)
        for i, spawner in enumerate(row_items):
            _draw_card(img, draw, x0 + i * (CARD_W + CARD_GAP_X), y, spawner, fonts)

    # Badges
    by = grid_bottom + 28
    badges = ("SICHER PER TICKET", "SCHNELLE ABWICKLUNG", "BEWERTUNGEN: /vouch")
    bw, bgap = 260, 12
    bx0 = (WIDTH - (3 * bw + 2 * bgap)) // 2
    for i, text in enumerate(badges):
        x = bx0 + i * (bw + bgap)
        draw.rounded_rectangle([x, by, x + bw, by + 44], radius=22, fill=CARD_FILL, outline=CARD_BORDER, width=2)
        group_w = 20 + 12 + _text_w(draw, text, fonts["badge"])
        gx = x + (bw - group_w) // 2
        draw.ellipse([gx, by + 12, gx + 20, by + 32], fill=GOLD)
        draw.text((gx + 32, by + 12), text, font=fonts["badge"], fill=WHITE)

    # Banner
    ry = by + 70
    draw.rounded_rectangle([MARGIN, ry, WIDTH - MARGIN, ry + 56], radius=28, fill=(255, 190, 40, 255), outline=(255, 235, 150, 255), width=2)
    for tx in (MARGIN + 34, WIDTH - MARGIN - 34):
        draw.polygon([(tx - 9, ry + 22), (tx + 9, ry + 22), (tx, ry + 36)], fill=(60, 30, 0, 255))
    _center_text(draw, cx, ry + 17, "SPAWNER KAUFEN & VERKAUFEN — BUTTONS DIREKT UNTER DEM PANEL", fonts["banner"], (60, 30, 0, 255))

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def _self_check() -> None:
    """ponytail: Render läuft für leere/volle/ungewöhnliche Eingaben durch und liefert ein PNG."""
    sample = [
        {"name": "Skeleton", "buy_price": 12_500_000, "sell_price": None},
        {"name": "Creeper", "buy_price": None, "sell_price": 6_000_000},
        {"name": "Iron Golem", "buy_price": 10_000_000, "sell_price": None},
        {"name": "Blaze", "buy_price": 3_500_000, "sell_price": None},
        {"name": "Cow", "buy_price": 3_000_000, "sell_price": None},
        {"name": "Spider", "buy_price": 3_000_000, "sell_price": None},
        {"name": "Zombie Piglin", "buy_price": 3_000_000, "sell_price": 4_000_000},
    ]
    for data in ([], sample[:1], sample, sample * 3):
        png = render_spawner_panel(data)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000
    assert _price_text(None) == "STOP" and _price_text(12_500_000) == "12.5M"
    for name in ("Skeleton", "Creeper", "Iron Golem", "Blaze", "Spider", "Cow", "Zombie Piglin"):
        assert _icon_for(name) is not None, f"Icon fehlt für {name}"
    assert _icon_for("Enderman") is None  # ohne Icon -> gezeichneter Würfel


if __name__ == "__main__":
    import sys

    _self_check()
    if len(sys.argv) > 1:
        with open(sys.argv[1], "wb") as fh:
            fh.write(render_spawner_panel([
                {"name": "Skeleton", "buy_price": 12_500_000, "sell_price": None},
                {"name": "Creeper", "buy_price": None, "sell_price": 6_000_000},
                {"name": "Iron Golem", "buy_price": 10_000_000, "sell_price": None},
                {"name": "Blaze", "buy_price": 3_500_000, "sell_price": None},
                {"name": "Cow", "buy_price": 3_000_000, "sell_price": None},
                {"name": "Spider", "buy_price": 3_000_000, "sell_price": None},
                {"name": "Zombie Piglin", "buy_price": 3_000_000, "sell_price": 4_000_000},
            ]))
    print("OK")
