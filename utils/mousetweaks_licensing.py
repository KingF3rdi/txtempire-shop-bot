"""
mousetweaks_licensing.py
=========================

Signiert Lizenzkeys für "Ferdi Mousetweaks" - byte-kompatibel mit
app/licensing.py in der Ferdi-Mousetweaks-App. Der Bot und die App
benutzen exakt denselben Algorithmus (HMAC-SHA256 über ein kompaktes
21-Byte-Binärpaket, Base32-kodiert und in 5er-Gruppen dargestellt - siehe
app/licensing.py, Abschnitt "KEY-FORMAT" für die genaue Byte-Aufteilung);
ein hier erzeugter Key wird von der App offline geprüft, ohne dass
irgendein Server erreichbar sein muss.

WICHTIG: config.MOUSETWEAKS_LICENSE_SECRET (aus der .env) MUSS exakt dem
Wert entsprechen, der in der App bei app/licensing.py -> LICENSE_SECRET
eingetragen ist. Ändert sich einer der beiden Werte, werden alle bisher
ausgestellten Keys für die jeweils andere Seite ungültig.

Dieses Modul hat KEINE Abhängigkeiten außerhalb der Python-Standardbibliothek.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from typing import Optional, Tuple

import config

TIER_LIFETIME = "lifetime"
TIER_14D = "14d"
TIER_30D = "30d"
TIER_DAYS = {TIER_14D: 14, TIER_30D: 30}
TIER_LABELS = {
    TIER_LIFETIME: "Lifetime",
    TIER_14D: "14 Tage",
    TIER_30D: "30 Tage",
}

_TIER_CODES = {TIER_LIFETIME: 0, TIER_14D: 1, TIER_30D: 2}
_TIER_CODES_REV = {v: k for k, v in _TIER_CODES.items()}
_PAYLOAD_LEN = 15
_SIG_LEN = 6
_TOTAL_LEN = _PAYLOAD_LEN + _SIG_LEN
_B32_PAD_BY_REMAINDER = {0: 0, 2: 6, 4: 4, 5: 3, 7: 1}


def licensing_configured() -> bool:
    return bool(getattr(config, "MOUSETWEAKS_LICENSE_SECRET", "").strip())


def tier_to_expiry(tier: str, issued_ts: Optional[int] = None) -> Optional[int]:
    days = TIER_DAYS.get(tier)
    if not days:
        return None
    base = issued_ts if issued_ts is not None else int(time.time())
    return base + days * 86400


def _hwid_to_raw(hwid_display: str) -> bytes:
    return bytes.fromhex(hwid_display.replace("-", "").strip())


def _hwid_from_raw(raw10: bytes) -> str:
    hex_str = raw10.hex().upper()
    return "-".join(hex_str[i:i + 5] for i in range(0, 20, 5))


def _dash_group(s: str, n: int = 5) -> str:
    return "-".join(s[i:i + n] for i in range(0, len(s), n))


def _b32_encode(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").rstrip("=")


def _b32_decode(s: str) -> bytes:
    rem = len(s) % 8
    pad = _B32_PAD_BY_REMAINDER.get(rem)
    if pad is None:
        raise ValueError("ungültige Key-Länge")
    return base64.b32decode(s + ("=" * pad))


def _sign_payload(payload: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()[:_SIG_LEN]


def generate_license_key(
    hwid: Optional[str],
    tier: str = TIER_LIFETIME,
    issued: Optional[int] = None,
) -> str:
    """Erzeugt einen fertigen, kompakten Lizenzkey-String.
    Braucht config.MOUSETWEAKS_LICENSE_SECRET (siehe licensing_configured()).

    `hwid`: None/leer erzeugt einen NICHT gebundenen Key - so wird er jetzt
    beim Kauf sofort ausgegeben, ohne vorher die Hardware-ID abzufragen. Die
    App bindet ihn automatisch an das Geraet des Kunden beim ersten
    Eintragen (siehe app/licensing.py: check_license_key).

    `issued`: normalerweise leer (= jetzt) - beim HWID-Reset wird das
    urspruengliche Ausstellungsdatum durchgereicht, damit sich der (aus
    Tier+issued berechnete) Ablauf dabei nicht verschiebt."""
    secret = config.MOUSETWEAKS_LICENSE_SECRET.strip()
    if not secret:
        raise RuntimeError(
            "MOUSETWEAKS_LICENSE_SECRET ist nicht gesetzt (siehe .env)."
        )
    if issued is None:
        issued = int(time.time())
    hwid_clean = (hwid or "").strip()
    hwid_raw = _hwid_to_raw(hwid_clean) if hwid_clean else bytes(10)
    payload = bytes([_TIER_CODES.get(tier, 0)]) + struct.pack(">I", issued) + hwid_raw
    sig = _sign_payload(payload, secret)
    return _dash_group(_b32_encode(payload + sig))


def describe_tier(tier: str, expires: Optional[int]) -> str:
    label = TIER_LABELS.get(tier, tier)
    if not expires:
        return label
    exp_str = time.strftime("%d.%m.%Y", time.localtime(float(expires)))
    return f"{label} (gültig bis {exp_str})"


def verify_own_key(key: str) -> Tuple[bool, Optional[dict], str]:
    """Nur zu Debug-/Testzwecken (z.B. /key generate zeigt danach eine
    Selbstprüfung) - prüft NICHT die Hardware-ID, weil der Bot keine hat.
    Die eigentliche Prüfung passiert in der App (app/licensing.py). Gibt im
    Payload-dict "hwid" (oder None), "tier", "issued", "expires" zurück."""
    secret = config.MOUSETWEAKS_LICENSE_SECRET.strip()
    if not secret:
        return False, None, "MOUSETWEAKS_LICENSE_SECRET nicht gesetzt."
    cleaned = (key or "").strip().upper().replace("-", "").replace(" ", "")
    if not cleaned:
        return False, None, "Bitte einen Lizenzkey eingeben."
    try:
        raw = _b32_decode(cleaned)
    except Exception:
        return False, None, "Format ungültig."
    if len(raw) != _TOTAL_LEN:
        return False, None, "Key beschädigt (falsche Länge)."
    payload_bytes, sig = raw[:_PAYLOAD_LEN], raw[_PAYLOAD_LEN:]
    expected = _sign_payload(payload_bytes, secret)
    if not hmac.compare_digest(sig, expected):
        return False, None, "Signatur ungültig."
    tier = _TIER_CODES_REV.get(payload_bytes[0], TIER_LIFETIME)
    issued = struct.unpack(">I", payload_bytes[1:5])[0]
    hwid_raw = payload_bytes[5:15]
    hwid = _hwid_from_raw(hwid_raw) if hwid_raw != bytes(10) else None
    payload = {
        "hwid": hwid,
        "tier": tier,
        "issued": issued,
        "expires": tier_to_expiry(tier, issued),
    }
    return True, payload, ""
