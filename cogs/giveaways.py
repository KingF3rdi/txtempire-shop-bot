from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.embeds import error_embed, format_price, success_embed
from utils.giveaways import finish_giveaway, parse_duration, build_giveaway_embed
from views.giveaway_views import GiveawayEnterView
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


class GiveawaysCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self) -> None:
        self.check_giveaways.cancel()

    @tasks.loop(seconds=30)
    async def check_giveaways(self) -> None:
        due = await self.bot.db.list_due_giveaways()
        for gw in due:
            try:
                await finish_giveaway(self.bot, gw)
            except Exception as e:
                print(f"[Giveaway] Ende fehlgeschlagen #{gw.get('id')}: {e!r}")

    @check_giveaways.before_loop
    async def before_check(self) -> None:
        await self.bot.wait_until_ready()

    giveaway = app_commands.Group(
        name="giveaway",
        description="Produkt-Giveaways",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _item_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if not interaction.guild_id:
            return []
        items = await self.bot.db.list_items(
            interaction.guild_id, active_only=True
        )
        q = (current or "").lower().strip()
        if q:
            items = [
                i
                for i in items
                if q in (i.get("name") or "").lower() or q == str(i.get("id"))
            ]
        return [
            app_commands.Choice(
                name=f"{i['name'][:70]} (#{i['id']}) — {format_price(float(i['price']))}",
                value=int(i["id"]),
            )
            for i in items[:25]
        ]

    @giveaway.command(
        name="start",
        description="Giveaway mit Shop-Produkt starten",
    )
    @app_commands.describe(
        item="Produkt (Gewinner erhält Pack + Rollen)",
        duration="Dauer z.B. 30m, 2h, 1d",
        winners="Anzahl Gewinner (Standard: 1)",
        channel="Channel (Standard: aktuell)",
    )
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        item: int,
        duration: str,
        winners: app_commands.Range[int, 1, 20] = 1,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return

        row = await self.bot.db.get_item(item)
        if not row or int(row["guild_id"]) != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Item nicht gefunden"), ephemeral=True
            )
            return
        if not int(row.get("active") or 0):
            await interaction.response.send_message(
                embed=error_embed("Item inaktiv"), ephemeral=True
            )
            return

        try:
            seconds = parse_duration(duration)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Dauer", str(e)), ephemeral=True
            )
            return

        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed("Kein Text-Channel"), ephemeral=True
            )
            return

        ends = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        ends_str = ends.strftime("%Y-%m-%d %H:%M:%S")
        prize = str(row["name"])

        await interaction.response.defer(ephemeral=True)
        gw_id = await self.bot.db.create_giveaway(
            interaction.guild.id,
            item_id=int(row["id"]),
            prize_name=prize,
            winners_count=int(winners),
            ends_at=ends_str,
            host_id=interaction.user.id,
        )
        embed = build_giveaway_embed(
            prize_name=prize,
            price=float(row["price"]),
            ends_at=ends,
            winners_count=int(winners),
            entries=0,
            host=interaction.user,
            status="active",
        )
        embed.set_footer(text=f"Giveaway #{gw_id} · Host: {interaction.user}")
        view = GiveawayEnterView(self.bot)
        try:
            msg = await target.send(embed=embed, view=view)
        except discord.HTTPException as e:
            await self.bot.db.update_giveaway(gw_id, status="ended")
            await interaction.followup.send(
                embed=error_embed("Post fehlgeschlagen", str(e)[:400]),
                ephemeral=True,
            )
            return

        await self.bot.db.update_giveaway(
            gw_id, channel_id=target.id, message_id=msg.id
        )
        await interaction.followup.send(
            embed=success_embed(
                "Giveaway gestartet",
                f"**{prize}** in {target.mention}\n"
                f"Ende <t:{int(ends.timestamp())}:R> · "
                f"{winners} Gewinner · ID `{gw_id}`\n"
                "Gewinner bekommen automatisch Pack + Rollen per DM.",
            ),
            ephemeral=True,
        )

    @giveaway_start.autocomplete("item")
    async def giveaway_start_item_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._item_ac(interaction, current)

    @giveaway.command(name="end", description="Giveaway vorzeitig beenden & ziehen")
    @app_commands.describe(giveaway_id="Giveaway-ID (steht im Footer)")
    async def giveaway_end(
        self, interaction: discord.Interaction, giveaway_id: int
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        gw = await self.bot.db.get_giveaway(giveaway_id)
        if not gw or int(gw["guild_id"]) != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        if gw["status"] != "active":
            await interaction.response.send_message(
                embed=error_embed("Bereits beendet"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        winners = await finish_giveaway(self.bot, gw, force=True)
        if not winners:
            await interaction.followup.send(
                embed=success_embed(
                    "Beendet",
                    "Keine Teilnehmer — kein Gewinner.",
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "Gewinner gezogen",
                ", ".join(f"<@{u}>" for u in winners)
                + "\nPack + Rollen wurden zugestellt (DM).",
            ),
            ephemeral=True,
        )

    @giveaway.command(name="list", description="Aktive Giveaways anzeigen")
    async def giveaway_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        rows = await self.bot.db.list_active_giveaways(interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                embed=success_embed("Giveaways", "Keine aktiven Giveaways."),
                ephemeral=True,
            )
            return
        lines = []
        for g in rows:
            n = await self.bot.db.count_giveaway_entries(int(g["id"]))
            lines.append(
                f"`#{g['id']}` **{g['prize_name']}** — "
                f"{n} Teilnehmer · Ende `{g['ends_at']}` UTC"
            )
        await interaction.response.send_message(
            embed=success_embed("Aktive Giveaways", "\n".join(lines)),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(GiveawaysCog(bot))
