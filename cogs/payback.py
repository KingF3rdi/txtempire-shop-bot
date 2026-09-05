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


async def _is_customer(bot: ShopBot, interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    settings = await bot.db.ensure_guild(interaction.guild.id)
    role_id = settings.get("customer_role_id")
    if not role_id:
        return False
    role = interaction.guild.get_role(int(role_id))
    return bool(role and role in interaction.user.roles)


def _daily_xp_for(*, is_customer: bool) -> tuple[int, bool]:
    """Returns (xp_gain, customer_bonus_applied)."""
    base = int(config.PAYBACK_DAILY_XP)
    if not is_customer or config.PAYBACK_CUSTOMER_BONUS_PCT <= 0:
        return base, False
    bonus = int(round(base * (config.PAYBACK_CUSTOMER_BONUS_PCT / 100.0)))
    return base + max(0, bonus), True


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
        customer = await _is_customer(self.bot, interaction)
        xp_gain, bonus_on = _daily_xp_for(is_customer=customer)
        try:
            result = await self.bot.db.claim_daily_xp(
                interaction.guild.id,
                interaction.user.id,
                xp_gain=xp_gain,
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

        bonus_note = (
            f" (Kunde +{config.PAYBACK_CUSTOMER_BONUS_PCT}%)"
            if bonus_on
            else ""
        )
        body = (
            f"+**{result['gained']} XP**{bonus_note} · Stand: **{result['xp']} XP**\n"
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
        customer = await _is_customer(self.bot, interaction)
        xp_gain, bonus_on = _daily_xp_for(is_customer=customer)
        daily_line = f"**Daily:** +{xp_gain} XP (`/daily`)"
        if bonus_on:
            daily_line += (
                f" — Kunde +{config.PAYBACK_CUSTOMER_BONUS_PCT}% "
                f"(Basis {config.PAYBACK_DAILY_XP})"
            )
        elif config.PAYBACK_CUSTOMER_BONUS_PCT > 0:
            daily_line += (
                f"\n**Kunde-Bonus:** +{config.PAYBACK_CUSTOMER_BONUS_PCT}% XP "
                f"mit Customer-Rolle"
            )
        await interaction.response.send_message(
            embed=success_embed(
                "Payback",
                f"**XP:** {xp} / {config.PAYBACK_REWARD_XP}\n"
                f"**Noch bis Belohnung:** {need} XP\n"
                f"**Belohnung:** {format_price(config.PAYBACK_REWARD_CURRENCY)} "
                f"Guthaben ({format_credits(currency_to_credits(config.PAYBACK_REWARD_CURRENCY))} Credits)\n"
                f"{daily_line}\n"
                f"**Bisher eingelöst:** {int(row.get('rewards_claimed') or 0)}×\n"
                f"Letztes Daily: `{row.get('last_daily') or '—'}`",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(PaybackCog(bot))
