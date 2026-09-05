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
      limit, used, remaining, premium, expires_at
    """
    if is_staff:
        used = await bot.db.get_scan_usage_today(guild_id, user_id)
        return {
            "limit": 999_999,
            "used": used,
            "remaining": 999_999,
            "premium": True,
            "expires_at": None,
            "staff": True,
        }

    premium = await bot.db.is_scan_premium(guild_id, user_id)
    expires = await bot.db.get_scan_premium_expires(guild_id, user_id) if premium else None
    limit = config.SCAN_PREMIUM_DAILY if premium else config.SCAN_FREE_DAILY
    used = await bot.db.get_scan_usage_today(guild_id, user_id)
    remaining = max(0, limit - used)
    return {
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "premium": premium,
        "expires_at": expires,
        "staff": False,
    }


async def consume_scan_quota(
    bot: ShopBot, guild_id: int, user_id: int, *, is_staff: bool = False
) -> dict:
    """Prüft Limit und zählt einen Scan. Wirft ValueError wenn aufgebraucht."""
    quota = await get_scan_quota(bot, guild_id, user_id, is_staff=is_staff)
    if not is_staff and quota["remaining"] <= 0:
        tier = "Premium (15/Tag)" if quota["premium"] else "Free (1/Tag)"
        raise ValueError(
            f"Tageslimit erreicht ({tier}). "
            f"Heute: **{quota['used']}/{quota['limit']}**.\n"
            "Hol dir **Scan Premium** mit `/scanpremium` (14 oder 30 Tage)."
        )
    used = await bot.db.increment_scan_usage(guild_id, user_id)
    quota["used"] = used
    quota["remaining"] = max(0, quota["limit"] - used) if not is_staff else 999_999
    return quota
