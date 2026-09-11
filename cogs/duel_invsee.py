"""
duel_invsee.py
==============

Discord-seitiger Kauf für "Duel Invsee": ein Spieler kann sich das Inventar
eines ANDEREN Spielers live auf der Website anzeigen lassen — aber nur, wenn
dieser andere Spieler das über den Ingame-Mod selbst erlaubt hat
(`/duelinvsee on`, siehe minecraft-mod/). Ohne dieses Opt-in lässt sich hier
gar nichts kaufen.

Ablauf:
  1. Ziel-Spieler aktiviert im Mod `/duelinvsee on` -> meldet sich am
     Bot-API-Endpunkt `/mc/v1/duelinvsee/optin` (integrations/mc_api.py).
  2. Käufer nutzt hier `/duelinvsee gegner:<IGN>`, zahlt Credits, bekommt
     einen Live-Link zur Website.
  3. Der Mod des Ziel-Spielers fragt alle 10s per Heartbeat beim Bot nach,
     ob gerade jemand zusieht, und pusht bei Bedarf sein eigenes Inventar
     direkt an die Website.
"""
from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.duel_invsee_store import create_watch, ensure_tables, is_opted_in
from utils.embeds import error_embed, format_price, success_embed

IGN_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


class DuelInvseeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="duelinvsee",
        description=f"Live-Inventar eines Gegners kaufen ({format_price(config.DUEL_INVSEE_PRICE)}) — nur wenn er zugestimmt hat",
    )
    @app_commands.describe(gegner="Minecraft-Name des Gegners (muss selbst /duelinvsee on aktiviert haben)")
    async def duelinvsee(self, interaction: discord.Interaction, gegner: str) -> None:
        assert interaction.guild is not None
        guild_id = interaction.guild.id
        ign = gegner.strip()

        if not IGN_RE.match(ign):
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Name", "Minecraft-Namen sind 3–16 Zeichen (Buchstaben/Zahlen/_)."),
                ephemeral=True,
            )
            return

        if not await is_opted_in(self.bot, guild_id, ign):
            await interaction.response.send_message(
                embed=error_embed(
                    "Nicht verfügbar",
                    f"**{ign}** hat Duel Invsee nicht aktiviert (`/duelinvsee on` im Spiel). "
                    "Ohne diese Zustimmung kann sein Inventar nicht gekauft werden.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        ok = await self.bot.db.try_deduct_credits(guild_id, interaction.user.id, config.DUEL_INVSEE_PRICE)
        if not ok:
            balance = await self.bot.db.get_credits(guild_id, interaction.user.id)
            await interaction.followup.send(
                embed=error_embed(
                    "Nicht genug Credits",
                    f"Duel Invsee kostet **{format_price(config.DUEL_INVSEE_PRICE)}**. "
                    f"Dein Guthaben: **{format_price(balance)}**.",
                ),
                ephemeral=True,
            )
            return

        token = await create_watch(
            self.bot, guild_id, interaction.user.id, ign, config.DUEL_INVSEE_WATCH_MINUTES
        )
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


async def setup(bot: commands.Bot) -> None:
    await ensure_tables(bot)  # type: ignore[arg-type]
    await bot.add_cog(DuelInvseeCog(bot))
