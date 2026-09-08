"""
tweak_vouch.py
===============

Gemeinsamer Vouch-Kanal für alle Tweak-Produkte (Ferdi Mousetweaks,
y3zz GPU Tweaks, ...). Bewusst getrennt vom generellen `/vouch`-Rating-
System (cogs/vouch.py, Bestellungen-Tabelle) - dort wird die ÄLTESTE offene
Bestellung nacheinander abgearbeitet, hier soll aber immer die zuletzt
bestätigte Tweak-Bestellung gemeint sein. Direkt nach jeder Key-Bestätigung
(Ticket-Confirm oder /key generate) wird die DM verschickt - das ist per
Definition immer der neueste Kauf.

Eigene Tabelle (tweak_vouch_settings) - keine Änderung an db/database.py
nötig.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from utils.embeds import base_embed

if TYPE_CHECKING:
    from bot import ShopBot


async def _ensure_table(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tweak_vouch_settings (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER
        );
        """
    )
    await bot.db.db.commit()


async def get_channel_id(bot: "ShopBot", guild_id: int) -> Optional[int]:
    await _ensure_table(bot)
    row = await bot.db.fetchone(
        "SELECT channel_id FROM tweak_vouch_settings WHERE guild_id = ?", (guild_id,)
    )
    return int(row["channel_id"]) if row and row["channel_id"] else None


async def set_channel_id(bot: "ShopBot", guild_id: int, channel_id: Optional[int]) -> None:
    await _ensure_table(bot)
    await bot.db.db.execute(
        """
        INSERT INTO tweak_vouch_settings (guild_id, channel_id) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id
        """,
        (guild_id, channel_id),
    )
    await bot.db.db.commit()


async def request_vouch(
    bot: "ShopBot",
    guild: discord.Guild,
    member: "discord.abc.User",
    *,
    product: str,
    tier_label: str,
) -> bool:
    """DM an den Kunden direkt nach Kauf-Bestätigung - bezieht sich immer auf
    genau diesen (den neuesten) Kauf. Best-effort: gibt False zurück (statt
    Fehler zu werfen), wenn kein Kanal gesetzt ist oder die DM fehlschlägt."""
    channel_id = await get_channel_id(bot, guild.id)
    if not channel_id:
        return False
    channel = guild.get_channel(channel_id)
    if channel is None:
        return False
    try:
        await member.send(
            embed=base_embed(
                "⭐ Vouch da lassen?",
                f"Danke für deinen Kauf **{product} — {tier_label}**!\n\n"
                f"Wenn du zufrieden bist, freuen wir uns riesig über einen Vouch in "
                f"{channel.mention}, z. B.:\n"
                f"`✅ Vouch für {product} ({tier_label}) - lief einwandfrei, schneller Service!`",
            )
        )
        return True
    except discord.HTTPException:
        return False
