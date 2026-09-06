from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import error_embed, success_embed, warn_embed
from utils.username_sniper import (
    MAX_LENGTH_CANDIDATES,
    MAX_NAMES_PER_RUN,
    PLATFORMS,
    check_many,
    format_results_embed_body,
    generate_candidates,
)
from views.snipe_panel import post_or_refresh_snipe_panel

if TYPE_CHECKING:
    from bot import ShopBot


PlatformChoice = app_commands.Choice[str]


def _platform_choices() -> list[PlatformChoice]:
    return [
        app_commands.Choice(name="Minecraft", value="minecraft"),
        app_commands.Choice(name="Roblox", value="roblox"),
        app_commands.Choice(name="Discord", value="discord"),
    ]


class UsernameSniperCog(commands.Cog):
    """Minecraft / Discord / Roblox Username-Verfügbarkeit."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    snipe = app_commands.Group(
        name="snipe",
        description="Username Sniper (Minecraft / Discord / Roblox)",
    )

    @app_commands.command(
        name="snipepanel",
        description="Username-Sniper Panel posten oder aktualisieren (Staff)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def snipepanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed("Kein Channel", "Bitte einen Text-Channel wählen."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        msg = await post_or_refresh_snipe_panel(
            self.bot, interaction.guild, target
        )
        await interaction.followup.send(
            embed=success_embed(
                "Sniper-Panel",
                f"Panel in {target.mention}: {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @snipe.command(
        name="check",
        description="Bestimmte Usernames prüfen — zeigt nur bestätigte freie",
    )
    @app_commands.describe(
        platform="Plattform",
        usernames="Ein oder mehrere Namen (Leerzeichen/Komma getrennt)",
        details="Auch vergebene/unklare Treffer anzeigen",
    )
    @app_commands.choices(platform=_platform_choices())
    async def snipe_check(
        self,
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        usernames: str,
        details: bool = False,
    ) -> None:
        plat = platform.value
        parts = [
            p.strip()
            for chunk in usernames.replace(",", " ").split()
            for p in [chunk]
            if p.strip()
        ]
        if not parts:
            await interaction.response.send_message(
                embed=error_embed("Keine Namen", "Bitte mindestens einen Username."),
                ephemeral=True,
            )
            return
        if len(parts) > MAX_NAMES_PER_RUN:
            await interaction.response.send_message(
                embed=error_embed(
                    "Zu viele Namen",
                    f"Max. **{MAX_NAMES_PER_RUN}** pro Lauf.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        status = await interaction.followup.send(
            embed=warn_embed(
                "🎯 Sniper läuft…",
                f"{PLATFORMS[plat].emoji} **{PLATFORMS[plat].label}** — "
                f"{len(parts)} Name(n) werden geprüft…",
            ),
            ephemeral=True,
        )
        results = await check_many(plat, parts)
        body, free = format_results_embed_body(
            plat, results, show_all=details
        )
        if free:
            embed = success_embed(
                f"✅ {len(free)} verfügbar — {PLATFORMS[plat].label}",
                body,
            )
        else:
            embed = warn_embed(
                f"Keine freien Treffer — {PLATFORMS[plat].label}",
                body,
            )
        await status.edit(embed=embed)

    @snipe.command(
        name="length",
        description="Zufällige Namen einer Länge prüfen — nur bestätigte freie",
    )
    @app_commands.describe(
        platform="Plattform",
        length="Exakte Username-Länge",
        count="Wie viele Kandidaten prüfen (max. 20)",
        prefix="Optionaler Prefix",
        suffix="Optionaler Suffix",
        details="Auch vergebene/unklare Treffer anzeigen",
    )
    @app_commands.choices(platform=_platform_choices())
    async def snipe_length(
        self,
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        length: app_commands.Range[int, 2, 32],
        count: app_commands.Range[int, 1, 20] = 10,
        prefix: str = "",
        suffix: str = "",
        details: bool = False,
    ) -> None:
        plat = platform.value
        spec = PLATFORMS[plat]
        try:
            names = generate_candidates(
                plat,
                int(length),
                count=int(count),
                prefix=(prefix or "").strip(),
                suffix=(suffix or "").strip(),
            )
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültige Parameter", str(e)),
                ephemeral=True,
            )
            return

        if not names:
            await interaction.response.send_message(
                embed=error_embed(
                    "Keine Kandidaten",
                    "Mit diesen Regeln konnten keine gültigen Namen erzeugt werden.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        status = await interaction.followup.send(
            embed=warn_embed(
                "🎯 Length-Sniper läuft…",
                f"{spec.emoji} **{spec.label}** · Länge **{length}** · "
                f"{len(names)} Kandidaten (max {MAX_LENGTH_CANDIDATES})\n"
                "_Nur API-bestätigte Freie werden als verfügbar gelistet._",
            ),
            ephemeral=True,
        )
        results = await check_many(plat, names)
        body, free = format_results_embed_body(
            plat, results, show_all=details
        )
        title = (
            f"✅ {len(free)} verfügbar — {spec.label} (len {length})"
            if free
            else f"Keine freien Treffer — {spec.label} (len {length})"
        )
        embed = (success_embed if free else warn_embed)(title, body)
        await status.edit(embed=embed)


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(UsernameSniperCog(bot))
