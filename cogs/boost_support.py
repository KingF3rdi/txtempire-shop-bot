"""
boost_support.py
=================

Verwaltung der gemeinsamen Support-Rolle + Einzelmitglieder für Account-Buy
(cogs/account_shop.py) und Tier-Buy (cogs/tier_boost.py). Beide Shops nutzen
dieselbe Rolle/Liste — hier zentral konfiguriert.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils import boost_support
from utils.embeds import base_embed, error_embed, success_embed


class BoostSupportCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    boost_group = app_commands.Group(
        name="boostsupport",
        description="Support-Rolle/Mitglieder für Account-Buy & Tier-Buy verwalten",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @boost_group.command(name="rolle", description="Support-Rolle für Account-Buy & Tier-Buy setzen")
    @app_commands.describe(rolle="Rolle, die in beiden Ticket-Arten gepingt wird und Zugriff bekommt")
    async def rolle(self, interaction: discord.Interaction, rolle: discord.Role) -> None:
        assert interaction.guild is not None
        await boost_support.set_staff_role_id(self.bot, interaction.guild.id, rolle.id)
        await interaction.response.send_message(
            embed=success_embed(
                "Gespeichert",
                f"Support-Rolle für Account-Buy & Tier-Buy ist jetzt {rolle.mention}.",
            ),
            ephemeral=True,
        )

    @boost_group.command(name="member-hinzufuegen", description="Einzelnes Mitglied ohne Rolle als Support hinzufügen")
    @app_commands.describe(user="Mitglied, das Zugriff auf Account/Tier-Tickets bekommt (ohne die Rolle tragen zu müssen)")
    async def member_add(self, interaction: discord.Interaction, user: discord.Member) -> None:
        assert interaction.guild is not None
        await boost_support.add_extra_staff(self.bot, interaction.guild.id, user.id)
        await interaction.response.send_message(
            embed=success_embed("Gespeichert", f"{user.mention} ist jetzt Support für Account-Buy & Tier-Buy."),
            ephemeral=True,
        )

    @boost_group.command(name="member-entfernen", description="Einzelnes Support-Mitglied wieder entfernen")
    @app_commands.describe(user="Mitglied, das wieder entfernt werden soll")
    async def member_remove(self, interaction: discord.Interaction, user: discord.Member) -> None:
        assert interaction.guild is not None
        await boost_support.remove_extra_staff(self.bot, interaction.guild.id, user.id)
        await interaction.response.send_message(
            embed=success_embed("Entfernt", f"{user.mention} ist kein Einzel-Support mehr."),
            ephemeral=True,
        )

    @boost_group.command(name="anzeigen", description="Aktuelle Support-Rolle & Mitglieder anzeigen")
    async def anzeigen(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        guild_id = interaction.guild.id
        role_id = await boost_support.get_staff_role_id(self.bot, guild_id)
        role = interaction.guild.get_role(role_id) if role_id else None
        extra_ids = await boost_support.get_extra_staff_ids(self.bot, guild_id)
        extras = "\n".join(f"• <@{uid}>" for uid in extra_ids) or "_keine_"
        await interaction.response.send_message(
            embed=base_embed(
                "Boost-Support",
                f"**Rolle:** {role.mention if role else '_keine gesetzt_'}\n\n"
                f"**Einzelmitglieder ohne Rolle:**\n{extras}",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await boost_support.ensure_table(bot)  # type: ignore[arg-type]
    await bot.add_cog(BoostSupportCog(bot))
