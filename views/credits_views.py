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

# Auswahl-Mengen für den Credits-Kauf
CREDIT_PRESETS = (1, 2, 5, 10, 25, 50, 100)


async def open_credits_purchase_ticket(
    bot: ShopBot,
    interaction: discord.Interaction,
    *,
    credits: float,
    panel_slot: int,
) -> None:
    """Erstellt ein Ticket zum Kauf von Credits (Betrag = credits × 100k)."""
    from cogs.tickets import create_order_ticket

    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"),
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

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    try:
        channel = await create_order_ticket(
            bot,
            interaction,
            cart_rows=cart_rows,
            clear_cart=False,
            credits_enabled=True,
            order_kind="credits",
            credits_amount=credits,
            source_panel_slot=panel_slot,
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

    balance = await bot.db.get_credits(interaction.guild.id, interaction.user.id)
    await interaction.followup.send(
        embed=success_embed(
            "Credits-Ticket erstellt",
            f"**{format_credits(credits)} Credits** "
            f"(= {format_price(price)})\n"
            f"Ticket: {channel.mention}\n\n"
            f"1 Credit = {int(CREDIT_VALUE / 1000)}k\n"
            f"Dein aktuelles Guthaben: **{format_credits(balance)}**\n\n"
            "Zahle wie gewohnt → Staff bestätigt → Credits werden gutgeschrieben.\n"
            "Danach kannst du Produkte per **Quick Buy** im Ticket kaufen.",
        ),
        ephemeral=True,
    )


class BuyCreditsAmountView(discord.ui.View):
    """Auswahl: wie viele Credits kaufen?"""

    def __init__(self, bot: ShopBot, *, panel_slot: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.panel_slot = panel_slot

        options = [
            discord.SelectOption(
                label=f"{n} Credit{'s' if n != 1 else ''}",
                value=str(n),
                description=f"= {format_price(credits_to_currency(n))}",
                emoji="🪙",
            )
            for n in CREDIT_PRESETS
        ]
        select = discord.ui.Select(
            placeholder="Wie viele Credits kaufen?",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

        custom_btn = discord.ui.Button(
            label="Andere Anzahl…",
            style=discord.ButtonStyle.secondary,
            emoji="✏️",
        )
        custom_btn.callback = self._on_custom  # type: ignore[method-assign]
        self.add_item(custom_btn)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = [
            c for c in self.children if isinstance(c, discord.ui.Select)
        ][0]
        credits = float(select.values[0])
        await open_credits_purchase_ticket(
            self.bot, interaction, credits=credits, panel_slot=self.panel_slot
        )
        self.stop()

    async def _on_custom(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            BuyCreditsModal(self.bot, panel_slot=self.panel_slot)
        )
        self.stop()


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
        try:
            credits = parse_credits_amount(str(self.amount.value))
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültig", str(e)),
                ephemeral=True,
            )
            return
        await open_credits_purchase_ticket(
            self.bot, interaction, credits=credits, panel_slot=self.panel_slot
        )
