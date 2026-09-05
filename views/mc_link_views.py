"""Discord UI: Minecraft-Account verlinken / unverifizieren."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord

import config
from utils.embeds import base_embed, error_embed, success_embed, warn_embed

if TYPE_CHECKING:
    from bot import ShopBot

IGN_MIN, IGN_MAX = 3, 16
MC_LINK_PANEL_TYPE = "mc_link"


def _valid_ign(ign: str) -> bool:
    s = ign.strip()
    if not (IGN_MIN <= len(s) <= IGN_MAX):
        return False
    return all(c.isalnum() or c == "_" for c in s)


def _make_code() -> str:
    return f"TXTE-{secrets.token_hex(3).upper()}"


async def build_mc_link_panel_embed(bot: ShopBot, guild_id: int) -> discord.Embed:
    ttl = int(config.MC_LINK_CODE_TTL_MINUTES)
    return base_embed(
        "Minecraft Account verknüpfen",
        "Verlinke deinen **Minecraft-IGN** mit Discord.\n"
        "Nach korrekter Ingame-Zahlung wird dein Kauf-Ticket "
        "**automatisch bestätigt** — ohne Screenshot.\n\n"
        f"1. **Account verlinken** → IGN eingeben\n"
        f"2. Code erhalten (gültig **{ttl} Min.**)\n"
        f"3. Ingame in den Chat (sichtbar für den Shop-Bot):\n"
        f"   `!link DEIN-CODE`\n"
        f"4. Fertig — Status siehst du hier oder per DM\n\n"
        "Mit **Unverifizieren** kannst du die Verknüpfung jederzeit lösen.",
    ).set_footer(text="Nur dein eigener Account · Ein IGN = ein Discord")


class LinkIgnModal(discord.ui.Modal, title="Minecraft IGN eingeben"):
    ign = discord.ui.TextInput(
        label="Ingame-Name (IGN)",
        placeholder="Steve",
        min_length=IGN_MIN,
        max_length=IGN_MAX,
        required=True,
    )

    def __init__(self, bot: ShopBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ign = str(self.ign.value or "").strip()
        if not _valid_ign(ign):
            await interaction.response.send_message(
                embed=error_embed(
                    "Ungültiger IGN",
                    "3–16 Zeichen, nur Buchstaben, Zahlen und `_`.",
                ),
                ephemeral=True,
            )
            return

        existing = await self.bot.db.get_mc_link_by_ign(self.guild_id, ign)
        if existing and int(existing["user_id"]) != interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed(
                    "IGN belegt",
                    f"**{ign}** ist bereits mit einem anderen Discord-Account "
                    "verknüpft.",
                ),
                ephemeral=True,
            )
            return

        my_link = await self.bot.db.get_mc_link(self.guild_id, interaction.user.id)
        if my_link and str(my_link.get("ign") or "").lower() == ign.lower():
            await interaction.response.send_message(
                embed=warn_embed(
                    "Bereits verknüpft",
                    f"Dein Account ist schon mit **{my_link['ign']}** verknüpft.\n"
                    "Nutze **Unverifizieren**, wenn du wechseln willst.",
                ),
                ephemeral=True,
            )
            return

        code = _make_code()
        expires = datetime.now(timezone.utc) + timedelta(
            minutes=int(config.MC_LINK_CODE_TTL_MINUTES)
        )
        expires_at = expires.strftime("%Y-%m-%d %H:%M:%S")
        await self.bot.db.create_mc_link_code(
            self.guild_id,
            interaction.user.id,
            ign,
            code=code,
            expires_at=expires_at,
        )

        embed = success_embed(
            "Link-Code erstellt",
            f"IGN: **{ign}**\n"
            f"Code: `{code}`\n"
            f"Gültig bis: `{expires_at}` UTC\n\n"
            f"**Ingame schreiben:**\n```\n!link {code}\n```\n"
            "Der Shop-Bot erkennt den Code und verknüpft deinen Discord.\n"
            "Danach werden passende Zahlungen **auto-bestätigt**.",
        )
        if my_link:
            embed.add_field(
                name="Hinweis",
                value=(
                    f"Aktuell verknüpft: **{my_link['ign']}**. "
                    "Nach erfolgreichem neuem Link wird der IGN aktualisiert."
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class McLinkPanelView(discord.ui.View):
    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Account verlinken",
        style=discord.ButtonStyle.primary,
        custom_id="mc:link",
        emoji="🔗",
    )
    async def link_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        await interaction.response.send_modal(
            LinkIgnModal(self.bot, interaction.guild.id)
        )

    @discord.ui.button(
        label="Status",
        style=discord.ButtonStyle.secondary,
        custom_id="mc:status",
        emoji="ℹ️",
    )
    async def status_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
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
            embed = success_embed(
                "Account-Status",
                f"Verknüpft mit **{link['ign']}**\n"
                f"Seit: `{link.get('linked_at') or '—'}`\n\n"
                "Offene Tickets mit passendem Betrag werden automatisch bestätigt.",
            )
        else:
            embed = warn_embed(
                "Nicht verknüpft",
                "Du hast noch keinen Minecraft-Account verknüpft.\n"
                "Klicke **Account verlinken**.",
            )
        if pending:
            embed.add_field(
                name="Offener Code",
                value=(
                    f"IGN **{pending['ign']}** · Code `{pending['code']}`\n"
                    f"Gültig bis `{pending['expires_at']}`\n"
                    f"Ingame: `!link {pending['code']}`"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Unverifizieren",
        style=discord.ButtonStyle.danger,
        custom_id="mc:unlink",
        emoji="🔓",
    )
    async def unlink_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
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
                f"Die Verknüpfung mit **{link['ign']}** wurde entfernt.\n"
                "Auto-Confirm ist damit deaktiviert, bis du erneut verlinkst.",
            ),
            ephemeral=True,
        )


async def post_or_refresh_mc_link_panel(
    bot: ShopBot,
    channel: discord.TextChannel,
    *,
    force_new: bool = False,
) -> discord.Message:
    embed = await build_mc_link_panel_embed(bot, channel.guild.id)
    view = McLinkPanelView(bot)
    row = await bot.db.get_service_panel(channel.guild.id, MC_LINK_PANEL_TYPE)
    if (
        not force_new
        and row
        and row.get("channel_id")
        and row.get("message_id")
        and int(row["channel_id"]) == channel.id
    ):
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            await msg.edit(embed=embed, view=view)
            return msg
        except (discord.NotFound, discord.HTTPException):
            pass

    msg = await channel.send(embed=embed, view=view)
    await bot.db.set_service_panel(
        channel.guild.id,
        MC_LINK_PANEL_TYPE,
        channel_id=channel.id,
        message_id=msg.id,
    )
    return msg


async def refresh_mc_link_panel_on_ready(bot: ShopBot) -> list[str]:
    lines: list[str] = []
    for row in await bot.db.list_service_panels():
        if str(row.get("panel_type")) != MC_LINK_PANEL_TYPE:
            continue
        guild = bot.get_guild(int(row["guild_id"]))
        if guild is None:
            continue
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            await msg.edit(
                embed=await build_mc_link_panel_embed(bot, guild.id),
                view=McLinkPanelView(bot),
            )
            lines.append(f"MC-Link-Panel aktualisiert in {channel.mention}")
        except discord.NotFound:
            lines.append("MC-Link-Panel Nachricht fehlt — kein Auto-Post")
        except discord.HTTPException as e:
            lines.append(f"MC-Link-Panel Edit fehlgeschlagen: {e}")
    return lines
