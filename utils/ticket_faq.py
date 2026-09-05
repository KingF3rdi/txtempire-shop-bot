"""Einfache Keyword-FAQ für Shop- und Service-Tickets."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# channel_id → last reply timestamp
_cooldowns: dict[int, float] = {}
COOLDOWN_SEC = 25.0


@dataclass(frozen=True)
class FaqEntry:
    keys: tuple[str, ...]
    answer: str


# Reihenfolge egal — es gewinnt der längste Keyword-Treffer.
FAQ: tuple[FaqEntry, ...] = (
    FaqEntry(
        (
            "wie kaufe ich",
            "wie kauft man",
            "wie bestelle",
            "wie bestellen",
            "pack kaufen",
            "packs kaufen",
            "wo kaufe",
            "wo kaufen",
            "wie shoppe",
            "buy panel",
            "buypanel",
            "shop panel",
            "wie hol ich",
            "wie bekomme ich ein pack",
            "wie bekomm ich ein pack",
            "texturepack kaufen",
            "produkt kaufen",
        ),
        "**Packs kaufen — so geht’s:**\n"
        "1) Im **Buy-Panel** / Shop-Kanal Kategorie wählen und Produkt in den "
        "Warenkorb legen\n"
        "2) **Kaufen / Checkout** → es öffnet sich dein privates Ticket\n"
        "3) Betrag laut Ticket **überweisen** (Empfänger steht im Embed)\n"
        "4) **Payment beweisen** (IGN + Screenshot)\n"
        "5) Staff bestätigt → Pack kommt per **DM** (+ ggf. Rolle)\n\n"
        "Tipp: Mit genug **Credits** geht oft auch **Quick Buy** ohne Überweisung.",
    ),
    FaqEntry(
        (
            "wie zahle",
            "wie bezahl",
            "wie bezahle",
            "wie kann ich zahl",
            "wie kann ich bezahl",
            "wohin überweis",
            "wohin zahl",
            "an wen zahl",
            "an wen überweis",
            "zahlung",
            "zahlen",
            "bezahlen",
            "überweis",
            "ueberweis",
            "paypal",
            "iban",
            "geld senden",
            "geld schicken",
            "payment",
            "payee",
            "empfänger",
            "empfaenger",
            "betrag",
            "was kostet",
            "wie viel zahl",
        ),
        "**Zahlung:** Im Ticket oben stehen **Empfänger** und **Betrag**.\n"
        "Überweise den **vollen Betrag** an diesen Empfänger (TxtEmpire).\n"
        "Danach **Payment beweisen** (Button) mit IGN + Screenshot der Zahlung.\n"
        "Ohne Beweis kann Staff den Kauf nicht bestätigen.",
    ),
    FaqEntry(
        (
            "payment beweis",
            "zahlung beweis",
            "beweis senden",
            "beweis schicken",
            "proof",
            "quittung",
            "screenshot zahlung",
            "screenshot von der zahlung",
            "zahlungsbeweis",
        ),
        "**Payment beweisen:** Button **Payment beweisen** → IGN eintragen "
        "und Screenshot der Zahlung anhängen.\n"
        "Danach wartet Staff auf die Bestätigung — danach kommt dein Pack.",
    ),
    FaqEntry(
        (
            "wie lange",
            "wartezeit",
            "wann bekomm",
            "wann krieg",
            "wie schnell",
            "wie lange dauert",
            "dauer",
        ),
        "Nach dem Zahlungsbeweis bestätigt Staff den Kauf. "
        "Packs/Rollen kommen danach automatisch (oft wenige Minuten, "
        "je nach Staff-Auslastung).",
    ),
    FaqEntry(
        (
            "wo ist mein pack",
            "pack nicht bekommen",
            "keine dm",
            "kein pack",
            "lieferung",
            "download",
            "datei bekomm",
            "pack per dm",
        ),
        "Nach Bestätigung kommt das **Pack per DM** (Text/Datei) und ggf. "
        "als Link im Ticket. DMs vom Server bitte erlauben.\n"
        "Wenn schon bestätigt und nichts da ist: Staff im Ticket kurz anpingen.",
    ),
    FaqEntry(
        (
            "credit",
            "credits",
            "guthaben",
            "coins",
            "quick buy",
            "schnellkauf",
        ),
        "**Credits:** Am Buy-Panel unter **Credits** kaufen. "
        "Bei aktiviertem Credits-Panel kannst du mit **Quick Buy** "
        "direkt ohne Überweisung zahlen (wenn genug Guthaben).",
    ),
    FaqEntry(
        (
            "rabatt",
            "creator code",
            "creator-code",
            "gutschein",
            "discount",
            "code eingeben",
            "rabattcode",
        ),
        "Im Ticket: Button **Rabatt / Creator Code** → Code eingeben. "
        "Der Preis wird angepasst, sofern der Code gültig ist.",
    ),
    FaqEntry(
        ("vouch", "bewertung", "review", "sterne", "bewerten"),
        "Nach erfolgreichem Kauf einmalig bewerten: "
        "`/vouch` (Server oder DM) oder Sterne-Buttons in der Pack-DM.",
    ),
    FaqEntry(
        ("abbrechen", "stornier", "cancel", "schließen", "schliessen"),
        "Kauf abbrechen: Button **Kauf abbrechen** oder `/order cancel`. "
        "Staff kann das Ticket mit **Ticket schließen** entfernen.",
    ),
    FaqEntry(
        ("ign", "minecraft name", "spielername", "ingame", "in game name"),
        "Dein **IGN** (Ingame-Name) wird beim **Payment beweisen** abgefragt "
        "und in der Bestellung gespeichert.",
    ),
    FaqEntry(
        ("rolle", "autorole", "customer rolle", "zugang"),
        "Nach Bestätigung vergibt der Bot die hinterlegten Rollen "
        "(Customer / Item- / Kategorie-Rolle), sofern konfiguriert.",
    ),
    FaqEntry(
        ("scan premium", "scanner", "malware", "rat scan", "file scan"),
        "File-Scanner: Panel **Datei hier droppen** oder `/scan file`. "
        "Premium erhöht das Limit — Kauf über das Scan-Panel.",
    ),
    FaqEntry(
        (
            "hilfe",
            "help",
            "was tun",
            "was muss ich",
            "wie geht",
            "anleitung",
            "wie funktioniert",
            "was soll ich machen",
            "next step",
            "nächster schritt",
            "naechster schritt",
        ),
        "**Kurzablauf:**\n"
        "1) Pack im Buy-Panel auswählen → Ticket öffnet sich\n"
        "2) Betrag **überweisen** (Daten im Ticket-Embed)\n"
        "3) **Payment beweisen** (IGN + Screenshot)\n"
        "4) Staff bestätigt → Pack/Rollen kommen per DM\n\n"
        "Fragen zu Zahlung oder Kauf einfach hier schreiben.",
    ),
)


def _normalize(text: str) -> str:
    t = text.lower().strip()
    # einfache Umlaute / Schreibweisen
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"[^\w\s+/.-]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def match_faq(content: str) -> str | None:
    """Gibt FAQ-Antwort oder None zurück (bester Keyword-Treffer)."""
    text = _normalize(content)
    if len(text) < 3 or len(text) > 400:
        return None
    if text.startswith("http"):
        return None

    best: FaqEntry | None = None
    best_len = 0
    for entry in FAQ:
        for key in entry.keys:
            k = _normalize(key)
            if len(k) < 3:
                continue
            if k in text and len(k) > best_len:
                best = entry
                best_len = len(k)
    return best.answer if best else None


def faq_cooldown_ok(channel_id: int) -> bool:
    now = time.monotonic()
    last = _cooldowns.get(channel_id, 0.0)
    if now - last < COOLDOWN_SEC:
        return False
    _cooldowns[channel_id] = now
    return True
