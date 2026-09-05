"""Einfache Keyword-FAQ für Shop- und Service-Tickets."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# channel_id → last reply timestamp
_cooldowns: dict[int, float] = {}
COOLDOWN_SEC = 8.0
MAX_FAQ_TURNS = 3

MONEY_LOG_HINT = (
    "📸 **Wichtig:** Bitte ein **Fullscreen-Bild** vom **Money-Log** / "
    "Zahlungsnachweis senden (ganzen Bildschirm, nicht zugeschnitten, "
    "Betrag & Empfänger gut lesbar)."
)


@dataclass(frozen=True)
class FaqEntry:
    keys: tuple[str, ...]
    answer: str


FAQ: tuple[FaqEntry, ...] = (
    FaqEntry(
        (
            "money log",
            "moneylog",
            "fullscreen",
            "full screen",
            "ganzen bildschirm",
            "vollbild",
            "screenshot geld",
            "bild vom log",
            "welche bild",
            "was fuer ein bild",
            "was für ein bild",
            "was fuer screenshot",
            "was für screenshot",
        ),
        MONEY_LOG_HINT
        + "\nDanach Button **Payment beweisen** (IGN + Bild anhängen).",
    ),
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
            "texturepack kaufen",
            "produkt kaufen",
            "wie geht der kauf",
        ),
        "**Packs kaufen:**\n"
        "1) Buy-Panel → Produkt wählen → Ticket öffnet sich\n"
        "2) Betrag **überweisen** (Daten im Ticket oben)\n"
        "3) **Verlinkter Account:** Ticket wird automatisch bestätigt\n"
        "   **Sonst:** Fullscreen Money-Log → **Payment beweisen** → Staff\n"
        "4) Pack per DM",
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
            "wo zahl ich",
            "wo zahle ich",
        ),
        "**Zahlung:** Empfänger + Betrag stehen **oben im Ticket**.\n"
        "Vollbetrag überweisen → **Fullscreen Money-Log** Screenshot → "
        "Button **Payment beweisen**.\n"
        f"{MONEY_LOG_HINT}",
    ),
    FaqEntry(
        (
            "payment beweis",
            "zahlung beweis",
            "beweis senden",
            "beweis schicken",
            "beweisen",
            "proof",
            "quittung",
            "screenshot zahlung",
            "zahlungsbeweis",
            "wie beweis",
            "bild senden",
            "bild schicken",
        ),
        "**Payment beweisen:** Button **Payment beweisen** → IGN + Bild anhängen.\n"
        f"{MONEY_LOG_HINT}",
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
            "wann kommt",
        ),
        "Nach dem Zahlungsbeweis bestätigt Staff den Kauf. "
        "Packs kommen danach oft innerhalb weniger Minuten "
        "(abhängig von Staff).",
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
            "wo pack",
        ),
        "Nach Bestätigung: Pack per **DM** (+ ggf. Link im Ticket). "
        "Server-DMs erlauben. Wenn schon bestätigt und nichts da: Staff pingen.",
    ),
    FaqEntry(
        ("credit", "credits", "guthaben", "coins", "quick buy", "schnellkauf"),
        "**Credits:** Am Buy-Panel unter **Credits** kaufen. "
        "Mit Guthaben oft **Quick Buy** ohne Überweisung möglich.",
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
        "Im Ticket: Button **Rabatt / Creator Code** → Code eingeben.",
    ),
    FaqEntry(
        ("vouch", "bewertung", "review", "sterne", "bewerten"),
        "Nach Kauf: `/vouch` oder Sterne in der Pack-DM.",
    ),
    FaqEntry(
        ("abbrechen", "stornier", "cancel", "schließen", "schliessen"),
        "Abbrechen: Button **Kauf abbrechen** oder `/order cancel`.",
    ),
    FaqEntry(
        (
            "link account",
            "account link",
            "account verlinken",
            "mc link",
            "minecraft link",
            "verknüpf",
            "verknuepf",
            "unverifiz",
            "unlink",
            "auto confirm",
            "automatisch bestätigt",
            "automatisch bestaetigt",
        ),
        "**Account verlinken:** Panel **Minecraft Account** → "
        "**Account verlinken** → IGN → Code bekommen → Ingame "
        "`/msg TxTEmpire !link CODE` (privat).\n"
        "Danach wird dein Ticket nach korrekter Zahlung **automatisch bestätigt**.\n"
        "**Unverifizieren:** gleicher Panel-Button oder `/unlink`.",
    ),
    FaqEntry(
        ("ign", "minecraft name", "spielername", "ingame", "in game name"),
        "**IGN:** Am besten einmalig über **Account verlinken** verbinden.\n"
        "Sonst trägst du den IGN beim **Payment beweisen** ein.",
    ),
    FaqEntry(
        ("rolle", "autorole", "customer rolle", "zugang"),
        "Rollen (Customer / Item) kommen automatisch nach Staff-Bestätigung.",
    ),
    FaqEntry(
        ("hallo", "hey", "moin", "guten tag", "servus", "hi", "hello"),
        "Hey! Überweise den Betrag aus dem Ticket, sende ein "
        "**Fullscreen Money-Log**, dann **Payment beweisen**.\n"
        "Fragen zu Zahlung/Pack einfach hier schreiben (max. 3 Auto-Antworten).",
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
            "was jetzt",
            "und jetzt",
        ),
        "**Ablauf:**\n"
        "1) Betrag überweisen (Ticket-Embed oben)\n"
        "2) Fullscreen Money-Log Screenshot\n"
        "3) **Payment beweisen** (IGN + Bild)\n"
        "4) Staff bestätigt → Pack/DM\n\n"
        f"{MONEY_LOG_HINT}",
    ),
)


def _normalize(text: str) -> str:
    t = text.lower().strip()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"[^\w\s+/.-?]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def looks_like_question(content: str) -> bool:
    text = _normalize(content)
    if "?" in content or "?" in text:
        return True
    starters = (
        "wie ",
        "was ",
        "wo ",
        "wann ",
        "warum ",
        "wieso ",
        "weshalb ",
        "kann ",
        "koennt",
        "könnt",
        "bitte",
        "help",
        "hilfe",
        "wohin",
        "welche",
        "welcher",
        "welches",
        "muss ich",
        "soll ich",
        "hab ich",
        "habe ich",
        "geht ",
        "okay",
        "ok ",
        "und dann",
    )
    if any(text.startswith(s) or f" {s}" in f" {text}" for s in starters):
        return True
    # kurze Unsicherheit
    if text in ("?", "??", "???", "hilfe", "help", "was", "wie", "hallo", "hi"):
        return True
    return len(text) >= 8  # normale Ticket-Nachrichten auch als Turn zählen


def match_faq(content: str) -> str | None:
    """Gibt FAQ-Antwort oder None zurück (bester Keyword-Treffer)."""
    text = _normalize(content)
    if len(text) < 2 or len(text) > 500:
        return None
    if text.startswith("http") and " " not in text:
        return None

    best: FaqEntry | None = None
    best_len = 0
    for entry in FAQ:
        for key in entry.keys:
            k = _normalize(key)
            if len(k) < 2:
                continue
            if k in text and len(k) > best_len:
                best = entry
                best_len = len(k)

    if best:
        return best.answer

    # Sehr einfache Fallbacks
    if any(w in text for w in ("zahl", "bezahl", "pay", "geld", "ueberweis", "überweis")):
        return match_faq("wie kann ich bezahlen money log")
    if any(w in text for w in ("kauf", "pack", "bestell", "shop")):
        return match_faq("wie kaufe ich ein pack")
    if any(w in text for w in ("bild", "screen", "foto", "proof", "beweis")):
        return MONEY_LOG_HINT
    return None


def faq_cooldown_ok(channel_id: int) -> bool:
    now = time.monotonic()
    last = _cooldowns.get(channel_id, 0.0)
    if now - last < COOLDOWN_SEC:
        return False
    _cooldowns[channel_id] = now
    return True


def money_log_hint_enabled(settings: dict) -> bool:
    raw = settings.get("ticket_money_log_hint")
    if raw is None:
        return True
    return int(raw) != 0
