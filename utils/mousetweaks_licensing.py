"""
mousetweaks_licensing.py
=========================

Signiert Lizenzkeys für "Ferdi Mousetweaks" - byte-kompatibel mit
app/licensing.py in der Ferdi-Mousetweaks-App. Der Bot und die App
benutzen exakt denselben Algorithmus (HMAC-SHA256 über einen
base64-kodierten JSON-Payload); ein hier erzeugter Key wird von der App
offline geprüft, ohne dass irgendein Server erreichbar sein muss.

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
import json
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


def licensing_configured() -> bool:
    return bool(getattr(config, "MOUSETWEAKS_LICENSE_SECRET", "").strip())


def tier_to_expiry(tier: str, issued_ts: Optional[int] = None) -> Optional[int]:
    days = TIER_DAYS.get(tier)
    if not days:
        return None
    base = issued_ts if issued_ts is not None else int(time.time())
    return base + days * 86400


def _sign(payload_b64: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_b64, hashlib.sha256).hexdigest()[:32]


def generate_license_key(
    hwid: Optional[str],
    note: str,
    tier: str = TIER_LIFETIME,
    expires: Optional[int] = None,
) -> str:
    """Erzeugt einen fertigen Lizenzkey-String ("payload.signatur").
    Braucht config.MOUSETWEAKS_LICENSE_SECRET (siehe licensing_configured()).

    `hwid`: None/leer erzeugt einen NICHT gebundenen Key - so wird er jetzt
    beim Kauf sofort ausgegeben, ohne vorher die Hardware-ID abzufragen. Die
    App bindet ihn automatisch an das Geraet des Kunden beim ersten
    Eintragen (siehe app/licensing.py: check_license_key)."""
    secret = config.MOUSETWEAKS_LICENSE_SECRET.strip()
    if not secret:
        raise RuntimeError(
            "MOUSETWEAKS_LICENSE_SECRET ist nicht gesetzt (siehe .env)."
        )
    issued = int(time.time())
    if expires is None:
        expires = tier_to_expiry(tier, issued)
    payload = {
        "hwid": (hwid or "").strip() or None,
        "note": (note or "").strip(),
        "issued": issued,
        "tier": tier,
        "expires": expires,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    sig = _sign(payload_b64, secret)
    return f"{payload_b64.decode('ascii')}.{sig}"


def describe_tier(tier: str, expires: Optional[int]) -> str:
    label = TIER_LABELS.get(tier, tier)
    if not expires:
        return label
    exp_str = time.strftime("%d.%m.%Y", time.localtime(float(expires)))
    return f"{label} (gültig bis {exp_str})"


def verify_own_key(key: str) -> Tuple[bool, Optional[dict], str]:
    """Nur zu Debug-/Testzwecken (z.B. /key generate zeigt danach eine
    Selbstprüfung) - prüft NICHT die Hardware-ID, weil der Bot keine hat.
    Die eigentliche Prüfung passiert in der App (app/licensing.py)."""
    secret = config.MOUSETWEAKS_LICENSE_SECRET.strip()
    if not secret:
        return False, None, "MOUSETWEAKS_LICENSE_SECRET nicht gesetzt."
    if "." not in key:
        return False, None, "Format ungültig."
    payload_b64_str, sig = key.rsplit(".", 1)
    expected = _sign(payload_b64_str.encode("ascii"), secret)
    if not hmac.compare_digest(sig, expected):
        return False, None, "Signatur ungültig."
    try:
        padded = payload_b64_str + "=" * (-len(payload_b64_str) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return False, None, "Payload nicht lesbar."
    return True, payload, ""
