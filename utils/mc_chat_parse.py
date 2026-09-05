"""Parser für Ingame-Chat: Link-Codes und Money-Transfers."""

from __future__ import annotations

import re
from typing import Any

# §-Farb-/Formatcodes und moderne Legacy-Reste entfernen
_STRIP_CODES = re.compile(r"§[0-9A-FK-OR]|&#[0-9A-Fa-f]{6}")
_WS = re.compile(r"\s+")

# Whisper /msg Formate → Absender + Nachrichtentext
WHISPER_PREFIXES: tuple[re.Pattern[str], ...] = (
    # "Steve whispers to you: hello" / "Steve whispered to you: …"
    re.compile(
        r"^(?P<ign>[A-Za-z0-9_]{3,16})\s+whispers?(?:\s+to\s+you)?\s*:\s*(?P<body>.+)$",
        re.I,
    ),
    # "Steve flüstert dir: …" / "Steve flüsterte dir zu: …"
    re.compile(
        r"^(?P<ign>[A-Za-z0-9_]{3,16})\s+fl[uü]ster(?:t|te)(?:\s+dir)?(?:\s+zu)?\s*:\s*(?P<body>.+)$",
        re.I,
    ),
    # "[MSG] Steve: …" / "[Whisper] Steve: …"
    re.compile(
        r"^\[(?:msg|whisper|pn|pm|nachricht)\]\s*(?P<ign>[A-Za-z0-9_]{3,16})\s*:\s*(?P<body>.+)$",
        re.I,
    ),
    # "Steve -> Dir: …" / "Steve → you: …"
    re.compile(
        r"^(?P<ign>[A-Za-z0-9_]{3,16})\s*(?:->|→|»)\s*(?:dir|you|dich)?\s*:\s*(?P<body>.+)$",
        re.I,
    ),
    # "Von Steve: …"
    re.compile(
        r"^[Vv]on\s+(?P<ign>[A-Za-z0-9_]{3,16})\s*:\s*(?P<body>.+)$",
    ),
    # "<Steve> !link …" / "[Steve] !link …"
    re.compile(
        r"^[<\[]\s*(?P<ign>[A-Za-z0-9_]{3,16})\s*[>\]]\s*(?P<body>.+)$",
    ),
)

# !link CODE  /  !verify CODE  /  link CODE
LINK_CMD = re.compile(
    r"(?:^|[\s\[\]<>:])(?P<cmd>!?link|!?verify|!verknüpf|!verknuepf)\s+"
    r"(?P<code>[A-Za-z0-9\-]{4,24})\b",
    re.I,
)
# Nur der Code allein (falls Bot im Whisper nur den Code sieht)
LINK_CODE_ONLY = re.compile(r"^(?:code[:\s]*)?(?P<code>TXT[E]?-[A-Z0-9]{4,12})$", re.I)

# Häufige DE/EN Money-Chat-Formate (GrieferGames, Citybuild, Custom)
PAYMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "Steve hat dir 1.000.000$ gegeben."
    re.compile(
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s+hat\s+dir\s+"
        r"(?P<amount>[\d][\d.,]*)\s*(?:\$|€|euro|geld|coins?)?\s+gegeben",
        re.I,
    ),
    # "Du hast 500000$ von Steve erhalten."
    re.compile(
        r"[Dd]u\s+hast\s+(?P<amount>[\d][\d.,]*)\s*(?:\$|€|euro)?\s+"
        r"von\s+(?P<ign>[A-Za-z0-9_]{3,16})\s+erhalten",
        re.I,
    ),
    # "Steve paid you 1000000"
    re.compile(
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s+(?:paid|sent|gave)\s+(?:you\s+)?"
        r"(?P<amount>[\d][\d.,]*)",
        re.I,
    ),
    # "[Money] Steve -> You: 500.000$"
    re.compile(
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s*(?:->|→|»|>)\s*(?:you|dir|dich)?\s*:?\s*"
        r"(?P<amount>[\d][\d.,]*)\s*(?:\$|€)?",
        re.I,
    ),
    # "Zahlung von Steve: 250000"
    re.compile(
        r"(?:zahlung|payment|überweisung|ueberweisung)\s+(?:von\s+)?"
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s*:?\s*(?P<amount>[\d][\d.,]*)",
        re.I,
    ),
)


def strip_mc_formatting(text: str) -> str:
    t = _STRIP_CODES.sub("", text or "")
    return _WS.sub(" ", t).strip()


def parse_amount(raw: str) -> float | None:
    s = (raw or "").strip().replace(" ", "")
    if not s:
        return None
    # 1.000.000,50 → DE  |  1,000,000.50 → EN
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # DE: 1.000,50
            s = s.replace(".", "").replace(",", ".")
        else:
            # EN: 1,000.50
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 3 and len(parts) > 1:
            # Tausender: 1,000,000
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _split_whisper(clean: str) -> tuple[str | None, str]:
    """Extrahiert Absender aus Whisper-/Chat-Präfixen."""
    for pat in WHISPER_PREFIXES:
        m = pat.match(clean)
        if m:
            return m.group("ign").strip(), m.group("body").strip()
    return None, clean


def parse_chat_event(
    text: str, *, sender: str | None = None
) -> dict[str, Any] | None:
    """Erkennt Link-Code oder Payment. sender = Chat-Absender falls bekannt."""
    clean = strip_mc_formatting(text)
    if not clean:
        return None

    whisper_ign, body = _split_whisper(clean)
    ign = (sender or whisper_ign or "").strip()

    m = LINK_CMD.search(body) or LINK_CMD.search(clean)
    if m and ign:
        return {
            "type": "link",
            "code": m.group("code").upper(),
            "ign": ign,
        }

    m2 = LINK_CODE_ONLY.match(body) or LINK_CODE_ONLY.match(clean)
    if m2 and ign:
        return {
            "type": "link",
            "code": m2.group("code").upper(),
            "ign": ign,
        }

    for pat in PAYMENT_PATTERNS:
        pm = pat.search(clean)
        if not pm:
            continue
        amount = parse_amount(pm.group("amount"))
        pay_ign = pm.group("ign").strip()
        if amount is None or amount <= 0 or not pay_ign:
            continue
        return {
            "type": "payment",
            "ign": pay_ign,
            "amount": amount,
        }

    return None
