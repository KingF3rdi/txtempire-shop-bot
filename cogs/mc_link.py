"""Minecraft Account-Linking + Auto-Confirm Setup."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from integrations.mc_api import McApiServer
from utils.embeds import error_embed, success_embed, warn_embed
from utils.mc_confirm import handle_mc_link_redeem, handle_mc_payment
from views.mc_link_views import (
    LinkIgnModal,
    collect_bot_link_status,
    format_bot_link_status,
    post_or_refresh_mc_link_panel,
    refresh_mc_link_panel_status,
)

if TYPE_CHECKING:
    from bot import ShopBot

# Fabric-Mod → Discord-Webhook (kein offener Server-Port nötig)
_MC_LINK = re.compile(
    r"^MC_LINK\s+([A-Z0-9\-]{4,24})\s+([A-Za-z0-9_]{3,16})\s+(\S+)\s*$",
    re.I,
)
_MC_PAY = re.compile(
    r"^MC_PAY\s+([A-Za-z0-9_]{3,16})\s+([0-9]+(?:\.[0-9]+)?)\s+(\S+)(?:\s+(.*))?$",
    re.I,
)
_MC_HB = re.compile(r"^MC_HB\s+(\S+)\s*$", re.I)


class McLinkCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot
        self.api = McApiServer(bot)

    async def cog_load(self) -> None:
        print(
            "[MC-API] Boot — "
            f"SERVER_PORT={os.getenv('SERVER_PORT')!r} "
            f"MC_API_PORT={config.MC_API_PORT} "
            f"KEY_SET={bool(config.MC_API_KEY)}"
        )
        await self.api.start()

    async def cog_unload(self) -> None:
        await self.api.stop()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.api.listening:
            await self.api.start()
        if self.api.listening:
            public = (
                os.getenv("SERVER_IP")
                or os.getenv("PUBLIC_IP")
                or "95.216.12.48"
            )
            print(
                "[MC-API] Bereit.\n"
                f"  HTTP Port {int(config.MC_API_PORT)} "
                f"(SERVER_PORT={os.getenv('SERVER_PORT')!r})\n"
                f"  Optional Test: http://{public}:{int(config.MC_API_PORT)}/mc/v1/health\n"
                "  Empfohlen bei Bot-Hosting: Discord-Webhook in der Mod-Config "
                "(discordWebhookUrl) — braucht keinen offenen Port."
            )
        else:
            print(
                "[MC-API] HTTP inaktiv (MC_API_KEY fehlt?). "
                "Webhook-Bridge funktioniert trotzdem, wenn die Mod einen Webhook nutzt."
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Nimmt Events von der Fabric-Mod per Discord-Webhook entgegen."""
        if not message.webhook_id:
            return
        text = (message.content or "").strip()
        if not text.startswith("MC_"):
            return
        key = (config.MC_API_KEY or "").strip()
        if not key:
            return

        m = _MC_LINK.match(text)
        if m:
            code, ign, got_key = m.group(1), m.group(2), m.group(3)
            if got_key != key:
                print("[MC-Webhook] LINK abgelehnt — apiKey falsch")
                return
            self.api._touch_watcher("webhook_link")
            result = await handle_mc_link_redeem(self.bot, code=code, ign=ign)
            print(f"[MC-Webhook] LINK {code} / {ign} → {result}")
            return

        m = _MC_PAY.match(text)
        if m:
            ign, amount_s, got_key, raw = (
                m.group(1),
                m.group(2),
                m.group(3),
                (m.group(4) or ""),
            )
            if got_key != key:
                print("[MC-Webhook] PAY abgelehnt — apiKey falsch")
                return
            guild_id = int(config.GUILD_ID or 0)
            if message.guild:
                guild_id = message.guild.id
            if not guild_id:
                return
            self.api._touch_watcher("webhook_payment")
            result = await handle_mc_payment(
                self.bot,
                guild_id=guild_id,
                ign=ign,
                amount=float(amount_s),
                raw_text=raw[:500],
            )
            print(f"[MC-Webhook] PAY {ign} {amount_s} → {result}")
            return

        m = _MC_HB.match(text)
        if m:
            if m.group(1) != key:
                return
            self.api._touch_watcher("webhook_heartbeat")
            return

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
