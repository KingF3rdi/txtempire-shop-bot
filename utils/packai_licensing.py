"""
packai_licensing.py
===================

Offline Pack-AI-Lizenzkeys (HMAC-SHA256) — kein License-Server.

Format: PACKAI-{TAG}-{AAAA}-{BBBB}-{CCCC}-{DDDD}-{SIG8}
  TAG = 14D | 30D | LIFE
  Nonce = 16 Hex
  SIG8 = HMAC-SHA256(secret, "v1|{plan}|{nonce}")[:8] hex upper

PACKAI_LICENSE_SECRET in .env MUSS mit kLicenseSecret in
pack-ai-native/src/license_client.hpp übereinstimmen.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional, Tuple

import config

TIER_14D = "14d"
TIER_30D = "30d"
TIER_LIFETIME = "lifetime"

TIER_TAGS = {TIER_14D: "14D", TIER_30D: "30D", TIER_LIFETIME: "LIFE"}
TAG_TO_TIER = {v: k for k, v in TIER_TAGS.items()}

TIER_TOKENS = {TIER_14D: 50, TIER_30D: 200, TIER_LIFETIME: 2000}
TIER_DAYS = {TIER_14D: 14, TIER_30D: 30, TIER_LIFETIME: 0}
TIER_LABELS = {
    TIER_14D: "14 Tage · 50 Tokens",
    TIER_30D: "30 Tage · 200 Tokens",
    TIER_LIFETIME: "Lifetime · 2000 Tokens",
}


def _secret() -> str:
    return (
        getattr(config, "PACKAI_LICENSE_SECRET", "")
        or getattr(config, "PACKAI_LICENSE_API_SECRET", "")
        or ""
    ).strip()


def licensing_configured() -> bool:
    s = _secret()
    return bool(s) and s != "change-me"


def _sign(plan: str, nonce: str, secret: str) -> str:
    msg = f"v1|{plan}|{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:8].upper()


def generate_license_key(tier: str = TIER_30D) -> str:
    secret = _secret()
    if not secret:
        raise RuntimeError("PACKAI_LICENSE_SECRET ist nicht gesetzt (siehe .env).")
    tier = (tier or TIER_30D).lower().strip()
    if tier not in TIER_TAGS:
        raise ValueError(f"Ungültiger Plan: {tier}")
    nonce = secrets.token_hex(8).upper()
    chunks = "-".join(nonce[i : i + 4] for i in range(0, 16, 4))
    sig = _sign(tier, nonce, secret)
    return f"PACKAI-{TIER_TAGS[tier]}-{chunks}-{sig}"


def verify_own_key(key: str) -> Tuple[bool, Optional[dict], str]:
    secret = _secret()
    if not secret:
        return False, None, "PACKAI_LICENSE_SECRET nicht gesetzt."
    cleaned = (key or "").strip().upper()
    if not cleaned.startswith("PACKAI-"):
        return False, None, "Format ungültig."
    parts = [p for p in cleaned.split("-") if p]
    # PACKAI, TAG, 4 nonce, SIG
    if len(parts) != 7 or parts[0] != "PACKAI":
        return False, None, "Format ungültig."
    tag = parts[1]
    tier = TAG_TO_TIER.get(tag)
    if not tier:
        return False, None, "Unbekannter Plan."
    nonce = "".join(parts[2:6])
    sig = parts[6]
    if len(nonce) != 16 or len(sig) != 8:
        return False, None, "Key beschädigt."
    expect = _sign(tier, nonce, secret)
    if not hmac.compare_digest(sig, expect):
        return False, None, "Signatur ungültig."
    return True, {
        "tier": tier,
        "tokens": TIER_TOKENS[tier],
        "days": TIER_DAYS[tier],
        "label": TIER_LABELS[tier],
    }, ""
