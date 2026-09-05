from __future__ import annotations


def compute_code_discount(
    total: float, discount_type: str, discount_value: float
) -> tuple[float, float]:
    """Berechnet (neuer_total, ersparnis) für einen Rabattcode."""
    original = float(total)
    if original <= 0:
        raise ValueError("Bestellung hat keinen Betrag.")
    dtype = (discount_type or "").strip().lower()
    value = float(discount_value)
    if dtype == "percent":
        if value <= 0 or value > 100:
            raise ValueError("Prozent-Rabatt muss zwischen 0 und 100 liegen.")
        savings = round(original * (value / 100.0), 2)
    elif dtype == "amount":
        if value <= 0:
            raise ValueError("Betrags-Rabatt muss größer als 0 sein.")
        savings = round(min(value, original), 2)
    else:
        raise ValueError("Ungültiger Rabatt-Typ.")
    new_total = round(max(original - savings, 0.01), 2)
    if new_total >= original:
        raise ValueError("Rabatt ändert den Preis nicht.")
    savings = round(original - new_total, 2)
    return new_total, savings


def format_code_discount(discount_type: str, discount_value: float) -> str:
    if discount_type == "percent":
        return f"−{float(discount_value):g}%"
    from utils.embeds import format_price

    return f"−{format_price(float(discount_value))}"
