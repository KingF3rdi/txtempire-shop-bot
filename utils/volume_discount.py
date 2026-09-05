"""Automatischer Mengenrabatt nach Pack-Anzahl."""

from __future__ import annotations

# (min_packs, percent) — höchste passende Stufe gilt
VOLUME_TIERS: tuple[tuple[int, float], ...] = (
    (20, 15.0),
    (15, 10.0),
    (10, 7.0),
    (5, 5.0),
)

# Rollen-Schwellen (min Packs → settings key)
VOLUME_ROLE_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (10, "volume_role_10_id"),
    (15, "volume_role_15_id"),
    (20, "volume_role_20_id"),
)


def pack_qty_from_rows(rows: list[dict]) -> int:
    return sum(int(r.get("qty") or 1) for r in rows)


def volume_discount_percent(pack_qty: int) -> float:
    for minimum, pct in VOLUME_TIERS:
        if pack_qty >= minimum:
            return pct
    return 0.0


def apply_volume_discount(
    subtotal: float, pack_qty: int
) -> tuple[float, float, float]:
    """
    Returns (new_total, savings, percent).
    """
    pct = volume_discount_percent(pack_qty)
    if pct <= 0 or subtotal <= 0:
        return round(float(subtotal), 2), 0.0, 0.0
    savings = round(float(subtotal) * (pct / 100.0), 2)
    new_total = round(max(float(subtotal) - savings, 0.01), 2)
    savings = round(float(subtotal) - new_total, 2)
    return new_total, savings, pct


def format_volume_tiers_help() -> str:
    return (
        "**Mengenrabatt (Packs = Stückzahl im Warenkorb):**\n"
        "• ab **5** Packs → **5 %**\n"
        "• ab **10** Packs → **7 %** + Rolle\n"
        "• ab **15** Packs → **10 %** + Rolle\n"
        "• ab **20** Packs → **15 %** + Rolle"
    )


def volume_role_ids_for_qty(settings: dict, pack_qty: int) -> list[tuple[int, str]]:
    """Rollen die bei dieser Pack-Anzahl vergeben werden (alle erreichten Stufen)."""
    out: list[tuple[int, str]] = []
    for minimum, key in VOLUME_ROLE_THRESHOLDS:
        if pack_qty < minimum:
            continue
        rid = settings.get(key)
        if rid:
            out.append((int(rid), f"Mengen-Rolle ({minimum}+ Packs)"))
    return out
