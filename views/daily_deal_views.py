from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from config import PAYMENT_NOTICE
from utils.embeds import error_embed, format_price, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


def deal_is_expired(deal: dict) -> bool:
    expires = deal.get("expires_at")
    if not expires:
        return False
    try:
        raw = str(expires).replace("Z", "+00:00")
        if "T" not in raw and " " in raw:
            raw = raw.replace(" ", "T", 1)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= dt
    except ValueError:
        return False


def build_daily_deal_embed(
    *,
    item: dict,
    deal: dict,
    category_name: str | None = None,
) -> discord.Embed:
    original = float(deal["original_price"])
    deal_price = float(deal["deal_price"])
    dtype = deal.get("discount_type") or "percent"
    dval = float(deal.get("discount_value") or 0)
    badge = f"−{dval:g}%" if dtype == "percent" else f"−{format_price(dval)}"

    lines = [
        f"**{item['name']}**",
        "",
        f"~~{format_price(original)}~~ → **{format_price(deal_price)}** ({badge})",
    ]
    if category_name:
        lines.append(f"Kategorie: **{category_name}**")
    item_desc = (item.get("description") or "").strip()
    if item_desc:
        lines.extend(["", item_desc[:400]])
    lines.extend(
        [
            "",
            "Mit **Jetzt kaufen** startest du direkt den Checkout zum Deal-Preis.",
        ]
    )

    embed = discord.Embed(
        title=f"🔥 Daily Deal {badge}",
        description="\n".join(lines),
        color=0xE11D48,
    )
    footer = PAYMENT_NOTICE
    if deal.get("expires_at"):
        footer = f"Gültig bis {deal['expires_at']} · {footer}"
    embed.set_footer(text=footer[:200])
    return embed


class DailyDealView(discord.ui.View):
    """Persistentes Daily-Deal-Panel mit Direkt-Kauf-Button."""

    def __init__(self, bot: ShopBot, deal_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.deal_id = int(deal_id)

        buy_btn = discord.ui.Button(
            label="Jetzt kaufen",
            style=discord.ButtonStyle.success,
            custom_id=f"deal:buy:{self.deal_id}",
            emoji="🛒",
        )
        buy_btn.callback = self._on_buy  # type: ignore[method-assign]
        self.add_item(buy_btn)

    async def _on_buy(self, interaction: discord.Interaction) -> None:
        await handle_daily_deal_buy(self.bot, interaction, self.deal_id)


async def handle_daily_deal_buy(
    bot: ShopBot, interaction: discord.Interaction, deal_id: int
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

    deal = await bot.db.get_daily_deal(deal_id)
    if not deal or int(deal.get("guild_id") or 0) != interaction.guild.id:
        await interaction.followup.send(
            embed=error_embed(
                "Deal nicht gefunden", "Dieser Daily Deal existiert nicht mehr."
            ),
            ephemeral=True,
        )
        return

    if not int(deal.get("active") or 0) or deal_is_expired(deal):
        if int(deal.get("active") or 0):
            await bot.db.deactivate_daily_deal(deal_id)
        await interaction.followup.send(
            embed=error_embed(
                "Deal abgelaufen",
                "Dieser Daily Deal ist nicht mehr aktiv.",
            ),
            ephemeral=True,
        )
        return

    row = await bot.db.build_cart_row_for_item(
        int(deal["item_id"]),
        price_override=float(deal["deal_price"]),
        qty=1,
    )
    if row is None:
        await interaction.followup.send(
            embed=error_embed(
                "Produkt nicht verfügbar",
                "Das Deal-Produkt ist nicht mehr aktiv.",
            ),
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
        print(f"[DailyDeal] Kauf fehlgeschlagen (deal={deal_id}): {e!r}")
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
            "Deal-Ticket erstellt",
            f"**{row['name']}** für **{format_price(float(row['price']))}**\n"
            f"Dein Ticket: {channel.mention}\n\n**{PAYMENT_NOTICE}**",
        ),
        ephemeral=True,
    )


async def handle_daily_deal_interaction(
    bot: ShopBot, interaction: discord.Interaction
) -> bool:
    """Fallback wenn persistente Deal-View nicht registriert ist."""
    if interaction.type != discord.InteractionType.component:
        return False
    data = interaction.data or {}
    custom_id = data.get("custom_id") or ""
    if not custom_id.startswith("deal:buy:"):
        return False
    try:
        deal_id = int(custom_id.rsplit(":", 1)[-1])
    except ValueError:
        return False
    await handle_daily_deal_buy(bot, interaction, deal_id)
    return True


async def register_daily_deal_views(bot: ShopBot) -> int:
    """Registriert Views für alle aktiven Daily Deals."""
    deals = await bot.db.list_active_daily_deals()
    for deal in deals:
        bot.add_view(DailyDealView(bot, int(deal["id"])))
    return len(deals)
