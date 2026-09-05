from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot import ShopBot


async def sync_scan_premium_role(
    bot: ShopBot,
    guild: discord.Guild,
    user_id: int,
    *,
    force_grant: bool = False,
) -> str | None:
    """Vergibt/entfernt Premium-Rolle je nach Ablauf. Gibt Status-Text zurück."""
    settings = await bot.db.ensure_guild(guild.id)
    role_id = settings.get("scan_premium_role_id")
    if not role_id:
        return None
    role = guild.get_role(int(role_id))
    if role is None:
        return None

    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    active = force_grant or await bot.db.is_scan_premium(guild.id, user_id)
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return "Keine Rollen-Berechtigung"
    if role >= me.top_role:
        return "Premium-Rolle zu hoch"

    try:
        if active and role not in member.roles:
            await member.add_roles(role, reason="Scan Premium aktiv")
            return "Rolle vergeben"
        if not active and role in member.roles:
            await member.remove_roles(role, reason="Scan Premium abgelaufen")
            return "Rolle entfernt"
    except discord.HTTPException as e:
        return f"Rollen-Fehler: {e}"
    return None


async def sweep_expired_scan_premium_roles(bot: ShopBot) -> int:
    """Entfernt Premium-Rollen bei abgelaufenen Abos (Bot-Start)."""
    removed = 0
    rows = await bot.db.fetchall("SELECT guild_id, user_id, expires_at FROM scan_premium")
    now = datetime.now(timezone.utc)
    for row in rows:
        guild = bot.get_guild(int(row["guild_id"]))
        if guild is None:
            continue
        try:
            exp = datetime.strptime(str(row["expires_at"]), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if exp > now:
            # noch aktiv — Rolle sicherstellen
            await sync_scan_premium_role(
                bot, guild, int(row["user_id"]), force_grant=True
            )
            continue
        result = await sync_scan_premium_role(bot, guild, int(row["user_id"]))
        if result == "Rolle entfernt":
            removed += 1
    return removed


async def post_scan_log(
    bot: ShopBot,
    guild: discord.Guild,
    *,
    user: discord.abc.User,
    filename: str,
    summary: str,
    is_clean: bool,
    is_blocked: bool,
) -> None:
    settings = await bot.db.ensure_guild(guild.id)
    ch_id = settings.get("scan_log_channel_id")
    if not ch_id:
        return
    channel = guild.get_channel(int(ch_id))
    if not isinstance(channel, discord.TextChannel):
        return

    from utils.embeds import success_embed, warn_embed

    if is_clean:
        embed = success_embed(
            f"Scan OK — {filename}",
            f"User: {user.mention} (`{user.id}`)\n\n{summary[:3500]}",
        )
    elif is_blocked:
        embed = warn_embed(
            f"Scan KRITISCH — {filename}",
            f"User: {user.mention} (`{user.id}`)\n\n{summary[:3500]}",
        )
        embed.color = discord.Color.dark_red()
    else:
        embed = warn_embed(
            f"Scan Treffer — {filename}",
            f"User: {user.mention} (`{user.id}`)\n\n{summary[:3500]}",
        )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass
