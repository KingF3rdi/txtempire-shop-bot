"""Einfache Keyword-FAQ — Kauf-Tickets vs. Support-Tickets getrennt."""

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
    # shop = nur Kauf-Tickets; support = nur Support-/Service-Tickets; any = beide
    scope: str = "any"


# ── Kauf-Ticket FAQ (Zahlung / Pack / Proof) ─────────────────────────
_SHOP_FAQ: tuple[FaqEntry, ...] = (
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
        "shop",
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
        "shop",
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
        "shop",
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
        "shop",
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
        "shop",
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
        "shop",
    ),
    FaqEntry(
        ("credit", "credits", "guthaben", "coins", "quick buy", "schnellkauf"),
        "**Credits:** Am Buy-Panel unter **Credits** kaufen. "
        "Mit Guthaben oft **Quick Buy** ohne Überweisung möglich.",
        "shop",
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
        "shop",
    ),
    FaqEntry(
        ("vouch", "bewertung", "review", "sterne", "bewerten"),
        "Nach Kauf: `/vouch` oder Sterne in der Pack-DM.",
        "shop",
    ),
    FaqEntry(
        ("abbrechen", "stornier", "cancel", "schließen", "schliessen"),
        "Abbrechen: Button **Kauf abbrechen** oder `/order cancel`.",
        "shop",
    ),
    FaqEntry(
        ("ign", "minecraft name", "spielername", "ingame", "in game name"),
        "**IGN:** Am besten einmalig über **Account verlinken** verbinden.\n"
        "Sonst trägst du den IGN beim **Payment beweisen** ein.",
        "shop",
    ),
    FaqEntry(
        ("rolle", "autorole", "customer rolle", "zugang"),
        "Rollen (Customer / Item) kommen automatisch nach Staff-Bestätigung.",
        "shop",
    ),
    FaqEntry(
        ("hallo", "hey", "moin", "guten tag", "servus", "hi", "hello"),
        "Hey! Überweise den Betrag aus dem Ticket, sende ein "
        "**Fullscreen Money-Log**, dann **Payment beweisen**.\n"
        "Fragen zu Zahlung/Pack einfach hier schreiben (max. 3 Auto-Antworten).",
        "shop",
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
        "shop",
    ),
)

# ── Support-/Service-Ticket FAQ (kein Payment) ───────────────────────
_SUPPORT_FAQ: tuple[FaqEntry, ...] = (
    FaqEntry(
        ("hallo", "hey", "moin", "guten tag", "servus", "hi", "hello"),
        "Hey! Beschreib kurz dein **Anliegen** (was passiert / was du brauchst). "
        "Staff meldet sich so schnell wie möglich.",
        "support",
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
            "was jetzt",
            "und jetzt",
        ),
        "Schreib bitte klar:\n"
        "• Was ist das Problem?\n"
        "• Seit wann?\n"
        "• Screenshots helfen oft.\n"
        "Staff liest mit und antwortet hier.",
        "support",
    ),
    FaqEntry(
        (
            "wie lange",
            "wartezeit",
            "wann antwort",
            "wann kommt jemand",
            "wie schnell",
            "antwortzeit",
            "staff online",
            "ist jemand da",
        ),
        "Support-Antworten kommen, sobald Staff online ist — oft kurz, "
        "manchmal etwas länger. Bitte hier im Ticket warten (nicht doppelt pingen).",
        "support",
    ),
    FaqEntry(
        (
            "bug",
            "fehler",
            "kaputt",
            "funktioniert nicht",
            "geht nicht",
            "problem",
            "crash",
            "fehlerhaft",
        ),
        "Bitte so genau wie möglich beschreiben + Screenshot/Video wenn möglich:\n"
        "• Was wolltest du tun?\n"
        "• Was ist passiert?\n"
        "• Fehlermeldung / Uhrzeit?\n"
        "Staff schaut sich das an.",
        "support",
    ),
    FaqEntry(
        (
            "pack fehlt",
            "kein pack bekommen",
            "datei fehlt",
            "dm nicht bekommen",
            "keine nachricht",
            "nicht geliefert",
            "wo ist mein pack",
        ),
        "Wenn der Kauf schon bestätigt war: prüfe DMs (Server-DMs erlauben) "
        "und Spam. Schreib IGN + ungefähre Kaufzeit hier rein — Staff prüft die Lieferung.",
        "support",
    ),
    FaqEntry(
        (
            "rolle fehlt",
            "keine rolle",
            "customer fehlt",
            "zugang fehlt",
            "nicht freigeschaltet",
        ),
        "Rollen kommen nach Kauf-Bestätigung. Fehlt etwas trotz bestätigtem Kauf: "
        "schreib welcher Kauf / welche Rolle — Staff korrigiert das.",
        "support",
    ),
    FaqEntry(
        (
            "scam",
            "betrug",
            "report",
            "melden",
            "fake",
            "abgezockt",
        ),
        "Beschreib den Vorfall mit Belegen (Screenshots, IGN, Uhrzeit). "
        "Staff prüft und hilft weiter. Keine Zahlungsdaten hier posten.",
        "support",
    ),
    FaqEntry(
        (
            "bewerbung",
            "bewerben",
            "application",
            "team bewerbung",
            "mitarbeiten",
        ),
        "Bewerbungen laufen über das **Bewerbungs-Panel**. "
        "Schreib hier deine Angaben / Erfahrung — Staff entscheidet im Ticket.",
        "support",
    ),
    FaqEntry(
        (
            "partner",
            "partnerschaft",
            "kooperation",
            "collab",
            "zusammenarbeit",
        ),
        "Partner-Anfragen: kurz vorstellen (Kanal/Server, Reichweite, Idee). "
        "Staff meldet sich mit Feedback.",
        "support",
    ),
    FaqEntry(
        (
            "texturepack verkaufen",
            "pack verkaufen",
            "ankauf",
            "an den server verkaufen",
            "pack anbieten",
        ),
        "Texturepack **Ankauf**: über das Texturepack-Panel → Ankauf. "
        "Preisvorstellung + Infos zum Pack hier rein — Staff bewertet.",
        "support",
    ),
    FaqEntry(
        (
            "texturepack tausch",
            "pack tausch",
            "tauschen",
            "tausch",
        ),
        "Texturepack **Tausch**: über das Texturepack-Panel → Tausch. "
        "Was bietest du / was suchst du? Staff prüft und nimmt an.",
        "support",
    ),
    FaqEntry(
        (
            "schließen",
            "schliessen",
            "ticket zu",
            "ticket schließ",
            "fertig",
            "erledigt",
            "kann zu",
        ),
        "Wenn dein Anliegen erledigt ist: Button **Ticket schließen** "
        "(oder Staff schließt). Danke!",
        "support",
    ),
    FaqEntry(
        (
            "danke",
            "thanks",
            "thx",
            "dankeschön",
            "dankesehr",
        ),
        "Gerne! Wenn noch etwas offen ist, einfach weiter schreiben — "
        "sonst Ticket schließen.",
        "support",
    ),
)

# Gemeinsam (nicht Payment-fokussiert)
_ANY_FAQ: tuple[FaqEntry, ...] = (
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
        "**Account verlinken** → IGN → Code → Ingame "
        "`/msg TxTEmpire !link CODE` (privat).\n"
        "Dann Auto-Confirm bei passender Zahlung. "
        "**Unverifizieren:** Panel-Button oder `/unlink`.",
        "any",
    ),
)

FAQ: tuple[FaqEntry, ...] = _SHOP_FAQ + _SUPPORT_FAQ + _ANY_FAQ


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
    if text in ("?", "??", "???", "hilfe", "help", "was", "wie", "hallo", "hi"):
        return True
    return len(text) >= 8


def _key_in_text(key: str, text: str) -> bool:
    """Keyword-Treffer mit Wortgrenzen bei kurzen Keys (weniger False Positives)."""
    if len(key) >= 6:
        return key in text
    # kurze Keys nur als ganzes Wort / Phrase
    return bool(re.search(rf"(?:^|\s){re.escape(key)}(?:\s|$|[?!.])", text))


def _is_confident_match(*, text: str, key_len: int) -> bool:
    """Lange/komplexe Fragen brauchen starken Keyword-Treffer — sonst Staff."""
    words = [w for w in text.split() if len(w) > 1]
    # Sehr kurze Nachrichten (Hallo / Danke) → kurze Keys ok
    if len(text) <= 20 and len(words) <= 3:
        return key_len >= 2
    # Substantielle Frage: Key muss aussagekräftig sein
    if key_len < 5:
        return False
    if len(words) >= 8 and key_len < 8:
        return False
    if "?" in text and len(text) >= 40 and key_len < 6:
        return False
    return True


def match_faq(
    content: str,
    *,
    ticket_kind: str = "order",
) -> str | None:
    """Kauf-Ticket → Shop-FAQ; Support → Support-FAQ.

    Bei unsicherem Treffer: None → Caller pingt Staff.
    """
    text = _normalize(content)
    if len(text) < 2 or len(text) > 500:
        return None
    if text.startswith("http") and " " not in text:
        return None

    is_support = ticket_kind == "service"
    allowed = {"support", "any"} if is_support else {"shop", "any"}

    best: FaqEntry | None = None
    best_len = 0
    for entry in FAQ:
        if entry.scope not in allowed:
            continue
        for key in entry.keys:
            k = _normalize(key)
            if len(k) < 2:
                continue
            if not _key_in_text(k, text):
                continue
            if len(k) > best_len:
                best = entry
                best_len = len(k)

    if best and _is_confident_match(text=text, key_len=best_len):
        return best.answer

    # Keine schwachen Fallbacks bei komplexen Fragen — lieber Staff
    if len(text) >= 35 or "?" in text:
        return None

    if is_support:
        if any(
            _key_in_text(w, text)
            for w in ("bug", "fehler", "problem", "kaputt")
        ) or "geht nicht" in text:
            return match_faq("bug fehler problem", ticket_kind="service")
        return None

    if any(
        _key_in_text(w, text)
        for w in ("zahlung", "bezahlen", "paypal", "iban", "ueberweis", "überweis")
    ):
        return match_faq("wie kann ich bezahlen money log", ticket_kind="order")
    if any(
        _key_in_text(w, text)
        for w in ("money log", "moneylog", "fullscreen", "zahlungsbeweis")
    ):
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
