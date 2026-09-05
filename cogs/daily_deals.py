from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import error_embed, format_price, success_embed
from utils.price import parse_price
from views.daily_deal_views import (
    DailyDealView,
    build_daily_deal_embed,
    deal_is_expired,
)

if TYPE_CHECKING:
    from bot import ShopBot


def compute_deal_price(
    original: float, discount_type: str, discount_value: float
) -> float:
    if discount_type == "percent":
        if discount_value <= 0 or discount_value >= 100:
            raise ValueError("Prozent-Rabatt muss zwischen 0 und 100 liegen.")
        price = original * (1.0 - discount_value / 100.0)
    else:
        if discount_value <= 0:
            raise ValueError("Betrags-Rabatt muss größer als 0 sein.")
        if discount_value >= original:
            raise ValueError("Betrags-Rabatt muss kleiner als der Originalpreis sein.")
        price = original - discount_value
    price = round(max(price, 0.01), 2)
    if price >= original:
        raise ValueError("Deal-Preis muss unter dem Originalpreis liegen.")
    return price


class DailyDealsCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    dailydeal = app_commands.Group(
        name="dailydeal",
        description="Daily Deals posten und verwalten",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if not interaction.guild_id:
            return []
        items = await self.bot.db.list_items(interaction.guild_id, active_only=True)
        q = (current or "").lower().strip()
        if q:
            items = [
                i
                for i in items
                if q in (i.get("name") or "").lower() or q == str(i.get("id"))
            ]
        return [
            app_commands.Choice(
                name=f"{i['name'][:70]} — {format_price(float(i['price']))} (#{i['id']})",
                value=int(i["id"]),
            )
            for i in items[:25]
        ]

    async def _deal_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if not interaction.guild_id:
            return []
        deals = await self.bot.db.list_active_daily_deals(interaction.guild_id)
        q = (current or "").lower().strip()
        out: list[app_commands.Choice[int]] = []
        for d in deals:
            label = (
                f"#{d['id']} {d.get('item_name') or 'Item'} — "
                f"{format_price(float(d['deal_price']))}"
            )
            if q and q not in label.lower() and q != str(d["id"]):
                continue
            out.append(app_commands.Choice(name=label[:100], value=int(d["id"])))
            if len(out) >= 25:
                break
        return out

    @dailydeal.command(
        name="post",
        description="Daily Deal posten: Rabatt + Produkt mit Direkt-Kauf-Button",
    )
    @app_commands.describe(
        item="Produkt (tippen zum Suchen)",
        discount_type="Rabattart",
        discount="Rabatt — z.B. 20 (für 20%) oder 5 / 5k (für Betrag)",
        channel="Ziel-Channel (Standard: aktueller Channel)",
        hours="Gültigkeit in Stunden (leer = ohne Ablauf)",
    )
    @app_commands.choices(
        discount_type=[
            app_commands.Choice(name="Prozent (%)", value="percent"),
            app_commands.Choice(name="Betrag (€)", value="amount"),
        ]
    )
    async def dailydeal_post(
        self,
        interaction: discord.Interaction,
        item: int,
        discount_type: Literal["percent", "amount"],
        discount: str,
        channel: Optional[discord.TextChannel] = None,
        hours: Optional[app_commands.Range[float, 0.5, 720]] = None,
    ) -> None:
        assert interaction.guild is not None

        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Kein Channel",
                    "Bitte einen Text-Channel angeben oder den Befehl dort ausführen.",
                ),
                ephemeral=True,
            )
            return

        row = await self.bot.db.get_item(item)
        if not row or int(row["guild_id"]) != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Produkt nicht gefunden"),
                ephemeral=True,
            )
            return
        if not int(row.get("active") or 0):
            await interaction.response.send_message(
                embed=error_embed("Produkt inaktiv", "Aktiviere das Produkt zuerst."),
                ephemeral=True,
            )
            return

        try:
            discount_value = parse_price(discount)
            original = float(row["price"])
            deal_price = compute_deal_price(original, discount_type, discount_value)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Rabatt", str(e)[:500]),
                ephemeral=True,
            )
            return

        expires_at: str | None = None
        if hours is not None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=float(hours))
            ).strftime("%Y-%m-%d %H:%M:%S")

        await interaction.response.defer(ephemeral=True)

        deal_id = await self.bot.db.create_daily_deal(
            interaction.guild.id,
            int(row["id"]),
            discount_type=discount_type,
            discount_value=float(discount_value),
            original_price=original,
            deal_price=deal_price,
            created_by=interaction.user.id,
            expires_at=expires_at,
        )
        deal = await self.bot.db.get_daily_deal(deal_id)
        assert deal is not None

        cat = await self.bot.db.get_category(int(row["category_id"]))
        embed = build_daily_deal_embed(
            item=row,
            deal=deal,
            category_name=cat["name"] if cat else None,
        )
        view = DailyDealView(self.bot, deal_id)
        try:
            msg = await target.send(embed=embed, view=view)
        except discord.Forbidden:
            await self.bot.db.deactivate_daily_deal(deal_id)
            await interaction.followup.send(
                embed=error_embed(
                    "Keine Berechtigung",
                    f"Bot darf in {target.mention} nicht posten.",
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await self.bot.db.deactivate_daily_deal(deal_id)
            await interaction.followup.send(
                embed=error_embed("Post fehlgeschlagen", str(e)[:500]),
                ephemeral=True,
            )
            return

        await self.bot.db.update_daily_deal(
            deal_id, channel_id=target.id, message_id=msg.id
        )
        self.bot.add_view(view)

        savings = original - deal_price
        await interaction.followup.send(
            embed=success_embed(
                "Daily Deal gepostet",
                f"**{row['name']}** in {target.mention}\n"
                f"{format_price(original)} → **{format_price(deal_price)}** "
                f"(erspart {format_price(savings)})\n"
                f"Deal-ID: `{deal_id}`"
                + (f"\nLäuft ab: `{expires_at}`" if expires_at else ""),
            ),
            ephemeral=True,
        )

    @dailydeal_post.autocomplete("item")
    async def dailydeal_post_item_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._item_autocomplete(interaction, current)

    @dailydeal.command(name="end", description="Aktiven Daily Deal beenden")
    @app_commands.describe(deal="Aktiver Deal (tippen zum Suchen)")
    async def dailydeal_end(
        self, interaction: discord.Interaction, deal: int
    ) -> None:
        assert interaction.guild is not None
        row = await self.bot.db.get_daily_deal(deal)
        if not row or int(row["guild_id"]) != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Deal nicht gefunden"),
                ephemeral=True,
            )
            return
        if not int(row.get("active") or 0):
            await interaction.response.send_message(
                embed=error_embed("Bereits beendet", "Dieser Deal ist schon inaktiv."),
                ephemeral=True,
            )
            return

        await self.bot.db.deactivate_daily_deal(deal)

        channel_id = row.get("channel_id")
        message_id = row.get("message_id")
        if channel_id and message_id:
            ch = interaction.guild.get_channel(int(channel_id))
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(int(message_id))
                    embed = msg.embeds[0] if msg.embeds else None
                    if embed:
                        embed.title = f"⛔ Beendet — {embed.title or 'Daily Deal'}"
                        embed.color = discord.Color.dark_grey()
                    await msg.edit(embed=embed, view=None)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        await interaction.response.send_message(
            embed=success_embed(
                "Daily Deal beendet",
                f"Deal `{deal}` ist nicht mehr kaufbar.",
            ),
            ephemeral=True,
        )

    @dailydeal_end.autocomplete("deal")
    async def dailydeal_end_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._deal_autocomplete(interaction, current)

    @dailydeal.command(name="list", description="Aktive Daily Deals anzeigen")
    async def dailydeal_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        deals = await self.bot.db.list_active_daily_deals(interaction.guild.id)
        if not deals:
            await interaction.response.send_message(
                embed=success_embed("Daily Deals", "Keine aktiven Deals."),
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for d in deals:
            expired = deal_is_expired(d)
            status = "⏰ abgelaufen" if expired else "✅ aktiv"
            dtype = d.get("discount_type")
            dval = float(d.get("discount_value") or 0)
            badge = f"−{dval:g}%" if dtype == "percent" else f"−{format_price(dval)}"
            lines.append(
                f"`#{d['id']}` **{d.get('item_name') or 'Item'}** — "
                f"~~{format_price(float(d['original_price']))}~~ → "
                f"**{format_price(float(d['deal_price']))}** ({badge}) · {status}"
            )

        await interaction.response.send_message(
            embed=success_embed("Aktive Daily Deals", "\n".join(lines)[:3900]),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(DailyDealsCog(bot))
