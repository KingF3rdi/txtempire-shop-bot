from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.credits import (
    CREDIT_VALUE,
    credits_to_currency,
    format_credits,
    parse_credits_amount,
)
from utils.embeds import error_embed, format_price, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


class BuyCreditsModal(discord.ui.Modal, title="Credits kaufen"):
    amount = discord.ui.TextInput(
        label="Anzahl Credits",
        placeholder="z.B. 5  (= 500k)",
        max_length=12,
        required=True,
    )

    def __init__(self, bot: ShopBot, *, panel_slot: int) -> None:
        super().__init__()
        self.bot = bot
        self.panel_slot = panel_slot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from cogs.tickets import create_order_ticket

        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"),
                ephemeral=True,
            )
            return

        try:
            credits = parse_credits_amount(str(self.amount.value))
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültig", str(e)),
                ephemeral=True,
            )
            return

        price = credits_to_currency(credits)
        cart_rows = [
            {
                "item_id": None,
                "category_id": None,
                "name": f"{format_credits(credits)} Credits",
                "price": price,
                "qty": 1,
                "pack_dm_text": "",
                "pack_link": "",
                "pack_file": "",
                "item_role_id": None,
                "category_role_id": None,
            }
        ]

        await interaction.response.defer(ephemeral=True)
        try:
            channel = await create_order_ticket(
                self.bot,
                interaction,
                cart_rows=cart_rows,
                clear_cart=False,
                credits_enabled=True,
                order_kind="credits",
                credits_amount=credits,
                source_panel_slot=self.panel_slot,
            )
        except ValueError as e:
            await interaction.followup.send(
                embed=error_embed("Credits-Kauf fehlgeschlagen", str(e)[:1500]),
                ephemeral=True,
            )
            return
        except Exception as e:
            await interaction.followup.send(
                embed=error_embed(
                    "Credits-Kauf fehlgeschlagen",
                    f"`{type(e).__name__}: {e}`",
                ),
                ephemeral=True,
            )
            return

        balance = await self.bot.db.get_credits(
            interaction.guild.id, interaction.user.id
        )
        await interaction.followup.send(
            embed=success_embed(
                "Credits-Ticket erstellt",
                f"**{format_credits(credits)} Credits** "
                f"(= {format_price(price)})\n"
                f"Ticket: {channel.mention}\n\n"
                f"1 Credit = {int(CREDIT_VALUE / 1000)}k\n"
                f"Dein aktuelles Guthaben: **{format_credits(balance)}**\n\n"
                "Zahle wie gewohnt und warte auf Staff-Bestätigung — "
                "danach werden die Credits gutgeschrieben.",
            ),
            ephemeral=True,
        )
