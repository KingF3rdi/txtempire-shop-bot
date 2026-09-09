from __future__ import annotations

from typing import TYPE_CHECKING

import config
from integrations.shop_api import shop_api

if TYPE_CHECKING:
    from bot import ShopBot


async def sync_revenue_now(bot: "ShopBot") -> None:
    """Berechnet die kombinierten Discord-Einnahmen neu und sendet sie an
    die Website. Best-effort — Fehler werden nur geloggt (siehe shop_api)."""
    if not shop_api.enabled or not config.GUILD_ID:
        return
    total = await bot.db.get_combined_bot_revenue(config.GUILD_ID)
    await shop_api.sync_revenue(total)
