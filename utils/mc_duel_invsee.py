"""Duel Invsee — Credits eines Spielers per IGN belasten (vom Server-Plugin aufgerufen).

Der Server-Plugin (DuelClanPlugin) prüft SELBST, dass der Käufer gerade in
einem laufenden Duell gegen genau diesen Gegner steckt, bevor er hierher
POSTet — dieser Handler kümmert sich nur noch um die Bezahlung (IGN ->
verknüpfter Discord-Account -> Credits abbuchen).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot import ShopBot


async def handle_duel_invsee_charge(
    bot: "ShopBot",
    *,
    guild_id: int,
    ign: str,
    amount: float,
) -> dict[str, Any]:
    """Bucht `amount` Shop-Währung vom mit `ign` verknüpften Discord-Account ab."""
    link = await bot.db.get_mc_link_by_ign(guild_id, ign)
    if not link:
        return {"ok": False, "reason": "ign_not_linked"}

    user_id = int(link["user_id"])
    ok = await bot.db.try_deduct_credits(guild_id, user_id, amount)
    if not ok:
        balance = await bot.db.get_credits(guild_id, user_id)
        return {
            "ok": False,
            "reason": "insufficient_credits",
            "user_id": user_id,
            "balance": balance,
        }

    balance = await bot.db.get_credits(guild_id, user_id)
    return {"ok": True, "user_id": user_id, "balance": balance}
