from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from integrations.shop_api import shop_api
from utils.embeds import error_embed, format_price, order_ref, success_embed
from utils.vouch_channel_perms import (
    get_vouch_text_channel,
    lock_vouch_channel_defaults,
    sync_vouch_write_permission,
)

if TYPE_CHECKING:
    from bot import ShopBot


def _stars(rating: int) -> str:
    rating = max(1, min(5, int(rating)))
    return "★" * rating + "☆" * (5 - rating)


def _resolve_guild_id(interaction: discord.Interaction) -> int | None:
    if interaction.guild is not None:
        return interaction.guild.id
    if config.GUILD_ID:
        return config.GUILD_ID
    return None


async def _get_vouch_channel(
    bot: ShopBot, guild_id: int
) -> discord.TextChannel | None:
    return await get_vouch_text_channel(bot, guild_id)


async def _post_local_vouch_embed(
    channel: discord.TextChannel,
    *,
    interaction: discord.Interaction,
    rating: int,
    message: str,
    order: dict,
) -> discord.Message:
    stars = _stars(rating)
    embed = discord.Embed(
        title="Neuer Vouch",
        description=message[:1500],
        color=0x2B6CB0,
    )
    embed.add_field(name="Bewertung", value=stars, inline=True)
    embed.add_field(name="Bestellung", value=order_ref(order), inline=True)
    embed.add_field(
        name="Betrag", value=format_price(float(order["total"])), inline=True
    )
    if order.get("ign"):
        embed.add_field(name="IGN", value=order["ign"], inline=True)
    embed.set_author(
        name=str(interaction.user),
        icon_url=interaction.user.display_avatar.url,
    )
    return await channel.send(embed=embed)


async def _submit_website_vouch(
    interaction: discord.Interaction,
    rating: int,
    message: str,
) -> dict | None:
    if not shop_api.enabled:
        return None

    pending = await shop_api.fetch_pending_vouches(str(interaction.user.id))
    if not pending:
        return None

    order = pending[0]
    return await shop_api.submit_vouch(
        discord_id=str(interaction.user.id),
        order_id=int(order["order_id"]),
        rating=int(rating),
        message=message,
        giver_name=str(interaction.user),
    )


async def _submit_local_vouch(
    bot: ShopBot,
    interaction: discord.Interaction,
    rating: int,
    message: str,
) -> bool:
    """Discord-Ticket-Vouch (funktioniert im Server und per DM)."""
    guild_id = _resolve_guild_id(interaction)
    if guild_id is None:
        return False

    order = await bot.db.get_unused_vouch_order_for_user(
        interaction.user.id, guild_id
    )
    if not order:
        return False

    channel = await _get_vouch_channel(bot, int(order["guild_id"]))
    if channel is None:
        await interaction.response.send_message(
            embed=error_embed(
                "Vouch-Channel fehlt",
                "Admin muss `/setup` mit einem Vouch-Channel ausführen.",
            ),
            ephemeral=True,
        )
        return True

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    await bot.db.mark_vouch_used_with_rating(int(order["id"]), int(rating))
    await _post_local_vouch_embed(
        channel,
        interaction=interaction,
        rating=rating,
        message=message,
        order=order,
    )

    await sync_vouch_write_permission(
        bot, guild_id=int(order["guild_id"]), user_id=interaction.user.id
    )

    from utils.vouch_stats import refresh_vouch_stats_under_latest

    await refresh_vouch_stats_under_latest(
        bot, channel, int(order["guild_id"])
    )

    if shop_api.enabled:
        stars = _stars(rating)
        await shop_api.sync_vouch(
            giver_name=str(interaction.user),
            message=f"{stars} — {message[:500]}",
            is_positive=rating >= 4,
            external_id=int(order["id"]),
        )

    await interaction.followup.send(
        embed=success_embed(
            "Vouch gesendet",
            f"Danke! Dein Vouch zu Bestellung {order_ref(order)} wurde gepostet.",
        ),
        ephemeral=True,
    )
    return True


class VouchCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Channel-Defaults einmalig härten (idempotent)
        for guild in self.bot.guilds:
            channel = await _get_vouch_channel(self.bot, guild.id)
            if channel is not None:
                await lock_vouch_channel_defaults(channel)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Vouch-Channel: jede neue User-Nachricht sofort löschen (Bestand bleibt)."""
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        settings = await self.bot.db.ensure_guild(message.guild.id)
        vouch_ch_id = settings.get("vouch_channel_id")
        if not vouch_ch_id or int(vouch_ch_id) != message.channel.id:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass

    @app_commands.command(
        name="vouch",
        description="Einmalig pro Kauf eine Bewertung hinterlassen (Server oder DM)",
    )
    @app_commands.describe(
        rating="Bewertung 1–5 Sterne",
        message="Dein Vouch-Text",
    )
    async def vouch(
        self,
        interaction: discord.Interaction,
        rating: app_commands.Range[int, 1, 5],
        message: str,
    ) -> None:
        text = message.strip()
        if not text:
            await interaction.response.send_message(
                embed=error_embed("Leerer Text", "Bitte eine Nachricht eingeben."),
                ephemeral=True,
            )
            return

        # Discord-Shop (lokal, ohne Website) — Server + DM
        if await _submit_local_vouch(self.bot, interaction, int(rating), text):
            return

        # Website-Bestellungen (nur wenn API konfiguriert)
        if shop_api.enabled:
            result = await _submit_website_vouch(interaction, int(rating), text)
            if result:
                product = result.get("product_name") or "dein Kauf"
                await interaction.response.send_message(
                    embed=success_embed(
                        "Vouch gesendet",
                        f"Danke! Dein Vouch zu **{product}** "
                        f"(Bestellung #{result.get('order_id')}) wurde gespeichert.",
                    ),
                    ephemeral=True,
                )
                return

            hint = (
                "Du brauchst einen **bestätigten Discord-Kauf** ohne bereits genutzten Vouch.\n"
                "Nach Staff-Bestätigung im Ticket kannst du hier oder per DM `/vouch` nutzen."
            )
            if interaction.guild is None and not config.GUILD_ID:
                hint += (
                    "\n\n_DM-Vouch:_ `GUILD_ID` in der Bot-`.env` setzen und Bot neu starten."
                )
            elif shop_api.enabled:
                hint += f"\n\nWebsite-Käufe: {config.FRONTEND_URL.rstrip('/')}/account"
            await interaction.response.send_message(
                embed=error_embed("Kein Vouch verfügbar", hint),
                ephemeral=True,
            )

    @app_commands.command(
        name="vouchstats",
        description="Vouch-/Bestell-Übersicht unter dem letzten Vouch neu posten (Staff)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def vouchstats(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        channel = await _get_vouch_channel(self.bot, interaction.guild.id)
        if channel is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Vouch-Channel fehlt",
                    "Zuerst `/setup` mit Vouch-Channel ausführen.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        from utils.vouch_stats import refresh_vouch_stats_under_latest

        await lock_vouch_channel_defaults(channel)
        msg = await refresh_vouch_stats_under_latest(
            self.bot, channel, interaction.guild.id
        )
        if msg is None:
            await interaction.followup.send(
                embed=error_embed("Konnte Stats nicht posten"),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "Stats aktualisiert",
                f"Übersicht unter dem letzten Vouch: {msg.jump_url}",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(VouchCog(bot))
