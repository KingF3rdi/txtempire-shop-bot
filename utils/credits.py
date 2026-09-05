from __future__ import annotations

import math

# 1 Credit = 100 000 Shop-Währung (z.B. 500k = 5 Credits)
CREDIT_VALUE = 100_000.0


def currency_to_credits(amount: float) -> float:
    """Wandelt Währungsbetrag in Credits um (exakt)."""
    return float(amount) / CREDIT_VALUE


def credits_to_currency(credits: float) -> float:
    return float(credits) * CREDIT_VALUE


def credits_needed_for_total(total: float) -> float:
    """Credits für einen Bestellbetrag — auf 2 Nachkommastellen gerundet."""
    return round(currency_to_credits(total), 2)


def format_credits(amount: float) -> str:
    a = float(amount)
    if abs(a - round(a)) < 1e-9:
        return f"{int(round(a))}"
    return f"{a:.2f}".rstrip("0").rstrip(".")


def parse_credits_amount(raw: str) -> float:
    """Parst Credit-Anzahl (ganze Zahlen oder Dezimal, z.B. 5 / 2.5)."""
    s = (raw or "").strip().replace(",", ".")
    if not s:
        raise ValueError("Bitte eine Credit-Anzahl angeben.")
    try:
        value = float(s)
    except ValueError as e:
        raise ValueError("Ungültige Credit-Anzahl.") from e
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Credit-Anzahl muss größer als 0 sein.")
    if value > 1_000_000:
        raise ValueError("Zu viele Credits auf einmal (max. 1 000 000).")
    return round(value, 2)
