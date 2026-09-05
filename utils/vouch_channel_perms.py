"""Vouch-Channel: Schreiben nur mit freiem Vouch (oder Staff)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot import ShopBot


async def get_vouch_text_channel(
    bot: ShopBot, guild_id: int
) -> discord.TextChannel | None:
    guild = bot.get_guild(guild_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(guild_id)
        except discord.HTTPException:
            return None
    settings = await bot.db.ensure_guild(guild_id)
    ch_id = settings.get("vouch_channel_id")
    if not ch_id:
        return None
    ch = guild.get_channel(int(ch_id))
    if ch is None:
        try:
            fetched = await guild.fetch_channel(int(ch_id))
            ch = fetched if isinstance(fetched, discord.TextChannel) else None
        except discord.HTTPException:
            return None
    return ch if isinstance(ch, discord.TextChannel) else None


async def lock_vouch_channel_defaults(channel: discord.TextChannel) -> bool:
    """@everyone darf nicht schreiben; nur der Bot postet Vouches."""
    me = channel.guild.me
    if me is None or not channel.permissions_for(me).manage_roles:
        return False
    try:
        await channel.set_permissions(
            channel.guild.default_role,
            send_messages=False,
            create_public_threads=False,
            create_private_threads=False,
            send_messages_in_threads=False,
            reason="Vouch-Channel: keine normalen Nachrichten",
        )
        await channel.set_permissions(
            me,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            reason="Vouch-Channel: Bot darf Vouches posten",
        )
        return True
    except discord.HTTPException:
        return False


async def sync_vouch_write_permission(
    bot: ShopBot,
    *,
    guild_id: int,
    user_id: int,
) -> None:
    """Individuelle User-Schreibrechte entfernen (Channel bleibt gesperrt)."""
    channel = await get_vouch_text_channel(bot, guild_id)
    if channel is None:
        return
    guild = channel.guild
    me = guild.me
    if me is None or not channel.permissions_for(me).manage_roles:
        return

    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            return

    try:
        overwrite = channel.overwrites_for(member)
        if overwrite.is_empty():
            return
        await channel.set_permissions(
            member,
            overwrite=None,
            reason="Vouch-Channel: keine User-Schreibrechte",
        )
    except discord.HTTPException:
        pass


async def revoke_vouch_write_permission(
    bot: ShopBot, *, guild_id: int, user_id: int
) -> None:
    await sync_vouch_write_permission(bot, guild_id=guild_id, user_id=user_id)
