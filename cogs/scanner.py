from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.archive_scanner import is_scannable_filename, scan_archive_bytes
from utils.embeds import error_embed, format_price, success_embed, warn_embed
from utils.scan_limits import consume_scan_quota, get_scan_quota
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


class ScanPremiumBuyView(discord.ui.View):
    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(
        label="14 Tage Premium",
        style=discord.ButtonStyle.primary,
        emoji="⭐",
    )
    async def buy_14(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await open_scan_premium_ticket(self.bot, interaction, days=14)

    @discord.ui.button(
        label="30 Tage Premium",
        style=discord.ButtonStyle.success,
        emoji="💎",
    )
    async def buy_30(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await open_scan_premium_ticket(self.bot, interaction, days=30)


async def open_scan_premium_ticket(
    bot: ShopBot, interaction: discord.Interaction, *, days: int
) -> None:
    from cogs.tickets import create_order_ticket

    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return

    days = 14 if days not in (14, 30) else days
    price = (
        config.SCAN_PREMIUM_14_PRICE if days == 14 else config.SCAN_PREMIUM_30_PRICE
    )
    cart_rows = [
        {
            "item_id": None,
            "category_id": None,
            "name": f"Scan Premium {days} Tage",
            "price": float(price),
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
            credits_enabled=False,
            order_kind="scan_premium",
            credits_amount=float(days),
        )
    except ValueError as e:
        await interaction.followup.send(
            embed=error_embed("Premium-Kauf fehlgeschlagen", str(e)[:1500]),
            ephemeral=True,
        )
        return
    except Exception as e:
        await interaction.followup.send(
            embed=error_embed(
                "Premium-Kauf fehlgeschlagen",
                f"`{type(e).__name__}: {e}`",
            ),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=success_embed(
            "Scan-Premium Ticket",
            f"**{days} Tage** Premium (= {format_price(price)})\n"
            f"→ **{config.SCAN_PREMIUM_DAILY} Scans/Tag**\n"
            f"Ticket: {channel.mention}\n\n"
            "Zahle wie gewohnt — nach Staff-Bestätigung ist Premium aktiv.",
        ),
        ephemeral=True,
    )


class ScannerCog(commands.Cog):
    """ZIP/RAR-Scanner auf RATs, Stealer und verdächtige Dateien."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="scan",
        description="ZIP/RAR/JAR auf RATs, Stealer und verdächtige Dateien scannen",
    )
    @app_commands.describe(file="Archiv-Datei (.zip / .rar / .jar)")
    async def scan(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server", "Scans nur im Server."),
                ephemeral=True,
            )
            return

        if not is_scannable_filename(file.filename):
            await interaction.response.send_message(
                embed=error_embed(
                    "Falscher Dateityp",
                    "Bitte eine **.zip**, **.rar** oder **.jar** Datei anhängen.",
                ),
                ephemeral=True,
            )
            return

        staff = await is_staff(self.bot, interaction)
        await interaction.response.defer(ephemeral=True)

        # Limit prüfen bevor Download zählt? Erst Quota-Check ohne Consume:
        from utils.scan_limits import get_scan_quota

        preview = await get_scan_quota(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            is_staff=staff,
        )
        if not staff and preview["remaining"] <= 0:
            tier = "Premium (15/Tag)" if preview["premium"] else "Free (1/Tag)"
            await interaction.followup.send(
                embed=error_embed(
                    "Scan-Limit",
                    f"Tageslimit erreicht ({tier}). "
                    f"Heute: **{preview['used']}/{preview['limit']}**.\n"
                    "Hol dir **Scan Premium** mit `/scanpremium` (14 oder 30 Tage).",
                ),
                ephemeral=True,
            )
            return

        try:
            data = await file.read()
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed("Download fehlgeschlagen", str(e)[:500]),
                ephemeral=True,
            )
            return

        try:
            quota = await consume_scan_quota(
                self.bot,
                interaction.guild.id,
                interaction.user.id,
                is_staff=staff,
            )
        except ValueError as e:
            await interaction.followup.send(
                embed=error_embed("Scan-Limit", str(e)),
                ephemeral=True,
            )
            return

        result = scan_archive_bytes(data, file.filename or "archive")
        footer = (
            f"Scans heute: {quota['used']}/{quota['limit']}"
            if not staff
            else "Staff — kein Limit"
        )
        if result.error and not result.findings:
            embed = warn_embed("Scan", result.summary())
            embed.set_footer(text=footer)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        if result.is_clean:
            embed = success_embed("Scan sauber", result.summary())
            embed.set_footer(text=footer)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = warn_embed("Verdächtige Datei", result.summary())
        if result.is_blocked:
            embed.title = "⛔ Kritische Treffer (RAT / Malware-Indikatoren)"
            embed.color = discord.Color.dark_red()
        embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="scanstatus",
        description="Dein Scan-Kontingent und Premium-Status",
    )
    async def scanstatus(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        staff = await is_staff(self.bot, interaction)
        quota = await get_scan_quota(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            is_staff=staff,
        )
        if staff:
            body = (
                "**Staff** — unbegrenzte Scans.\n"
                f"Heute genutzt: **{quota['used']}**"
            )
        elif quota["premium"]:
            body = (
                f"**Premium aktiv** bis `{quota['expires_at']}`\n"
                f"**{config.SCAN_PREMIUM_DAILY} Scans/Tag**\n"
                f"Heute: **{quota['used']}/{quota['limit']}** "
                f"(noch {quota['remaining']})"
            )
        else:
            body = (
                f"**Free** — **{config.SCAN_FREE_DAILY} Scan/Tag**\n"
                f"Heute: **{quota['used']}/{quota['limit']}** "
                f"(noch {quota['remaining']})\n\n"
                f"Premium: `/scanpremium` — "
                f"14 Tage ({format_price(config.SCAN_PREMIUM_14_PRICE)}) oder "
                f"30 Tage ({format_price(config.SCAN_PREMIUM_30_PRICE)}) "
                f"→ **{config.SCAN_PREMIUM_DAILY} Scans/Tag**"
            )
        await interaction.response.send_message(
            embed=success_embed("Scan-Status", body),
            ephemeral=True,
        )

    @app_commands.command(
        name="scanpremium",
        description="Scan Premium kaufen (14 oder 30 Tage → 15 Scans/Tag)",
    )
    async def scanpremium(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        quota = await get_scan_quota(
            self.bot, interaction.guild.id, interaction.user.id
        )
        extra = ""
        if quota["premium"]:
            extra = f"\n\nDein Premium läuft bis `{quota['expires_at']}` (Kauf verlängert)."
        await interaction.response.send_message(
            embed=success_embed(
                "Scan Premium",
                f"Free: **{config.SCAN_FREE_DAILY}/Tag** · "
                f"Premium: **{config.SCAN_PREMIUM_DAILY}/Tag**\n\n"
                f"• **14 Tage** — {format_price(config.SCAN_PREMIUM_14_PRICE)}\n"
                f"• **30 Tage** — {format_price(config.SCAN_PREMIUM_30_PRICE)}"
                f"{extra}",
            ),
            view=ScanPremiumBuyView(self.bot),
            ephemeral=True,
        )

    @app_commands.command(
        name="scanpack",
        description="Gespeicherte Pack-Datei eines Items scannen (Staff)",
    )
    @app_commands.describe(item="Item-ID")
    @app_commands.default_permissions(manage_guild=True)
    async def scanpack(
        self, interaction: discord.Interaction, item: int
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
        from utils.packs import resolve_pack_path

        path = resolve_pack_path(row.get("pack_file"))
        if path is None:
            await interaction.response.send_message(
                embed=error_embed("Kein Pack", "Dieses Item hat keine lokale Pack-Datei."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        from utils.archive_scanner import scan_archive_path

        result = scan_archive_path(path)
        if result.is_clean:
            await interaction.followup.send(
                embed=success_embed(
                    f"Pack Item `{item}` sauber", result.summary()
                ),
                ephemeral=True,
            )
            return
        embed = warn_embed(f"Pack Item `{item}`", result.summary())
        if result.is_blocked:
            embed.color = discord.Color.dark_red()
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="scangrant",
        description="Scan Premium manuell vergeben (Staff)",
    )
    @app_commands.describe(user="User", days="Tage (z.B. 14 oder 30)")
    @app_commands.default_permissions(manage_guild=True)
    async def scangrant(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        days: app_commands.Range[int, 1, 365] = 14,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        expires = await self.bot.db.extend_scan_premium(
            interaction.guild.id, user.id, int(days)
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Premium vergeben",
                f"{user.mention}: **+{days} Tage**\nLäuft bis `{expires}`",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(ScannerCog(bot))
