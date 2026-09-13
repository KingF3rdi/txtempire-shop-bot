"""
autorole.py
===========

Automatische Rollenvergabe für neu beigetretene Mitglieder (Bots werden
übersprungen). Eigene Tabelle (autorole_settings) - keine Änderung an
db/database.py nötig.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import base_embed, error_embed, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


async def _ensure_table(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS autorole_settings (
            guild_id INTEGER PRIMARY KEY,
            role_id INTEGER,
            enabled INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    await bot.db.db.commit()


async def _get_settings(bot: "ShopBot", guild_id: int) -> dict:
    row = await bot.db.fetchone("SELECT * FROM autorole_settings WHERE guild_id = ?", (guild_id,))
    if row:
        return dict(row)
    return {"guild_id": guild_id, "role_id": None, "enabled": 0}


class AutoRoleCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    autorole_group = app_commands.Group(
        name="autorole", description="Automatische Rolle für neue Mitglieder verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @autorole_group.command(name="setzen", description="Rolle setzen und aktivieren, die neue Mitglieder automatisch bekommen")
    @app_commands.describe(rolle="Rolle für neue (menschliche) Mitglieder")
    async def setzen(self, interaction: discord.Interaction, rolle: discord.Role) -> None:
        assert interaction.guild is not None
        if rolle.managed or rolle >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                embed=error_embed(
                    "Rolle nicht vergebbar",
                    "Diese Rolle ist verwaltet oder steht über/gleich der höchsten Bot-Rolle — "
                    "der Bot kann sie niemandem geben.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.db.execute(
            """
            INSERT INTO autorole_settings (guild_id, role_id, enabled) VALUES (?, ?, 1)
            ON CONFLICT(guild_id) DO UPDATE SET role_id = excluded.role_id, enabled = 1
            """,
            (interaction.guild.id, rolle.id),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Gespeichert", f"Neue Mitglieder bekommen jetzt automatisch {rolle.mention}."),
            ephemeral=True,
        )

    @autorole_group.command(name="aus", description="Automatische Rollenvergabe deaktivieren")
    async def aus(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "UPDATE autorole_settings SET enabled = 0 WHERE guild_id = ?", (interaction.guild.id,),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Deaktiviert", "Automatische Rollenvergabe ist jetzt aus."), ephemeral=True,
        )

    @autorole_group.command(name="anzeigen", description="Aktuelle Auto-Role-Einstellung anzeigen")
    async def anzeigen(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await _get_settings(self.bot, interaction.guild.id)
        role = interaction.guild.get_role(int(settings["role_id"])) if settings.get("role_id") else None
        status = "✅ an" if settings.get("enabled") and role else "❌ aus"
        await interaction.response.send_message(
            embed=base_embed(
                "Auto-Role", f"Status: {status}\nRolle: {role.mention if role else '_keine gesetzt_'}",
            ),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        settings = await _get_settings(self.bot, member.guild.id)
        if not settings.get("enabled") or not settings.get("role_id"):
            return
        role = member.guild.get_role(int(settings["role_id"]))
        if role is None:
            return
        try:
            await member.add_roles(role, reason="Auto-Role bei Beitritt")
        except discord.HTTPException:
            pass


async def setup(bot: "ShopBot") -> None:
    await _ensure_table(bot)
    await bot.add_cog(AutoRoleCog(bot))
