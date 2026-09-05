from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


class GiveawayEnterView(discord.ui.View):
    """Persistente Teilnahme-Buttons für alle Giveaways."""

    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Teilnehmen",
        style=discord.ButtonStyle.success,
        custom_id="giveaway:enter",
        emoji="🎉",
    )
    async def enter(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.message is None or interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Ungültig"), ephemeral=True
            )
            return
        gw = await self.bot.db.get_giveaway_by_message(interaction.message.id)
        if not gw or gw["status"] != "active":
            await interaction.response.send_message(
                embed=error_embed("Beendet", "Dieses Giveaway läuft nicht mehr."),
                ephemeral=True,
            )
            return
        if await self.bot.db.has_giveaway_entry(
            int(gw["id"]), interaction.user.id
        ):
            await interaction.response.send_message(
                embed=success_embed(
                    "Schon dabei",
                    "Du nimmst bereits an diesem Giveaway teil.",
                ),
                ephemeral=True,
            )
            return
        ok = await self.bot.db.add_giveaway_entry(
            int(gw["id"]), interaction.user.id
        )
        if not ok:
            await interaction.response.send_message(
                embed=success_embed("Schon dabei", "Du bist bereits eingetragen."),
                ephemeral=True,
            )
            return

        count = await self.bot.db.count_giveaway_entries(int(gw["id"]))
        await interaction.response.send_message(
            embed=success_embed(
                "Teilnahme gespeichert",
                f"Viel Glück! Aktuell **{count}** Teilnehmer.",
            ),
            ephemeral=True,
        )

        # Embed-Teilnehmerzahl aktualisieren
        from datetime import datetime, timezone

        from utils.giveaways import build_giveaway_embed

        try:
            ends = datetime.strptime(
                str(gw["ends_at"]), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            ends = datetime.now(timezone.utc)
        item = await self.bot.db.get_item(int(gw["item_id"]))
        price = float(item["price"]) if item else 0.0
        host = interaction.guild.get_member(int(gw["host_id"])) or interaction.user
        embed = build_giveaway_embed(
            prize_name=str(gw["prize_name"]),
            price=price,
            ends_at=ends,
            winners_count=int(gw["winners_count"] or 1),
            entries=count,
            host=host,
            status="active",
        )
        try:
            await interaction.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass
