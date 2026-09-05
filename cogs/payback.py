from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.credits import currency_to_credits, format_credits
from utils.embeds import error_embed, format_price, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


class PaybackCog(commands.Cog):
    """Daily Payback-XP → Guthaben ab 100 XP."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="daily",
        description=f"Täglich {config.PAYBACK_DAILY_XP} Payback-XP abholen",
    )
    async def daily(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        try:
            result = await self.bot.db.claim_daily_xp(
                interaction.guild.id,
                interaction.user.id,
                xp_gain=config.PAYBACK_DAILY_XP,
            )
        except ValueError as e:
            row = await self.bot.db.get_payback(
                interaction.guild.id, interaction.user.id
            )
            await interaction.response.send_message(
                embed=error_embed(
                    "Daily schon abgeholt",
                    f"{e}\n\nAktuell: **{int(row.get('xp') or 0)} XP** "
                    f"(Belohnung ab **{config.PAYBACK_REWARD_XP} XP** → "
                    f"{format_price(config.PAYBACK_REWARD_CURRENCY)}).",
                ),
                ephemeral=True,
            )
            return

        body = (
            f"+**{result['gained']} XP** · Stand: **{result['xp']} XP**\n"
            f"Nächste Belohnung bei **{config.PAYBACK_REWARD_XP} XP** → "
            f"**{format_price(config.PAYBACK_REWARD_CURRENCY)}** Guthaben "
            f"({format_credits(currency_to_credits(config.PAYBACK_REWARD_CURRENCY))} Credits)."
        )
        if result["rewards"] > 0:
            body += (
                f"\n\n🎉 **{result['rewards']}× Belohnung!** "
                f"+**{format_price(result['currency_granted'])}** "
                f"({format_credits(result['credits_granted'])} Credits)\n"
                f"Credits jetzt: **{format_credits(result['balance'] or 0)}**"
            )
        await interaction.response.send_message(
            embed=success_embed("Payback Daily", body),
            ephemeral=True,
        )

    @app_commands.command(
        name="dayli",
        description="Alias für /daily (Payback-XP)",
    )
    async def dayli(self, interaction: discord.Interaction) -> None:
        await self.daily(interaction)

    @app_commands.command(
        name="payback",
        description="Dein Payback-XP und Belohnungsstand",
    )
    async def payback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        row = await self.bot.db.get_payback(
            interaction.guild.id, interaction.user.id
        )
        xp = int(row.get("xp") or 0)
        need = max(0, config.PAYBACK_REWARD_XP - xp)
        await interaction.response.send_message(
            embed=success_embed(
                "Payback",
                f"**XP:** {xp} / {config.PAYBACK_REWARD_XP}\n"
                f"**Noch bis Belohnung:** {need} XP\n"
                f"**Belohnung:** {format_price(config.PAYBACK_REWARD_CURRENCY)} "
                f"Guthaben ({format_credits(currency_to_credits(config.PAYBACK_REWARD_CURRENCY))} Credits)\n"
                f"**Daily:** +{config.PAYBACK_DAILY_XP} XP (`/daily`)\n"
                f"**Bisher eingelöst:** {int(row.get('rewards_claimed') or 0)}×\n"
                f"Letztes Daily: `{row.get('last_daily') or '—'}`",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(PaybackCog(bot))
