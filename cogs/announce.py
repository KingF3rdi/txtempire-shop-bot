from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import base_embed, error_embed, success_embed
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


class AnnounceTextModal(discord.ui.Modal):
    content = discord.ui.TextInput(
        label="Text",
        style=discord.TextStyle.paragraph,
        placeholder="Schreib deinen Text…",
        max_length=3900,
        required=True,
    )

    def __init__(
        self,
        bot: ShopBot,
        *,
        title: str,
        channel: discord.TextChannel,
        embed_title: str,
    ) -> None:
        super().__init__(title=title)
        self.bot = bot
        self.channel = channel
        self.embed_title = embed_title

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.content.value).strip()
        if not text:
            await interaction.response.send_message(
                embed=error_embed("Leerer Text"), ephemeral=True
            )
            return
        embed = base_embed(self.embed_title, text)
        embed.set_author(
            name=str(interaction.user),
            icon_url=interaction.user.display_avatar.url,
        )
        try:
            msg = await self.channel.send(embed=embed)
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed("Senden fehlgeschlagen", str(e)[:400]),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=success_embed(
                "Gesendet",
                f"In {self.channel.mention}: {msg.jump_url}",
            ),
            ephemeral=True,
        )


class AnnounceCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    async def _resolve_channel(
        self,
        interaction: discord.Interaction,
        *,
        setting_key: str,
        label: str,
    ) -> discord.TextChannel | None:
        assert interaction.guild is not None
        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        ch_id = settings.get(setting_key)
        if not ch_id:
            await interaction.response.send_message(
                embed=error_embed(
                    f"{label}-Channel fehlt",
                    f"Setze ihn mit `/setupchannels` "
                    f"(Feld **{label}**).",
                ),
                ephemeral=True,
            )
            return None
        channel = interaction.guild.get_channel(int(ch_id))
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed(
                    "Channel ungültig",
                    f"{label}-Channel nicht gefunden — `/setupchannels` neu setzen.",
                ),
                ephemeral=True,
            )
            return None
        return channel

    @app_commands.command(
        name="changelog",
        description="Changelog per Textfenster in den Changelog-Channel senden",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def changelog(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        channel = await self._resolve_channel(
            interaction, setting_key="changelog_channel_id", label="Changelog"
        )
        if channel is None:
            return
        await interaction.response.send_modal(
            AnnounceTextModal(
                self.bot,
                title="Changelog schreiben",
                channel=channel,
                embed_title="📋 Changelog",
            )
        )

    @app_commands.command(
        name="msg",
        description="Nachricht per Textfenster in den Msg-Channel senden",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def msg(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        channel = await self._resolve_channel(
            interaction, setting_key="msg_channel_id", label="Msg"
        )
        if channel is None:
            return
        await interaction.response.send_modal(
            AnnounceTextModal(
                self.bot,
                title="Nachricht schreiben",
                channel=channel,
                embed_title="📢 Nachricht",
            )
        )

    @app_commands.command(
        name="setupchannels",
        description="Scan-Log, Changelog, Msg-Channel und Scan-Premium-Rolle setzen",
    )
    @app_commands.describe(
        scan_log="Channel für erfolgreiche / abgeschlossene Scans",
        changelog="Channel für /changelog",
        msg="Channel für /msg",
        scan_premium_role="Rolle bei Scan Premium (wird bei Ablauf entfernt)",
    )
    @app_commands.default_permissions(administrator=True)
    async def setupchannels(
        self,
        interaction: discord.Interaction,
        scan_log: discord.TextChannel | None = None,
        changelog: discord.TextChannel | None = None,
        msg: discord.TextChannel | None = None,
        scan_premium_role: discord.Role | None = None,
    ) -> None:
        assert interaction.guild is not None
        fields: dict = {}
        lines: list[str] = []
        if scan_log is not None:
            fields["scan_log_channel_id"] = scan_log.id
            lines.append(f"**Scan-Log:** {scan_log.mention}")
        if changelog is not None:
            fields["changelog_channel_id"] = changelog.id
            lines.append(f"**Changelog:** {changelog.mention}")
        if msg is not None:
            fields["msg_channel_id"] = msg.id
            lines.append(f"**Msg:** {msg.mention}")
        if scan_premium_role is not None:
            fields["scan_premium_role_id"] = scan_premium_role.id
            lines.append(f"**Scan-Premium-Rolle:** {scan_premium_role.mention}")
        if not fields:
            await interaction.response.send_message(
                embed=error_embed(
                    "Nichts gesetzt",
                    "Mindestens einen Channel oder die Premium-Rolle angeben.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild_settings(interaction.guild.id, **fields)
        await interaction.response.send_message(
            embed=success_embed("Channels / Rolle gespeichert", "\n".join(lines)),
            ephemeral=True,
        )

    @app_commands.command(
        name="volumeroles",
        description="Mengen-Rollen ab 10 / 15 / 20 Packs setzen",
    )
    @app_commands.describe(
        role_10="Rolle ab 10 Packs",
        role_15="Rolle ab 15 Packs",
        role_20="Rolle ab 20 Packs",
    )
    @app_commands.default_permissions(administrator=True)
    async def volumeroles(
        self,
        interaction: discord.Interaction,
        role_10: discord.Role | None = None,
        role_15: discord.Role | None = None,
        role_20: discord.Role | None = None,
    ) -> None:
        assert interaction.guild is not None
        fields: dict = {}
        lines: list[str] = []
        if role_10 is not None:
            fields["volume_role_10_id"] = role_10.id
            lines.append(f"**10+ Packs:** {role_10.mention}")
        if role_15 is not None:
            fields["volume_role_15_id"] = role_15.id
            lines.append(f"**15+ Packs:** {role_15.mention}")
        if role_20 is not None:
            fields["volume_role_20_id"] = role_20.id
            lines.append(f"**20+ Packs:** {role_20.mention}")
        if not fields:
            from utils.volume_discount import format_volume_tiers_help

            settings = await self.bot.db.ensure_guild(interaction.guild.id)
            cur = []
            for key, label in (
                ("volume_role_10_id", "10+"),
                ("volume_role_15_id", "15+"),
                ("volume_role_20_id", "20+"),
            ):
                rid = settings.get(key)
                role = interaction.guild.get_role(int(rid)) if rid else None
                cur.append(
                    f"**{label}:** {role.mention if role else '_nicht gesetzt_'}"
                )
            await interaction.response.send_message(
                embed=success_embed(
                    "Mengen-Rollen / Rabatt",
                    format_volume_tiers_help()
                    + "\n\n"
                    + "\n".join(cur)
                    + "\n\nZum Setzen: `/volumeroles role_10:@… role_15:@… role_20:@…`",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild_settings(interaction.guild.id, **fields)
        await interaction.response.send_message(
            embed=success_embed("Mengen-Rollen gespeichert", "\n".join(lines)),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(AnnounceCog(bot))
