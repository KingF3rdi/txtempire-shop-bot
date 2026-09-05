"""Parser für Ingame-Chat: Link-Codes und Money-Transfers."""

from __future__ import annotations

import re
from typing import Any

# §-Farb-/Formatcodes und moderne Legacy-Reste entfernen
_STRIP_CODES = re.compile(r"§[0-9A-FK-OR]|&#[0-9A-Fa-f]{6}")
_WS = re.compile(r"\s+")

RESERVED_IGNS = frozenset(
    {
        "nachricht",
        "msg",
        "whisper",
        "pn",
        "pm",
        "message",
        "system",
        "money",
        "geld",
        "zahlung",
        "payment",
        "link",
        "verify",
        "code",
        "txtempire",
        "dir",
        "you",
        "dich",
        "du",
        "sell",
        "auktionshaus",
        "hugosmp",
        "has",
        "paid",
        "sent",
        "gave",
        "from",
        "von",
        "the",
        "and",
        "hat",
        "hast",
        "erhalten",
        "gegeben",
        "an",
    }
)

# Whisper /msg Formate → Absender + Nachrichtentext
WHISPER_PREFIXES: tuple[re.Pattern[str], ...] = (
    # "[Nachricht] p9x1 -> Du: !link …"
    re.compile(
        r"^\[(?:nachricht|msg|whisper|pn|pm|message)\]\s*"
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s*(?:->|→|»|›)\s*"
        r"(?:dir|you|dich|du|[A-Za-z0-9_]{3,16})\s*[:»>]\s*(?P<body>.+)$",
        re.I,
    ),
    re.compile(
        r"^(?P<ign>[A-Za-z0-9_]{3,16})\s+whispers?(?:\s+to\s+you)?\s*:\s*(?P<body>.+)$",
        re.I,
    ),
    re.compile(
        r"^(?P<ign>[A-Za-z0-9_]{3,16})\s+fl[uü]ster(?:t|te)(?:\s+dir)?(?:\s+zu)?\s*:\s*(?P<body>.+)$",
        re.I,
    ),
    re.compile(
        r"^\[(?:msg|whisper|pn|pm|nachricht|message)\]\s*"
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s*[:»>]\s*(?P<body>.+)$",
        re.I,
    ),
    re.compile(
        r"^(?P<ign>[A-Za-z0-9_]{3,16})\s*(?:->|→|»)\s*"
        r"(?:dir|you|dich|du)?\s*:\s*(?P<body>.+)$",
        re.I,
    ),
    re.compile(
        r"^[Vv]on\s+(?P<ign>[A-Za-z0-9_]{3,16})\s*:\s*(?P<body>.+)$",
    ),
    re.compile(
        r"^[<\[]\s*(?P<ign>[A-Za-z0-9_]{3,16})\s*[>\]]\s*(?P<body>.+)$",
    ),
)

LINK_CMD = re.compile(
    r"(?:^|[\s\[\]<>:])(?P<cmd>!?link|!?verify|!verknüpf|!verknuepf)\s+"
    r"(?P<code>[A-Za-z0-9\-]{4,24})\b",
    re.I,
)
LINK_CODE_ONLY = re.compile(r"^(?:code[:\s]*)?(?P<code>TXT[E]?-[A-Z0-9]{4,12})$", re.I)
CODE_ANYWHERE = re.compile(r"\b(?P<code>TXT[E]?-[A-Z0-9]{4,12})\b", re.I)
NAME_BEFORE_CODE = re.compile(
    r"\b(?P<ign>[A-Za-z0-9_]{3,16})\b[^A-Za-z0-9_]{0,48}?"
    r"\b(?P<code>TXT[E]?-[A-Z0-9]{4,12})\b",
    re.I,
)

PAYMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "[HugoSMP] Du hast $400000 von 0Moos erhalten."
    re.compile(
        r"[Dd]u\s+hast\s+\$?\s*(?P<amount>[\d][\d.,]*\s*[kKmMbB]?)\s*(?:\$|€|euro)?\s+"
        r"von\s+(?P<ign>[A-Za-z0-9_]{3,16})\s+erhalten",
    ),
    # "Du hast von 0Moos $400000 erhalten."
    re.compile(
        r"[Dd]u\s+hast\s+von\s+(?P<ign>[A-Za-z0-9_]{3,16})\s+"
        r"\$?\s*(?P<amount>[\d][\d.,]*\s*[kKmMbB]?)\s*(?:\$|€|euro)?\s*erhalten",
    ),
    # "0Moos hat dir $400,000 geschickt/überwiesen"
    re.compile(
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s+hat\s+dir\s+\$?\s*"
        r"(?P<amount>[\d][\d.,]*\s*[kKmMbB]?)\s*(?:\$|€|euro|geld|coins?)?\s+"
        r"(?:gegeben|geschickt|gesendet|überwiesen|ueberwiesen|gezahlt|bezahlt)",
        re.I,
    ),
    # "Gamerleo15 » TxTEmpire - $450,000"
    re.compile(
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s*[»›→>]\s*"
        r"(?:you|dir|dich|du|txtempire|[A-Za-z0-9_]{3,16})\s*[-–:]\s*"
        r"\$?\s*(?P<amount>[\d][\d.,]*\s*[kKmMbB]?)",
        re.I,
    ),
    re.compile(
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s+has\s+paid\s+you\s+"
        r"\$?\s*(?P<amount>[\d][\d.,]*\s*[kKmMbB]?)",
        re.I,
    ),
    re.compile(
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s+(?:paid|sent|gave)\s+(?:you\s+)?"
        r"\$?\s*(?P<amount>[\d][\d.,]*\s*[kKmMbB]?)",
        re.I,
    ),
    re.compile(
        r"(?:zahlung|payment|überweisung|ueberweisung)\s+(?:von\s+)?"
        r"(?P<ign>[A-Za-z0-9_]{3,16})\s*:?\s*\$?\s*(?P<amount>[\d][\d.,]*\s*[kKmMbB]?)",
        re.I,
    ),
)


def strip_mc_formatting(text: str) -> str:
    t = _STRIP_CODES.sub("", text or "")
    return _WS.sub(" ", t).strip()


def is_valid_ign(name: str | None) -> bool:
    if not name:
        return False
    s = name.strip()
    if not (3 <= len(s) <= 16):
        return False
    if s.lower() in RESERVED_IGNS:
        return False
    return all(c.isalnum() or c == "_" for c in s)


def parse_amount(raw: str) -> float | None:
    s = (raw or "").strip().replace(" ", "").replace("$", "").replace("€", "")
    if not s:
        return None
    mult = 1.0
    last = s[-1].lower()
    if last in ("k", "m", "b") and len(s) > 1:
        mult = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}[last]
        s = s[:-1]
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 3 and len(parts) > 1:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s) * mult
    except ValueError:
        return None


def _split_whisper(clean: str) -> tuple[str | None, str]:
    for pat in WHISPER_PREFIXES:
        m = pat.match(clean)
        if not m:
            continue
        ign = m.group("ign").strip()
        if is_valid_ign(ign):
            return ign, m.group("body").strip()
    return None, clean


def parse_chat_event(
    text: str, *, sender: str | None = None
) -> dict[str, Any] | None:
    """Erkennt Link-Code oder Payment. sender = Chat-Absender falls bekannt."""
    clean = strip_mc_formatting(text)
    if not clean:
        return None

    whisper_ign, body = _split_whisper(clean)
    ign = ""
    if is_valid_ign(sender):
        ign = sender.strip()
    elif whisper_ign:
        ign = whisper_ign

    m = LINK_CMD.search(body) or LINK_CMD.search(clean)
    if m and is_valid_ign(ign):
        return {
            "type": "link",
            "code": m.group("code").upper(),
            "ign": ign,
        }

    m2 = LINK_CODE_ONLY.match(body) or LINK_CODE_ONLY.match(clean)
    if m2 and is_valid_ign(ign):
        return {
            "type": "link",
            "code": m2.group("code").upper(),
            "ign": ign,
        }

    for nm in NAME_BEFORE_CODE.finditer(clean):
        cand = nm.group("ign")
        if is_valid_ign(cand):
            return {
                "type": "link",
                "code": nm.group("code").upper(),
                "ign": cand.strip(),
            }

    if is_valid_ign(ign):
        anywhere = CODE_ANYWHERE.search(clean)
        if anywhere:
            return {
                "type": "link",
                "code": anywhere.group("code").upper(),
                "ign": ign,
            }

    for pat in PAYMENT_PATTERNS:
        pm = pat.search(clean)
        if not pm:
            continue
        amount = parse_amount(pm.group("amount"))
        pay_ign = pm.group("ign").strip()
        if amount is None or amount <= 0 or not is_valid_ign(pay_ign):
            continue
        return {
            "type": "payment",
            "ign": pay_ign,
            "amount": amount,
        }

    return None
