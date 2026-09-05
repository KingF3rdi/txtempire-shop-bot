from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.credits import CREDIT_VALUE, format_credits, parse_credits_amount
from utils.embeds import error_embed, format_price, success_embed
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


class CreditsCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    credits = app_commands.Group(
        name="credits",
        description="Credit-Guthaben anzeigen und verwalten",
    )

    @credits.command(name="balance", description="Credit-Guthaben anzeigen")
    @app_commands.describe(user="User (nur Staff, sonst du selbst)")
    async def credits_balance(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = user or interaction.user
        if user is not None and user.id != interaction.user.id:
            if not await is_staff(self.bot, interaction):
                await interaction.response.send_message(
                    embed=error_embed(
                        "Keine Berechtigung",
                        "Nur Staff kann fremde Guthaben einsehen.",
                    ),
                    ephemeral=True,
                )
                return
        bal = await self.bot.db.get_credits(interaction.guild.id, target.id)
        await interaction.response.send_message(
            embed=success_embed(
                "Credits",
                f"{target.mention}: **{format_credits(bal)} Credits**\n"
                f"(1 Credit = {format_price(CREDIT_VALUE)})",
            ),
            ephemeral=True,
        )

    @credits.command(name="add", description="Credits hinzufügen (Staff)")
    @app_commands.describe(user="Empfänger", amount="Anzahl Credits (z.B. 5)")
    @app_commands.default_permissions(manage_guild=True)
    async def credits_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: str,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung", "Nur Staff/Admin."),
                ephemeral=True,
            )
            return
        try:
            value = parse_credits_amount(amount)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültig", str(e)),
                ephemeral=True,
            )
            return
        new_bal = await self.bot.db.add_credits(
            interaction.guild.id, user.id, value
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Credits hinzugefügt",
                f"**+{format_credits(value)}** an {user.mention}\n"
                f"Neues Guthaben: **{format_credits(new_bal)}**",
            ),
            ephemeral=True,
        )

    @credits.command(name="set", description="Credits setzen (Staff)")
    @app_commands.describe(user="User", amount="Neues Guthaben")
    @app_commands.default_permissions(manage_guild=True)
    async def credits_set(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: str,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung", "Nur Staff/Admin."),
                ephemeral=True,
            )
            return
        try:
            # 0 erlauben zum Zurücksetzen
            raw = (amount or "").strip().replace(",", ".")
            value = float(raw)
            if value < 0:
                raise ValueError("Negativ nicht erlaubt.")
            value = round(value, 2)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültig", "Bitte eine Zahl ≥ 0 angeben."),
                ephemeral=True,
            )
            return
        new_bal = await self.bot.db.set_credits(
            interaction.guild.id, user.id, value
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Credits gesetzt",
                f"{user.mention}: **{format_credits(new_bal)} Credits**",
            ),
            ephemeral=True,
        )

    @credits.command(
        name="remove", description="Credits abziehen (Staff)"
    )
    @app_commands.describe(user="User", amount="Anzahl Credits")
    @app_commands.default_permissions(manage_guild=True)
    async def credits_remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: str,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung", "Nur Staff/Admin."),
                ephemeral=True,
            )
            return
        try:
            value = parse_credits_amount(amount)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültig", str(e)),
                ephemeral=True,
            )
            return
        current = await self.bot.db.get_credits(interaction.guild.id, user.id)
        new_bal = await self.bot.db.set_credits(
            interaction.guild.id, user.id, max(0.0, current - value)
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Credits abgezogen",
                f"**−{format_credits(value)}** von {user.mention}\n"
                f"Neues Guthaben: **{format_credits(new_bal)}**",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(CreditsCog(bot))
