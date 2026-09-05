"""Direkt-Kauf-Panel für ein einzelnes Shop-Item (/new item, /new panel)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from config import PAYMENT_NOTICE
from utils.embeds import base_embed, error_embed, format_price, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


def build_item_buy_embed(
    *,
    item: dict,
    category_name: str | None = None,
    discount_code: str | None = None,
    discount_label: str | None = None,
) -> discord.Embed:
    price = float(item["price"])
    desc_parts = [
        f"**{item['name']}** — **{format_price(price)}**",
    ]
    if category_name:
        desc_parts.append(f"Kategorie: **{category_name}**")
    item_desc = (item.get("description") or "").strip()
    if item_desc:
        desc_parts.extend(["", item_desc[:500]])
    desc_parts.extend(
        [
            "",
            "Mit **Jetzt kaufen** startest du direkt den Checkout für dieses Item.",
        ]
    )
    if discount_code:
        desc_parts.extend(
            [
                "",
                f"🏷️ Launch-Code: `{discount_code}`"
                + (f" — {discount_label}" if discount_label else "")
                + " (im Ticket unter **Rabatt / Creator Code**)",
            ]
        )
    embed = base_embed(f"Neu: {item['name'][:80]}", "\n".join(desc_parts))
    embed.set_footer(text=f"Item #{item['id']} · {PAYMENT_NOTICE}")
    return embed


class ItemBuyView(discord.ui.View):
    """Persistentes Panel: ein Button → Ticket für genau dieses Item."""

    def __init__(self, bot: ShopBot, item_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.item_id = int(item_id)

        buy_btn = discord.ui.Button(
            label="Jetzt kaufen",
            style=discord.ButtonStyle.success,
            custom_id=f"item:buy:{self.item_id}",
            emoji="🛒",
        )
        buy_btn.callback = self._on_buy  # type: ignore[method-assign]
        self.add_item(buy_btn)

    async def _on_buy(self, interaction: discord.Interaction) -> None:
        await handle_item_direct_buy(self.bot, interaction, self.item_id)


async def handle_item_direct_buy(
    bot: ShopBot, interaction: discord.Interaction, item_id: int
) -> None:
    from cogs.tickets import create_order_ticket

    if interaction.guild is None:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error_embed(
                    "Nur auf dem Server", "Bitte im Server-Channel kaufen."
                ),
                ephemeral=True,
            )
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    item = await bot.db.get_item(item_id)
    if (
        not item
        or int(item.get("guild_id") or 0) != interaction.guild.id
        or not int(item.get("active") or 0)
    ):
        await interaction.followup.send(
            embed=error_embed(
                "Item nicht verfügbar",
                "Dieses Produkt ist nicht mehr aktiv oder wurde gelöscht.",
            ),
            ephemeral=True,
        )
        return

    row = await bot.db.build_cart_row_for_item(item_id, qty=1)
    if row is None:
        await interaction.followup.send(
            embed=error_embed("Produkt nicht verfügbar"),
            ephemeral=True,
        )
        return

    try:
        channel = await create_order_ticket(
            bot,
            interaction,
            cart_rows=[row],
            clear_cart=False,
        )
    except ValueError as e:
        await interaction.followup.send(
            embed=error_embed("Kauf fehlgeschlagen", str(e)[:1500]),
            ephemeral=True,
        )
        return
    except Exception as e:
        print(f"[ItemBuy] Kauf fehlgeschlagen (item={item_id}): {e!r}")
        await interaction.followup.send(
            embed=error_embed(
                "Kauf fehlgeschlagen",
                f"Unerwarteter Fehler: `{type(e).__name__}: {e}`",
            ),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=success_embed(
            "Ticket erstellt",
            f"**{row['name']}** für **{format_price(float(row['price']))}**\n"
            f"Dein Ticket: {channel.mention}\n\n**{PAYMENT_NOTICE}**",
        ),
        ephemeral=True,
    )


async def handle_item_buy_interaction(
    bot: ShopBot, interaction: discord.Interaction
) -> bool:
    if interaction.type != discord.InteractionType.component:
        return False
    data = interaction.data or {}
    custom_id = data.get("custom_id") or ""
    if not custom_id.startswith("item:buy:"):
        return False
    try:
        item_id = int(custom_id.rsplit(":", 1)[-1])
    except ValueError:
        return False
    await handle_item_direct_buy(bot, interaction, item_id)
    return True


def ensure_item_buy_view(bot: ShopBot, item_id: int) -> None:
    registered: set[int] = getattr(bot, "_item_buy_registered", set())
    iid = int(item_id)
    if iid in registered:
        return
    bot.add_view(ItemBuyView(bot, iid))
    registered.add(iid)
    bot._item_buy_registered = registered
