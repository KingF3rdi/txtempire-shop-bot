from __future__ import annotations

from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from bot import ShopBot


def _tier_text(quota: dict) -> str:
    if quota.get("lifetime"):
        return "Lifetime (unbegrenzt)"
    if quota.get("premium") and quota.get("unlimited"):
        return "Premium 30 Tage (unbegrenzt)"
    if quota.get("premium"):
        return f"Premium 14 Tage ({config.SNIPE_PREMIUM_14_DAILY}/Tag)"
    return f"Free ({config.SNIPE_FREE_DAILY}/Tag)"


async def get_snipe_quota(
    bot: ShopBot, guild_id: int, user_id: int, *, is_staff: bool = False
) -> dict:
    """
    Returns:
      limit, used, remaining, premium, unlimited, lifetime, expires_at
    """
    used = await bot.db.get_snipe_usage_today(guild_id, user_id)
    if is_staff:
        return {
            "limit": 999_999,
            "used": used,
            "remaining": 999_999,
            "premium": True,
            "unlimited": True,
            "lifetime": False,
            "expires_at": None,
            "staff": True,
        }

    lifetime = await bot.db.is_snipe_premium_lifetime(guild_id, user_id)
    premium = lifetime or await bot.db.is_snipe_premium(guild_id, user_id)
    expires = (
        await bot.db.get_snipe_premium_expires(guild_id, user_id)
        if premium and not lifetime
        else ("Lifetime" if lifetime else None)
    )
    unlimited = False
    if premium:
        unlimited = lifetime or await bot.db.is_snipe_premium_unlimited(
            guild_id, user_id
        )

    if unlimited:
        limit = 999_999
    elif premium:
        limit = config.SNIPE_PREMIUM_14_DAILY
    else:
        limit = config.SNIPE_FREE_DAILY

    remaining = 999_999 if unlimited else max(0, limit - used)
    return {
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "premium": premium,
        "unlimited": unlimited,
        "lifetime": lifetime,
        "expires_at": expires,
        "staff": False,
    }


async def reserve_snipe_quota(
    bot: ShopBot,
    guild_id: int,
    user_id: int,
    want: int,
    *,
    is_staff: bool = False,
) -> tuple[int, dict]:
    """
    Reserviert bis zu `want` Names. Wirft ValueError wenn nichts übrig.
    Returns (allowed_count, quota_after).
    """
    want = max(1, int(want))
    quota = await get_snipe_quota(bot, guild_id, user_id, is_staff=is_staff)
    if is_staff or quota.get("unlimited"):
        used = await bot.db.increment_snipe_usage(guild_id, user_id, want)
        quota["used"] = used
        quota["remaining"] = 999_999
        return want, quota

    remaining = int(quota["remaining"])
    if remaining <= 0:
        raise ValueError(
            f"Tageslimit erreicht ({_tier_text(quota)}).\n"
            f"Heute: **{quota['used']}/{quota['limit']}** Names.\n\n"
            f"• Free: **{config.SNIPE_FREE_DAILY}/Tag**\n"
            f"• 14 Tage Premium: **{config.SNIPE_PREMIUM_14_DAILY}/Tag**\n"
            f"• 30 Tage / Lifetime: **unbegrenzt**\n"
            "Premium: Button **Premium kaufen** oder `/snipepremium`."
        )
    allowed = min(want, remaining)
    used = await bot.db.increment_snipe_usage(guild_id, user_id, allowed)
    quota["used"] = used
    quota["remaining"] = max(0, quota["limit"] - used)
    return allowed, quota


def format_snipe_quota_line(quota: dict) -> str:
    if quota.get("staff"):
        return f"Staff · heute **{quota['used']}** Names"
    if quota.get("lifetime") or quota.get("unlimited"):
        until = quota.get("expires_at") or "Lifetime"
        return f"Premium · unbegrenzt · `{until}` · heute **{quota['used']}**"
    return (
        f"{_tier_text(quota)} · heute **{quota['used']}/{quota['limit']}** "
        f"(noch {quota['remaining']})"
    )
