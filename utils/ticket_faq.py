"""Einfache Keyword-FAQ für Shop-Tickets."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# channel_id → last reply timestamp
_cooldowns: dict[int, float] = {}
COOLDOWN_SEC = 45.0


@dataclass(frozen=True)
class FaqEntry:
    keys: tuple[str, ...]
    answer: str


FAQ: tuple[FaqEntry, ...] = (
    FaqEntry(
        ("wie zahle", "wie bezahl", "zahlung", "überweis", "paypal", "iban", "geld senden"),
        "**Zahlung:** Die genauen Zahlungsdaten stehen im Ticket-Embed oben "
        "(Empfänger + Betrag).\n"
        "Überweise den **gesamten Betrag** an den angegebenen Empfänger "
        "(TxtEmpire), dann **Payment beweisen**.",
    ),
    FaqEntry(
        ("payment beweis", "beweis", "proof", "quittung", "screenshot zahlung"),
        "**Payment beweisen:** Button **Payment beweisen** → IGN eintragen "
        "und einen Screenshot der Zahlung anhängen.\n"
        "Danach wartet Staff auf die Bestätigung.",
    ),
    FaqEntry(
        ("wie lange", "wartezeit", "wann bekomm", "wie schnell", "dauer"),
        "Nach dem Zahlungsbeweis bestätigt Staff den Kauf. "
        "Packs/Rollen kommen danach automatisch (oft wenige Minuten, "
        "je nach Staff-Auslastung).",
    ),
    FaqEntry(
        ("pack", "lieferung", "download", "dm", "datei bekomm"),
        "Nach Bestätigung wird das **Pack per DM** (Text/Datei) und ggf. "
        "als Link im Ticket geliefert. DMs vom Server bitte offen lassen.",
    ),
    FaqEntry(
        ("credit", "guthaben", "coins"),
        "**Credits:** Am Buy-Panel unter **Credits** kaufen. "
        "Bei aktiviertem Credits-Panel kannst du mit **Quick Buy** "
        "direkt ohne Überweisung zahlen (wenn genug Guthaben).",
    ),
    FaqEntry(
        ("rabatt", "creator code", "gutschein", "discount", "code"),
        "Im Ticket gibt es den Button **Rabatt / Creator Code**. "
        "Gib deinen Code ein — der Preis wird angepasst (sofern gültig).",
    ),
    FaqEntry(
        ("vouch", "bewertung", "review", "sterne"),
        "Nach erfolgreichem Kauf kannst du einmalig bewerten: "
        "`/vouch` (Server oder DM) oder über die Sterne-Buttons in der DM.",
    ),
    FaqEntry(
        ("abbrechen", "stornier", "cancel", "schließen"),
        "Kauf abbrechen: Button **Kauf abbrechen** oder `/order cancel`. "
        "Staff kann das Ticket mit **Ticket schließen** entfernen.",
    ),
    FaqEntry(
        ("ign", "minecraft name", "spielername", "ingame"),
        "Dein **IGN** (Ingame-Name) wird beim **Payment beweisen** abgefragt "
        "und in der Bestellung gespeichert.",
    ),
    FaqEntry(
        ("rolle", "autorole", "customer", "zugang"),
        "Nach Bestätigung vergibt der Bot die hinterlegten Rollen "
        "(Customer / Item- / Kategorie-Rolle), sofern konfiguriert.",
    ),
    FaqEntry(
        ("scan premium", "scanner", "malware", "rat scan"),
        "File-Scanner: Panel **Datei hier droppen** oder `/scan file`. "
        "Premium erhöht das Tageslimit — Kauf über das Scan-Panel.",
    ),
    FaqEntry(
        ("hilfe", "help", "was tun", "wie geht", "anleitung"),
        "**Kurzablauf:** 1) Betrag überweisen → 2) **Payment beweisen** "
        "(IGN + Screenshot) → 3) Staff bestätigt → 4) Pack/Rollen kommen.\n"
        "Bei Problemen einfach warten — Staff sieht dein Ticket.",
    ),
)


def _normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def match_faq(content: str) -> str | None:
    """Gibt FAQ-Antwort oder None zurück."""
    text = _normalize(content)
    if len(text) < 4 or len(text) > 280:
        return None
    # Avoid matching pure order chatter / only mentions
    if text.startswith("http") or text.count(" ") > 40:
        return None
    for entry in FAQ:
        if any(k in text for k in entry.keys):
            return entry.answer
    return None


def faq_cooldown_ok(channel_id: int) -> bool:
    now = time.monotonic()
    last = _cooldowns.get(channel_id, 0.0)
    if now - last < COOLDOWN_SEC:
        return False
    _cooldowns[channel_id] = now
    return True
