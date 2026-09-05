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
    """@everyone darf nicht schreiben; Bot schon. Staff bleibt über Rollen-Overrides."""
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
            reason="Vouch-Channel: nur mit freiem Vouch schreiben",
        )
        await channel.set_permissions(
            me,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            reason="Vouch-Channel: Bot darf posten",
        )
        return True
    except discord.HTTPException:
        return False


def is_staff_writer(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return bool(
        perms.administrator
        or perms.manage_guild
        or perms.manage_messages
        or perms.manage_channels
    )


async def user_has_free_vouch(bot: ShopBot, guild_id: int, user_id: int) -> bool:
    order = await bot.db.get_unused_vouch_order(guild_id, user_id)
    return order is not None


async def sync_vouch_write_permission(
    bot: ShopBot,
    *,
    guild_id: int,
    user_id: int,
) -> None:
    """Send-Recht setzen/entfernen je nach freiem Vouch."""
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

    if is_staff_writer(member):
        return

    has_vouch = await user_has_free_vouch(bot, guild_id, user_id)
    try:
        if has_vouch:
            await channel.set_permissions(
                member,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                reason="Freier Vouch — Schreibrecht im Vouch-Channel",
            )
        else:
            overwrite = channel.overwrites_for(member)
            if overwrite.is_empty():
                return
            await channel.set_permissions(
                member,
                overwrite=None,
                reason="Kein freier Vouch — Schreibrecht entfernt",
            )
    except discord.HTTPException:
        pass


async def revoke_vouch_write_permission(
    bot: ShopBot, *, guild_id: int, user_id: int
) -> None:
    await sync_vouch_write_permission(bot, guild_id=guild_id, user_id=user_id)
