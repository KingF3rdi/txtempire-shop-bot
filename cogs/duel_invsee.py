"""
duel_invsee.py
==============

Discord-seitiger Kauf für "Duel Invsee": ein Spieler kann sich das Inventar
eines ANDEREN Spielers live auf der Website anzeigen lassen — aber nur, wenn
dieser andere Spieler das selbst erlaubt hat (Ingame-Mod `/duelinvsee on`
oder hier `/duelinvsee-optin`). Ohne dieses Opt-in lässt sich hier gar
nichts kaufen.

Kaufwege (beide rufen denselben `_purchase` auf):
  - Slash-Befehl `/duelinvsee gegner:<IGN>`
  - Panel-Button (`/duelinvseepanel` postet ihn) -> Modal fragt nach dem
    Gegner-Namen

Ablauf danach:
  1. Käufer zahlt Credits, bekommt einen Live-Link zur Website.
  2. Der Mod des Ziel-Spielers fragt alle 10s per Heartbeat beim Bot nach,
     ob gerade jemand zusieht, und pusht bei Bedarf sein eigenes Inventar
     direkt an die Website. Ohne laufenden Mod passiert das nicht — Opt-in
     allein liefert keine Inventardaten, es erlaubt nur den Kauf.
"""
from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.duel_invsee_store import create_watch, ensure_tables, is_opted_in, set_opt_in
from utils.embeds import base_embed, error_embed, format_price, success_embed

IGN_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


async def _purchase(bot: commands.Bot, interaction: discord.Interaction, gegner: str) -> None:
    """Gemeinsame Kauflogik für Slash-Befehl und Panel-Button/Modal."""
    assert interaction.guild is not None
    guild_id = interaction.guild.id
    ign = gegner.strip()

    if not IGN_RE.match(ign):
        await interaction.response.send_message(
            embed=error_embed("Ungültiger Name", "Minecraft-Namen sind 3–16 Zeichen (Buchstaben/Zahlen/_)."),
            ephemeral=True,
        )
        return

    if not await is_opted_in(bot, guild_id, ign):
        await interaction.response.send_message(
            embed=error_embed(
                "Nicht verfügbar",
                f"**{ign}** hat Duel Invsee nicht aktiviert (`/duelinvsee on` im Spiel oder "
                "`/duelinvsee-optin`). Ohne diese Zustimmung kann sein Inventar nicht gekauft werden.",
            ),
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    ok = await bot.db.try_deduct_credits(guild_id, interaction.user.id, config.DUEL_INVSEE_PRICE)  # type: ignore[attr-defined]
    if not ok:
        balance = await bot.db.get_credits(guild_id, interaction.user.id)  # type: ignore[attr-defined]
        await interaction.followup.send(
            embed=error_embed(
                "Nicht genug Credits",
                f"Duel Invsee kostet **{format_price(config.DUEL_INVSEE_PRICE)}**. "
                f"Dein Guthaben: **{format_price(balance)}**.",
            ),
            ephemeral=True,
        )
        return

    token = await create_watch(bot, guild_id, interaction.user.id, ign, config.DUEL_INVSEE_WATCH_MINUTES)  # type: ignore[arg-type]
    url = f"{config.DUEL_INVSEE_VIEW_URL}?duelinvsee={token}"
    await interaction.followup.send(
        embed=success_embed(
            "Duel Invsee aktiv",
            f"Live-Inventar von **{ign}**: {url}\n\n"
            f"Aktualisiert alle 10s, sobald **{ign}** online ist und sein Mod sich meldet. "
            f"Gültig für **{config.DUEL_INVSEE_WATCH_MINUTES} Minuten**.",
        ),
        ephemeral=True,
    )


class DuelInvseeBuyModal(discord.ui.Modal, title="Duel Invsee kaufen"):
    gegner = discord.ui.TextInput(
        label="Minecraft-Name des Gegners", max_length=16, min_length=3, required=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _purchase(self.bot, interaction, str(self.gegner.value))


class DuelInvseePanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Invsee kaufen", style=discord.ButtonStyle.primary, custom_id="duelinvsee:buy", emoji="🗡️",
    )
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await interaction.response.send_modal(DuelInvseeBuyModal(self.bot))


def _panel_embed() -> discord.Embed:
    return base_embed(
        "🗡️ Duel Invsee",
        f"Kaufe eine Live-Ansicht des Inventars eines Gegners — **{format_price(config.DUEL_INVSEE_PRICE)}**.\n\n"
        "Funktioniert nur, wenn der Gegner selbst zugestimmt hat "
        "(`/duelinvsee on` im Spiel oder `/duelinvsee-optin` hier im Discord) "
        "und sein Ingame-Mod läuft — Inventar wird alle 10s live aktualisiert und auf der Website angezeigt.\n\n"
        "Klicke unten und gib den Minecraft-Namen des Gegners ein.",
    )


class DuelInvseeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="duelinvsee",
        description=f"Live-Inventar eines Gegners kaufen ({format_price(config.DUEL_INVSEE_PRICE)}) — nur wenn er zugestimmt hat",
    )
    @app_commands.describe(gegner="Minecraft-Name des Gegners (muss selbst zugestimmt haben)")
    async def duelinvsee(self, interaction: discord.Interaction, gegner: str) -> None:
        await _purchase(self.bot, interaction, gegner)

    @app_commands.command(
        name="duelinvseepanel", description="Duel-Invsee-Kaufpanel posten (Staff)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def duelinvseepanel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await target.send(embed=_panel_embed(), view=DuelInvseePanelView(self.bot))
        await interaction.followup.send(
            embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True,
        )

    @app_commands.command(
        name="duelinvsee-optin",
        description="Erlaube oder verbiete anderen, dein Inventar per Duel Invsee zu kaufen",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="An — andere können mein Inventar kaufen", value="an"),
            app_commands.Choice(name="Aus", value="aus"),
        ]
    )
    async def duelinvsee_optin_cmd(
        self, interaction: discord.Interaction, status: app_commands.Choice[str]
    ) -> None:
        assert interaction.guild is not None
        guild_id = interaction.guild.id

        link = await self.bot.db.get_mc_link(guild_id, interaction.user.id)  # type: ignore[attr-defined]
        ign = str(link["ign"]) if link and link.get("ign") else None
        if not ign:
            await interaction.response.send_message(
                embed=error_embed(
                    "Kein Minecraft-Account verknüpft",
                    "Verknüpfe zuerst deinen Account mit `/link`, damit wir wissen, "
                    "welcher Minecraft-Name dir gehört.",
                ),
                ephemeral=True,
            )
            return

        enabled = status.value == "an"
        await set_opt_in(self.bot, guild_id, ign, enabled)
        await interaction.response.send_message(
            embed=success_embed(
                "Gespeichert",
                (
                    f"Duel Invsee für **{ign}** ist jetzt **an** — andere können dein Inventar "
                    f"per `/duelinvsee` oder dem Panel-Button kaufen, solange dein Ingame-Mod läuft "
                    f"und du das nicht wieder ausschaltest."
                    if enabled
                    else f"Duel Invsee für **{ign}** ist jetzt **aus**."
                ),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await ensure_tables(bot)  # type: ignore[arg-type]
    await bot.add_cog(DuelInvseeCog(bot))
