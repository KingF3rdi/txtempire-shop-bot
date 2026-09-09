"""Einmaliger Nachtrag: bestehende (vor dem Website-Sync entstandene)
Vouches und Bestseller nachträglich an die Website melden."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import httpx
from discord import app_commands
from discord.ext import commands

import config
from integrations.shop_api import shop_api
from utils.embeds import error_embed, success_embed
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


def _stars(rating: int) -> str:
    rating = max(1, min(5, int(rating)))
    return "★" * rating + "☆" * (5 - rating)


class WebsiteBackfillCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="websitebackfill",
        description="Bestehende Vouches + Bestseller nachträglich an die Website melden (einmalig)",
    )
    @app_commands.default_permissions(administrator=True)
    async def website_backfill(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        if not shop_api.enabled:
            await interaction.response.send_message(
                embed=error_embed("Website-API deaktiviert", "SHOP_API_URL/BOT_API_KEY prüfen."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id

        vouches_synced = 0
        vouches_failed = 0
        name_cache: dict[int, str] = {}
        for row in await self.bot.db.get_rated_ticket_vouches(guild_id):
            user_id = int(row["user_id"])
            if user_id not in name_cache:
                try:
                    user = await self.bot.fetch_user(user_id)
                    name_cache[user_id] = str(user)
                except discord.HTTPException:
                    name_cache[user_id] = f"User#{user_id}"
            rating = int(row["vouch_rating"])
            ok = await shop_api.sync_vouch(
                giver_name=name_cache[user_id],
                message=_stars(rating),
                is_positive=rating >= 4,
                external_id=int(row["id"]),
                rating=rating,
                source="ticket",
            )
            if ok:
                vouches_synced += 1
            else:
                vouches_failed += 1

        products_created = 0
        products_updated = 0
        products_failed = 0
        category_cache: dict[int | None, str] = {}
        for row in await self.bot.db.get_shop_bestsellers(guild_id):
            category_id = row.get("category_id")
            if category_id not in category_cache:
                cat = await self.bot.db.get_category(int(category_id)) if category_id else None
                category_cache[category_id] = str(cat["name"]) if cat else "Sonstiges"
            result = await shop_api.upsert_product(
                category_name=category_cache[category_id],
                product_name=str(row["name"]),
                price=float(row["price"] or 0),
                sales_count=int(row["total_qty"] or 0),
            )
            if result is None:
                products_failed += 1
            elif result.get("created"):
                products_created += 1
            else:
                products_updated += 1

        await interaction.followup.send(
            embed=success_embed(
                "Website-Backfill abgeschlossen",
                f"**Vouches:** {vouches_synced} übernommen"
                + (f", {vouches_failed} fehlgeschlagen" if vouches_failed else "")
                + f"\n**Produkte:** {products_created} neu angelegt, "
                f"{products_updated} aktualisiert"
                + (f", {products_failed} fehlgeschlagen" if products_failed else ""),
            ),
        )


    @app_commands.command(
        name="nettest",
        description="Prüft ausgehende Verbindungen des Bot-Hosts (Discord, Cloudflare, allgemein)",
    )
    @app_commands.default_permissions(administrator=True)
    async def nettest(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        targets = {
            "Discord API (Baseline)": "https://discord.com/api/v10/gateway",
            "Cloudflare Worker (Website)": f"{(config.SHOP_API_URL or '').rstrip('/')}/api/stats",
            "Allgemeines Internet": "https://1.1.1.1",
        }
        lines: list[str] = []
        for label, url in targets.items():
            if not url or url == "/api/stats":
                lines.append(f"⚠️ **{label}**: SHOP_API_URL nicht gesetzt")
                continue
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(url)
                lines.append(f"✅ **{label}**: HTTP {resp.status_code} erreichbar")
            except Exception as exc:
                lines.append(f"❌ **{label}**: {type(exc).__name__} — {exc}")

        await interaction.followup.send(
            embed=success_embed("Netzwerk-Test", "\n".join(lines)),
        )


async def setup(bot: "ShopBot") -> None:
    await bot.add_cog(WebsiteBackfillCog(bot))
