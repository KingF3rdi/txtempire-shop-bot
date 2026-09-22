"""Tool Credits — eigene Anzeige-Einheit für die "Tools"-Überkategorie
(aktuell: Duel Invsee), analog zu utils/credits.py (1 Credit = 100k).
Bezahlt wird weiterhin aus demselben Guthaben (user_credits.balance) —
Tool Credits sind kein zweites Konto, nur eine kleinere, zum Tool-Preis
passende Einheit für die Anzeige."""
from __future__ import annotations

# 1 Tool Credit = 50 000 Shop-Währung
TOOL_CREDIT_VALUE = 50_000.0


def currency_to_tool_credits(amount: float) -> float:
    return float(amount) / TOOL_CREDIT_VALUE


def format_tool_credits(amount: float) -> str:
    tc = currency_to_tool_credits(amount)
    if abs(tc - round(tc)) < 1e-9:
        return f"{int(round(tc))}"
    return f"{tc:.2f}".rstrip("0").rstrip(".")
