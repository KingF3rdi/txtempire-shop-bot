"""Snipe-Premium Preise pro Guild (Fallback: config/.env)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from utils.credits import currency_to_credits

if TYPE_CHECKING:
    from bot import ShopBot

# credits_amount / plan codes
SNIPE_PLAN_14 = 14
SNIPE_PLAN_30 = 30
SNIPE_PLAN_LIFETIME = 0


def normalize_snipe_plan(raw: int | float | None) -> int:
    """14 | 30 | 0 (Lifetime)."""
    try:
        plan = int(float(raw if raw is not None else 14))
    except (TypeError, ValueError):
        return SNIPE_PLAN_14
    if plan == 0 or plan >= 3650:
        return SNIPE_PLAN_LIFETIME
    if plan >= 30:
        return SNIPE_PLAN_30
    return SNIPE_PLAN_14


def snipe_plan_title(plan: int) -> str:
    plan = normalize_snipe_plan(plan)
    if plan == SNIPE_PLAN_LIFETIME:
        return "Lifetime"
    if plan == SNIPE_PLAN_30:
        return "30 Tage"
    return "14 Tage"


def premium_snipe_label(*, plan: int | None = None, days: int | None = None) -> str:
    """Kurztext für Snipe-Kontingent (pro Kategorie: Minecraft/Roblox/Discord)."""
    resolved = normalize_snipe_plan(plan if plan is not None else days)
    if resolved == SNIPE_PLAN_LIFETIME:
        return f"{config.SNIPE_PREMIUM_LIFETIME_DAILY} Names/Tag je Kategorie"
    if resolved == SNIPE_PLAN_30:
        return f"{config.SNIPE_PREMIUM_30_DAILY} Names/Tag je Kategorie"
    return f"{config.SNIPE_PREMIUM_14_DAILY} Names/Tag je Kategorie"


async def get_snipe_prices(bot: ShopBot, guild_id: int) -> dict[str, float]:
    settings = await bot.db.ensure_guild(guild_id)
    price_14 = settings.get("snipe_price_14")
    price_30 = settings.get("snipe_price_30")
    price_life = settings.get("snipe_price_lifetime")
    cred_14 = settings.get("snipe_credits_14")
    cred_30 = settings.get("snipe_credits_30")
    cred_life = settings.get("snipe_credits_lifetime")

    p14 = (
        float(price_14)
        if price_14 is not None
        else float(config.SNIPE_PREMIUM_14_PRICE)
    )
    p30 = (
        float(price_30)
        if price_30 is not None
        else float(config.SNIPE_PREMIUM_30_PRICE)
    )
    plife = (
        float(price_life)
        if price_life is not None
        else float(config.SNIPE_PREMIUM_LIFETIME_PRICE)
    )
    c14 = (
        float(cred_14)
        if cred_14 is not None
        else float(config.SNIPE_PREMIUM_14_CREDITS or currency_to_credits(p14))
    )
    c30 = (
        float(cred_30)
        if cred_30 is not None
        else float(config.SNIPE_PREMIUM_30_CREDITS or currency_to_credits(p30))
    )
    clife = (
        float(cred_life)
        if cred_life is not None
        else float(
            config.SNIPE_PREMIUM_LIFETIME_CREDITS or currency_to_credits(plife)
        )
    )
    return {
        "price_14": p14,
        "price_30": p30,
        "price_lifetime": plife,
        "credits_14": round(c14, 2),
        "credits_30": round(c30, 2),
        "credits_lifetime": round(clife, 2),
    }


def snipe_price_for_plan(prices: dict[str, float], plan: int) -> float:
    plan = normalize_snipe_plan(plan)
    if plan == SNIPE_PLAN_LIFETIME:
        return float(prices["price_lifetime"])
    if plan == SNIPE_PLAN_30:
        return float(prices["price_30"])
    return float(prices["price_14"])


def snipe_credits_for_plan(prices: dict[str, float], plan: int) -> float:
    plan = normalize_snipe_plan(plan)
    if plan == SNIPE_PLAN_LIFETIME:
        return float(prices["credits_lifetime"])
    if plan == SNIPE_PLAN_30:
        return float(prices["credits_30"])
    return float(prices["credits_14"])
