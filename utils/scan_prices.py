"""Scan-Premium Preise pro Guild (Fallback: config/.env)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from utils.credits import currency_to_credits

if TYPE_CHECKING:
    from bot import ShopBot


def premium_scan_label(*, days: int | None = None, unlimited: bool = False) -> str:
    """Kurztext für Premium-Kontingent."""
    if unlimited or (days is not None and int(days) >= 30):
        return "unbegrenzte Scans"
    return f"{config.SCAN_PREMIUM_DAILY} Scans/Tag"


async def get_scan_prices(bot: ShopBot, guild_id: int) -> dict[str, float]:
    settings = await bot.db.ensure_guild(guild_id)
    price_14 = settings.get("scan_price_14")
    price_30 = settings.get("scan_price_30")
    cred_14 = settings.get("scan_credits_14")
    cred_30 = settings.get("scan_credits_30")

    p14 = float(price_14) if price_14 is not None else float(config.SCAN_PREMIUM_14_PRICE)
    p30 = float(price_30) if price_30 is not None else float(config.SCAN_PREMIUM_30_PRICE)
    c14 = (
        float(cred_14)
        if cred_14 is not None
        else float(config.SCAN_PREMIUM_14_CREDITS or currency_to_credits(p14))
    )
    c30 = (
        float(cred_30)
        if cred_30 is not None
        else float(config.SCAN_PREMIUM_30_CREDITS or currency_to_credits(p30))
    )
    return {
        "price_14": p14,
        "price_30": p30,
        "credits_14": round(c14, 2),
        "credits_30": round(c30, 2),
    }
