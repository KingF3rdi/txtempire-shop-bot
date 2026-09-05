from __future__ import annotations

from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from bot import ShopBot


async def get_scan_quota(
    bot: ShopBot, guild_id: int, user_id: int, *, is_staff: bool = False
) -> dict:
    """
    Returns:
      limit, used, remaining, premium, unlimited, expires_at
    """
    if is_staff:
        used = await bot.db.get_scan_usage_today(guild_id, user_id)
        return {
            "limit": 999_999,
            "used": used,
            "remaining": 999_999,
            "premium": True,
            "unlimited": True,
            "expires_at": None,
            "staff": True,
        }

    premium = await bot.db.is_scan_premium(guild_id, user_id)
    expires = await bot.db.get_scan_premium_expires(guild_id, user_id) if premium else None
    unlimited = False
    if premium:
        unlimited = await bot.db.is_scan_premium_unlimited(guild_id, user_id)

    if unlimited:
        limit = 999_999
    elif premium:
        limit = config.SCAN_PREMIUM_DAILY
    else:
        limit = config.SCAN_FREE_DAILY

    used = await bot.db.get_scan_usage_today(guild_id, user_id)
    remaining = max(0, limit - used) if not unlimited else 999_999
    return {
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "premium": premium,
        "unlimited": unlimited,
        "expires_at": expires,
        "staff": False,
    }


async def consume_scan_quota(
    bot: ShopBot, guild_id: int, user_id: int, *, is_staff: bool = False
) -> dict:
    """Prüft Limit und zählt einen Scan. Wirft ValueError wenn aufgebraucht."""
    quota = await get_scan_quota(bot, guild_id, user_id, is_staff=is_staff)
    if not is_staff and not quota.get("unlimited") and quota["remaining"] <= 0:
        if quota["premium"]:
            tier = f"Premium ({config.SCAN_PREMIUM_DAILY}/Tag)"
        else:
            tier = f"Free ({config.SCAN_FREE_DAILY}/Tag)"
        raise ValueError(
            f"Tageslimit erreicht ({tier}). "
            f"Heute: **{quota['used']}/{quota['limit']}**.\n"
            "30-Tage Premium = **unbegrenzte** Scans. "
            "Sonst: `/scanpremium`."
        )
    used = await bot.db.increment_scan_usage(guild_id, user_id)
    quota["used"] = used
    if quota.get("unlimited") or is_staff:
        quota["remaining"] = 999_999
    else:
        quota["remaining"] = max(0, quota["limit"] - used)
    return quota
