"""Vouch-/Bestell-Stats — immer nur unter dem letzten Vouch im Channel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.embeds import format_price

if TYPE_CHECKING:
    from bot import ShopBot


def _stars_avg(avg: float | None) -> str:
    if avg is None:
        return "—"
    filled = max(0, min(5, round(avg)))
    return f"{'★' * filled}{'☆' * (5 - filled)} ({avg:.2f})"


async def count_vouch_channel_messages(
    channel: discord.TextChannel,
) -> int:
    """Zählt alle Messages im Vouch-Channel (ohne Stats-Übersicht)."""
    total = 0
    async for msg in channel.history(limit=None):
        if _is_stats_message(msg):
            continue
        total += 1
    return total


def _is_stats_message(msg: discord.Message) -> bool:
    if not msg.embeds:
        return False
    title = msg.embeds[0].title or ""
    return "Vouch- & Bestell" in title or title.startswith("📊 Vouch")


def build_vouch_stats_embed(
    *,
    channel_vouches: int,
    orders_completed: int,
    revenue: float,
    unique_buyers: int,
    vouches_used: int,
    avg_rating: float | None,
    rated_count: int,
) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Vouch- & Bestell-Übersicht",
        color=0x2B6CB0,
    )
    embed.add_field(
        name="Gesamt-Vouches (Channel)",
        value=(
            f"**{channel_vouches}** Nachricht(en) im Vouch-Channel\n"
            "_Alle Messages werden mitgezählt._"
        ),
        inline=False,
    )
    embed.add_field(
        name="Vouch-Übersicht",
        value=(
            f"**Abgegebene Vouches (DB):** {vouches_used}\n"
            f"**Mit Sterne-Rating:** {rated_count}\n"
            f"**Ø Bewertung:** {_stars_avg(avg_rating)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Bestellungs-Stats",
        value=(
            f"**Abgeschlossene Käufe:** {orders_completed}\n"
            f"**Unique Käufer:** {unique_buyers}\n"
            f"**Umsatz:** {format_price(revenue)}"
        ),
        inline=False,
    )
    embed.set_footer(
        text="Nur unter dem letzten Vouch · wird bei jedem neuen Vouch aktualisiert"
    )
    return embed


async def refresh_vouch_stats_under_latest(
    bot: ShopBot,
    channel: discord.TextChannel,
    guild_id: int,
) -> discord.Message | None:
    """
    Löscht alte Stats-Nachrichten und postet eine neue am Channel-Ende
    (unter dem zuletzt geposteten Vouch).
    """
    settings = await bot.db.ensure_guild(guild_id)
    old_id = settings.get("vouch_stats_message_id")
    old_id_int = int(old_id) if old_id else None

    # Alte / verwaiste Stats-Messages entfernen
    to_delete: list[discord.Message] = []
    async for msg in channel.history(limit=50):
        if _is_stats_message(msg) or (
            old_id_int is not None and msg.id == old_id_int
        ):
            to_delete.append(msg)
    for msg in to_delete:
        try:
            await msg.delete()
        except discord.HTTPException:
            pass

    channel_count = await count_vouch_channel_messages(channel)
    db_stats = await bot.db.get_vouch_and_order_stats(guild_id)
    embed = build_vouch_stats_embed(
        channel_vouches=channel_count,
        orders_completed=db_stats["orders_completed"],
        revenue=db_stats["revenue"],
        unique_buyers=db_stats["unique_buyers"],
        vouches_used=db_stats["vouches_used"],
        avg_rating=db_stats["avg_rating"],
        rated_count=db_stats["rated_count"],
    )
    try:
        msg = await channel.send(embed=embed)
    except discord.HTTPException:
        return None

    await bot.db.update_guild_settings(guild_id, vouch_stats_message_id=msg.id)
    return msg
