"""Schickt/aktualisiert das Duel-Invsee-Inventarbild in der DM des Käufers.

Erste Meldung: neue DM mit Bild. Jede weitere Meldung für denselben Watch:
dieselbe Nachricht wird per Edit aktualisiert (kein Nachrichten-Spam, kein
Rate-Limit-Risiko durch wiederholtes Senden).
"""
from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import discord

from utils.duel_invsee_render import render_inventory_image
from utils.duel_invsee_store import set_watch_dm
from utils.embeds import base_embed

if TYPE_CHECKING:
    from bot import ShopBot


async def send_or_update_watch_dm(
    bot: "ShopBot", watch: dict[str, Any], *, ign: str, items: list[dict]
) -> bool:
    buyer_id = int(watch["buyer_discord_id"])
    png = render_inventory_image(items, ign)
    embed = base_embed("🗡️ Duel Invsee", f"Live-Inventar von **{ign}** — aktualisiert sich, solange dein Kauf läuft.")

    embed.set_image(url="attachment://invsee.png")

    channel_id = watch.get("dm_channel_id")
    message_id = watch.get("dm_message_id")
    if channel_id and message_id:
        try:
            channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))  # type: ignore[union-attr]
            await message.edit(embed=embed, attachments=[discord.File(io.BytesIO(png), filename="invsee.png")])
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass  # DM/Nachricht weg — unten neu anlegen

    try:
        user = bot.get_user(buyer_id) or await bot.fetch_user(buyer_id)
        dm = user.dm_channel or await user.create_dm()
        file = discord.File(io.BytesIO(png), filename="invsee.png")
        message = await dm.send(embed=embed, file=file)
        await set_watch_dm(bot, int(watch["id"]), dm.id, message.id)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False
