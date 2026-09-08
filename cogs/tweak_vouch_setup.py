"""
tweak_vouch_setup.py
======================

Staff-Command zum Einrichten des gemeinsamen Vouch-Kanals für alle
Tweak-Produkte (Ferdi Mousetweaks, y3zz GPU Tweaks, ...). Die eigentliche
DM-Einladung nach Kauf-Bestätigung sitzt in utils/tweak_vouch.py und wird
von cogs/mousetweaks_keys.py und cogs/gputweaks_keys.py aufgerufen.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils import tweak_vouch
from utils.embeds import success_embed

if TYPE_CHECKING:
    from bot import ShopBot


class TweakVouchSetupCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="tweakvouchsetup",
        description="Gemeinsamen Vouch-Kanal für Mousetweaks/GPU-Tweaks setzen (Staff)",
    )
    @app_commands.describe(channel="Kanal, in dem Kunden Tweak-Vouches posten sollen")
    @app_commands.default_permissions(manage_guild=True)
    async def tweakvouchsetup(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        assert interaction.guild is not None
        await tweak_vouch.set_channel_id(self.bot, interaction.guild.id, channel.id)
        await interaction.response.send_message(
            embed=success_embed(
                "Tweak-Vouch-Kanal gesetzt",
                f"Kunden werden nach jedem bestätigten Mousetweaks-/GPU-Tweaks-Kauf "
                f"per DM eingeladen, in {channel.mention} zu vouchen.",
            ),
            ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await bot.add_cog(TweakVouchSetupCog(bot))
