"""Minecraft Account-Linking + Auto-Confirm Setup."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from integrations.mc_api import McApiServer
from utils.embeds import error_embed, success_embed, warn_embed
from views.mc_link_views import (
    LinkIgnModal,
    collect_bot_link_status,
    format_bot_link_status,
    post_or_refresh_mc_link_panel,
    refresh_mc_link_panel_status,
)

if TYPE_CHECKING:
    from bot import ShopBot


class McLinkCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot
        self.api = McApiServer(bot)

    async def cog_load(self) -> None:
        # API erst nach Discord-Login starten (on_ready) — sonst wirkt
        # „Listening“ als wäre der Bot online, obwohl Login noch scheitert.
        pass

    async def cog_unload(self) -> None:
        await self.api.stop()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.api.listening:
            return
        await self.api.start()
        if self.api.listening:
            public = (
                os.getenv("SERVER_IP")
                or os.getenv("PUBLIC_IP")
                or "95.216.12.48"
            )
            print(
                "[MC-API] Bereit für Fabric-Watcher.\n"
                f"  Lauscht auf Port {int(config.MC_API_PORT)} "
                f"(SERVER_PORT={os.getenv('SERVER_PORT')!r})\n"
                f"  Test: http://{public}:{int(config.MC_API_PORT)}/mc/v1/health\n"
                f"  Mod apiUrl: http://{public}:{int(config.MC_API_PORT)}"
            )
        else:
            print(
                "[MC-API] NICHT aktiv — setze MC_API_KEY in .env, "
                "sonst keine Link-/Payment-Bestätigung."
            )

    # Attribute darf nicht mit bot_/cog_ beginnen (discord.py)
    status_group = app_commands.Group(
        name="bot",
        description="Bot-Status & Linking",
    )

    @status_group.command(
        name="status",
        description="Bot-/Watcher-Status aufs Minecraft-Link-Panel schreiben",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def show_link_status(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        status = await collect_bot_link_status(self.bot, interaction.guild.id)
        msg = await refresh_mc_link_panel_status(self.bot, interaction.guild.id)
        text = format_bot_link_status(status)
        if msg is None:
            await interaction.followup.send(
                embed=warn_embed(
                    "Bot-Status",
                    f"{text}\n\n"
                    "_Kein Link-Panel gefunden — bitte zuerst `/mclinkpanel` posten._",
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "Status aufs Link-Panel geschrieben",
                f"{text}\n\nPanel: {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="mclinkpanel",
        description="Panel zum Verlinken / Unverifizieren des Minecraft-Accounts",
    )
    @app_commands.describe(channel="Channel für das Panel (Standard: hier)")
    @app_commands.default_permissions(manage_guild=True)
    async def mclinkpanel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Nur Text-Channel"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        msg = await post_or_refresh_mc_link_panel(self.bot, target)
        await interaction.followup.send(
            embed=success_embed(
                "MC-Link-Panel",
                f"Panel in {target.mention}: {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="link",
        description="Minecraft-Account mit Discord verknüpfen (IGN + Code)",
    )
    async def link_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        await interaction.response.send_modal(
            LinkIgnModal(self.bot, interaction.guild.id)
        )

    @app_commands.command(
        name="unlink",
        description="Minecraft-Account-Verknüpfung entfernen (unverifizieren)",
    )
    async def unlink_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        link = await self.bot.db.get_mc_link(
            interaction.guild.id, interaction.user.id
        )
        if not link:
            await interaction.response.send_message(
                embed=warn_embed(
                    "Nichts zu lösen",
                    "Es ist kein Minecraft-Account verknüpft.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.unlink_mc_account(
            interaction.guild.id, interaction.user.id
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Unverifiziert",
                f"Verknüpfung mit **{link['ign']}** entfernt.",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="mcstatus",
        description="Zeigt deinen verknüpften Minecraft-Account",
    )
    async def mcstatus(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        link = await self.bot.db.get_mc_link(
            interaction.guild.id, interaction.user.id
        )
        pending = await self.bot.db.get_pending_mc_link_code(
            interaction.guild.id, interaction.user.id
        )
        if link:
            text = (
                f"Verknüpft mit **{link['ign']}** "
                f"(seit `{link.get('linked_at') or '—'}`)."
            )
        else:
            text = "Nicht verknüpft."
        if pending:
            text += (
                f"\nOffener Code für **{pending['ign']}**: `{pending['code']}` "
                f"— Ingame: `{config.mc_link_command(str(pending['code']))}`"
            )
        await interaction.response.send_message(
            embed=success_embed("MC-Status", text)
            if link
            else warn_embed("MC-Status", text),
            ephemeral=True,
        )

    @app_commands.command(
        name="mcsetup",
        description="Minecraft Auto-Confirm / Log-Channel konfigurieren",
    )
    @app_commands.describe(
        log_channel="Channel für Link- und Payment-Logs",
        auto_confirm="Tickets nach passender Zahlung automatisch bestätigen",
    )
    @app_commands.default_permissions(administrator=True)
    async def mcsetup(
        self,
        interaction: discord.Interaction,
        log_channel: Optional[discord.TextChannel] = None,
        auto_confirm: Optional[bool] = None,
    ) -> None:
        assert interaction.guild is not None
        fields: dict = {}
        if log_channel is not None:
            fields["mc_payment_log_channel_id"] = log_channel.id
        if auto_confirm is not None:
            fields["mc_auto_confirm"] = 1 if auto_confirm else 0
        if not fields:
            settings = await self.bot.db.ensure_guild(interaction.guild.id)
            ch_id = settings.get("mc_payment_log_channel_id")
            ch = (
                interaction.guild.get_channel(int(ch_id)).mention  # type: ignore[union-attr]
                if ch_id
                else "—"
            )
            ac = "an" if int(settings.get("mc_auto_confirm") or 0) else "aus"
            api = (
                f"`http://{config.MC_API_HOST}:{config.MC_API_PORT}`"
                if config.MC_API_KEY
                else "_MC_API_KEY fehlt_"
            )
            await interaction.response.send_message(
                embed=success_embed(
                    "MC-Setup",
                    f"**Log-Channel:** {ch}\n"
                    f"**Auto-Confirm:** {ac}\n"
                    f"**Mod-API:** {api}\n"
                    f"**Code-TTL:** {config.MC_LINK_CODE_TTL_MINUTES} Min.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild_settings(interaction.guild.id, **fields)
        await interaction.response.send_message(
            embed=success_embed("MC-Setup gespeichert", "Einstellungen aktualisiert."),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(McLinkCog(bot))
