from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from utils.username_sniper import (
    MAX_LENGTH_CANDIDATES,
    PLATFORMS,
    check_many,
    format_results_embed_body,
    generate_candidates,
)

if TYPE_CHECKING:
    from bot import ShopBot


def build_snipe_panel_embed() -> discord.Embed:
    embed = base_embed(
        "🎯 Username Sniper",
        "Finde **bestätigt freie** Usernames auf:\n"
        "🟩 Minecraft · 🟥 Roblox · 🟦 Discord\n\n"
        "**Name prüfen** — bestimmte Usernames checken\n"
        "**Nach Länge** — zufällige Namen einer Länge scannen\n\n"
        "Ergebnis zeigt **nur API-bestätigte verfügbare** Namen "
        "(Unklar/Rate-Limit zählt nicht als frei).\n\n"
        "Auch: `/snipe check` · `/snipe length`",
    )
    embed.set_footer(text="TxtEmpire Sniper · Nur bestätigte FREE-Treffer")
    return embed


class SnipeCheckModal(discord.ui.Modal, title="Username prüfen"):
    usernames = discord.ui.TextInput(
        label="Username(s)",
        placeholder="name1, name2, name3",
        style=discord.TextStyle.paragraph,
        max_length=400,
        required=True,
    )

    def __init__(self, bot: ShopBot, platform: str) -> None:
        super().__init__()
        self.bot = bot
        self.platform = platform

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parts = [
            p.strip()
            for chunk in str(self.usernames.value).replace(",", " ").split()
            for p in [chunk]
            if p.strip()
        ]
        if not parts:
            await interaction.response.send_message(
                embed=error_embed("Keine Namen"), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        spec = PLATFORMS[self.platform]
        status = await interaction.followup.send(
            embed=warn_embed(
                "🎯 Sniper läuft…",
                f"{spec.emoji} **{spec.label}** — {len(parts)} Name(n)…",
            ),
            ephemeral=True,
        )
        results = await check_many(self.platform, parts)
        body, free = format_results_embed_body(self.platform, results)
        embed = (
            success_embed(f"✅ {len(free)} verfügbar — {spec.label}", body)
            if free
            else warn_embed(f"Keine freien Treffer — {spec.label}", body)
        )
        await status.edit(embed=embed)


class SnipeLengthModal(discord.ui.Modal, title="Nach Länge snipen"):
    length = discord.ui.TextInput(
        label="Länge",
        placeholder="z.B. 4",
        max_length=2,
        required=True,
    )
    count = discord.ui.TextInput(
        label="Anzahl Kandidaten",
        placeholder=f"1–{MAX_LENGTH_CANDIDATES} (Standard 10)",
        max_length=2,
        required=False,
    )
    prefix = discord.ui.TextInput(
        label="Prefix (optional)",
        required=False,
        max_length=16,
    )
    suffix = discord.ui.TextInput(
        label="Suffix (optional)",
        required=False,
        max_length=16,
    )

    def __init__(self, bot: ShopBot, platform: str) -> None:
        super().__init__()
        self.bot = bot
        self.platform = platform

    async def on_submit(self, interaction: discord.Interaction) -> None:
        spec = PLATFORMS[self.platform]
        try:
            length = int(str(self.length.value).strip())
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Länge muss eine Zahl sein"), ephemeral=True
            )
            return
        count_raw = str(self.count.value or "").strip()
        try:
            count = int(count_raw) if count_raw else 10
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Anzahl muss eine Zahl sein"), ephemeral=True
            )
            return
        count = max(1, min(count, MAX_LENGTH_CANDIDATES))
        prefix = str(self.prefix.value or "").strip()
        suffix = str(self.suffix.value or "").strip()

        try:
            names = generate_candidates(
                self.platform,
                length,
                count=count,
                prefix=prefix,
                suffix=suffix,
            )
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültig", str(e)), ephemeral=True
            )
            return

        if not names:
            await interaction.response.send_message(
                embed=error_embed("Keine gültigen Kandidaten"), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        status = await interaction.followup.send(
            embed=warn_embed(
                "🎯 Length-Sniper läuft…",
                f"{spec.emoji} **{spec.label}** · len **{length}** · "
                f"{len(names)} Kandidaten…",
            ),
            ephemeral=True,
        )
        results = await check_many(self.platform, names)
        body, free = format_results_embed_body(self.platform, results)
        embed = (
            success_embed(
                f"✅ {len(free)} verfügbar — {spec.label} (len {length})",
                body,
            )
            if free
            else warn_embed(
                f"Keine freien Treffer — {spec.label} (len {length})",
                body,
            )
        )
        await status.edit(embed=embed)


class PlatformPickView(discord.ui.View):
    """Kurzlebige Plattform-Auswahl vor Modal."""

    def __init__(self, bot: ShopBot, *, mode: str) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.mode = mode  # check | length

    async def _open(
        self, interaction: discord.Interaction, platform: str
    ) -> None:
        if self.mode == "check":
            await interaction.response.send_modal(
                SnipeCheckModal(self.bot, platform)
            )
        else:
            await interaction.response.send_modal(
                SnipeLengthModal(self.bot, platform)
            )

    @discord.ui.button(label="Minecraft", style=discord.ButtonStyle.success, emoji="🟩")
    async def mc(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, "minecraft")

    @discord.ui.button(label="Roblox", style=discord.ButtonStyle.danger, emoji="🟥")
    async def rbx(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, "roblox")

    @discord.ui.button(label="Discord", style=discord.ButtonStyle.primary, emoji="🟦")
    async def dc(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, "discord")


class SnipePanelView(discord.ui.View):
    """Persistentes Sniper-Panel."""

    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Name prüfen",
        style=discord.ButtonStyle.success,
        custom_id="snipepanel:check",
        emoji="🔎",
        row=0,
    )
    async def check_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            embed=base_embed(
                "Plattform wählen",
                "Für welchen Dienst soll geprüft werden?",
            ),
            view=PlatformPickView(self.bot, mode="check"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Nach Länge",
        style=discord.ButtonStyle.primary,
        custom_id="snipepanel:length",
        emoji="📏",
        row=0,
    )
    async def length_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            embed=base_embed(
                "Plattform wählen",
                "Zufällige Namen welcher Plattform snipen?",
            ),
            view=PlatformPickView(self.bot, mode="length"),
            ephemeral=True,
        )


async def post_or_refresh_snipe_panel(
    bot: ShopBot,
    guild: discord.Guild,
    channel: discord.TextChannel,
    *,
    force_new: bool = False,
) -> discord.Message:
    embed = build_snipe_panel_embed()
    view = SnipePanelView(bot)
    row = await bot.db.get_snipe_panel(guild.id)

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
    await bot.db.set_snipe_panel(
        guild.id, channel_id=channel.id, message_id=msg.id
    )
    return msg
