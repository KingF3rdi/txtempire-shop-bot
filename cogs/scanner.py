from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.archive_scanner import is_scannable_filename, scan_archive_bytes
from utils.credits import format_credits
from utils.embeds import error_embed, format_price, success_embed, warn_embed
from utils.scan_limits import consume_scan_quota, get_scan_quota
from utils.scan_prices import get_scan_prices, premium_scan_label
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
    prices = await get_scan_prices(bot, interaction.guild.id)
    price = prices["price_14"] if days == 14 else prices["price_30"]
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
            f"→ **{premium_scan_label(days=days)}**\n"
            f"Ticket: {channel.mention}\n\n"
            "Zahle wie gewohnt — nach Staff-Bestätigung ist Premium aktiv.",
        ),
        ephemeral=True,
    )


class ScannerCog(commands.Cog):
    """ZIP/RAR-Scanner auf RATs, Stealer und verdächtige Dateien."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    scan = app_commands.Group(name="scan", description="File Scanner")

    @app_commands.command(
        name="scanpanel",
        description="Scan-Panel posten oder aktualisieren (DM/URL + Premium)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def scanpanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed("Kein Channel", "Bitte einen Text-Channel wählen."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        from views.scan_panel import post_or_refresh_scan_panel

        msg = await post_or_refresh_scan_panel(
            self.bot, interaction.guild, target
        )
        await interaction.followup.send(
            embed=success_embed(
                "Scan-Panel",
                f"Panel in {target.mention}: {msg.jump_url}\n"
                "Datei droppen / URL · Premium per Zahlung oder Credits.",
            ),
            ephemeral=True,
        )

    @scan.command(
        name="file",
        description="ZIP/RAR/JAR auf RATs, Stealer und verdächtige Dateien scannen",
    )
    @app_commands.describe(file="Archiv-Datei (.zip / .rar / .jar)")
    async def scan_file(
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
        from utils.scan_premium_role import post_scan_log
        from utils.scan_stats import log_scan_result

        await log_scan_result(
            self.bot, interaction.guild.id, interaction.user.id, result
        )
        await post_scan_log(
            self.bot,
            interaction.guild,
            user=interaction.user,
            filename=file.filename or "archive",
            summary=result.summary(),
            is_clean=result.is_clean,
            is_blocked=result.is_blocked,
        )
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

    @scan.command(
        name="url",
        description="ZIP/RAR/JAR per Download-URL scannen",
    )
    @app_commands.describe(url="Direkter http(s)-Link zur Archiv-Datei")
    async def scan_url(
        self,
        interaction: discord.Interaction,
        url: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server", "Scans nur im Server."),
                ephemeral=True,
            )
            return

        staff = await is_staff(self.bot, interaction)
        await interaction.response.defer(ephemeral=True)

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
                    "Hol dir **Scan Premium** mit `/scanpremium`.",
                ),
                ephemeral=True,
            )
            return

        from utils.scan_download import download_archive_from_url

        try:
            data, filename = await download_archive_from_url(url)
        except ValueError as e:
            await interaction.followup.send(
                embed=error_embed("URL-Scan fehlgeschlagen", str(e)[:800]),
                ephemeral=True,
            )
            return

        from views.scan_panel import deliver_scan_result

        await deliver_scan_result(
            self.bot,
            interaction,
            data=data,
            filename=filename,
            guild_id=interaction.guild.id,
        )

    @scan.command(
        name="stats",
        description="Scan-Statistik: Premium, Scans, gut/schlecht nach Kategorien",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def scan_stats(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return

        stats = await self.bot.db.get_scan_stats(interaction.guild.id)
        bad = stats["suspicious"] + stats["blocked"]
        cat_lines = (
            "\n".join(
                f"• `{name}`: **{cnt}**" for name, cnt in stats["categories"][:12]
            )
            or "_Noch keine Treffer-Kategorien_"
        )
        embed = success_embed(
            "Scan-Statistik",
            f"**Premium-Käufe (bestätigt):** {stats['premium_purchases']}\n"
            f"**Unique Käufer:** {stats['premium_buyers']}\n"
            f"**Aktives Premium:** {stats['premium_active']}\n\n"
            f"**Scans geloggt:** {stats['total_logged']}\n"
            f"**Scan-Nutzung (Quota-Zähler):** {stats['usage_total']}\n\n"
            f"✅ **Gut (sauber):** {stats['clean']}\n"
            f"⚠️ **Verdächtig:** {stats['suspicious']}\n"
            f"⛔ **Schlecht (blockiert):** {stats['blocked']}\n"
            f"❌ **Fehler:** {stats['error']}\n"
            f"**Schlecht gesamt:** {bad}\n\n"
            f"**Treffer-Kategorien:**\n{cat_lines}",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @scan.command(
        name="status",
        description="Dein Scan-Kontingent und Premium-Status",
    )
    async def scan_status(self, interaction: discord.Interaction) -> None:
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
        elif quota["premium"] and quota.get("unlimited"):
            body = (
                f"**Premium aktiv** bis `{quota['expires_at']}`\n"
                f"**Unbegrenzte Scans**\n"
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
            prices = await get_scan_prices(self.bot, interaction.guild.id)
            body = (
                f"**Free** — **{config.SCAN_FREE_DAILY} Scan/Tag**\n"
                f"Heute: **{quota['used']}/{quota['limit']}** "
                f"(noch {quota['remaining']})\n\n"
                f"Premium: `/scanpremium` —\n"
                f"• 14 Tage ({format_price(prices['price_14'])}) → "
                f"**{config.SCAN_PREMIUM_DAILY} Scans/Tag**\n"
                f"• 30 Tage ({format_price(prices['price_30'])}) → "
                f"**unbegrenzte Scans**"
            )
        await interaction.response.send_message(
            embed=success_embed("Scan-Status", body),
            ephemeral=True,
        )

    @app_commands.command(
        name="scanpremium",
        description="Scan Premium kaufen (14 Tage limitiert / 30 Tage unbegrenzt)",
    )
    async def scanpremium(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        prices = await get_scan_prices(self.bot, interaction.guild.id)
        quota = await get_scan_quota(
            self.bot, interaction.guild.id, interaction.user.id
        )
        extra = ""
        if quota["premium"]:
            tier = (
                "unbegrenzt"
                if quota.get("unlimited")
                else f"{config.SCAN_PREMIUM_DAILY}/Tag"
            )
            extra = (
                f"\n\nDein Premium läuft bis `{quota['expires_at']}` "
                f"({tier}; Kauf verlängert)."
            )
        await interaction.response.send_message(
            embed=success_embed(
                "Scan Premium",
                f"Free: **{config.SCAN_FREE_DAILY}/Tag**\n"
                f"• **14 Tage** — {format_price(prices['price_14'])} "
                f"oder **{format_credits(prices['credits_14'])} Credits** "
                f"→ **{config.SCAN_PREMIUM_DAILY}/Tag**\n"
                f"• **30 Tage** — {format_price(prices['price_30'])} "
                f"oder **{format_credits(prices['credits_30'])} Credits** "
                f"→ **unbegrenzte Scans**"
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
                embed=error_embed(
                    "Kein Pack", "Dieses Item hat keine lokale Pack-Datei."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        from utils.archive_scanner import scan_archive_path
        from utils.scan_stats import log_scan_result

        result = scan_archive_path(path)
        await log_scan_result(
            self.bot, interaction.guild.id, interaction.user.id, result
        )
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
        from utils.scan_premium_role import sync_scan_premium_role

        role_status = await sync_scan_premium_role(
            self.bot, interaction.guild, user.id, force_grant=True
        )
        role_note = f"\nRolle: {role_status}" if role_status else ""
        await interaction.response.send_message(
            embed=success_embed(
                "Premium vergeben",
                f"{user.mention}: **+{days} Tage** "
                f"({premium_scan_label(days=int(days))})\n"
                f"Läuft bis `{expires}`{role_note}",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="scanprices",
        description="Scan-Premium Preise anzeigen oder setzen (Staff)",
    )
    @app_commands.describe(
        price_14="14-Tage Preis in Shop-Währung (z.B. 500000)",
        price_30="30-Tage Preis in Shop-Währung (z.B. 900000)",
        credits_14="14-Tage Preis in Credits (optional)",
        credits_30="30-Tage Preis in Credits (optional)",
        reset="Auf .env/Config-Defaults zurücksetzen",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def scanprices(
        self,
        interaction: discord.Interaction,
        price_14: float | None = None,
        price_30: float | None = None,
        credits_14: float | None = None,
        credits_30: float | None = None,
        reset: bool = False,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return

        if reset:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                scan_price_14=None,
                scan_price_30=None,
                scan_credits_14=None,
                scan_credits_30=None,
            )
        else:
            fields: dict = {}
            if price_14 is not None:
                if price_14 < 0:
                    await interaction.response.send_message(
                        embed=error_embed("Preis muss ≥ 0 sein"), ephemeral=True
                    )
                    return
                fields["scan_price_14"] = float(price_14)
            if price_30 is not None:
                if price_30 < 0:
                    await interaction.response.send_message(
                        embed=error_embed("Preis muss ≥ 0 sein"), ephemeral=True
                    )
                    return
                fields["scan_price_30"] = float(price_30)
            if credits_14 is not None:
                if credits_14 < 0:
                    await interaction.response.send_message(
                        embed=error_embed("Credits müssen ≥ 0 sein"), ephemeral=True
                    )
                    return
                fields["scan_credits_14"] = round(float(credits_14), 2)
            if credits_30 is not None:
                if credits_30 < 0:
                    await interaction.response.send_message(
                        embed=error_embed("Credits müssen ≥ 0 sein"), ephemeral=True
                    )
                    return
                fields["scan_credits_30"] = round(float(credits_30), 2)
            if fields:
                await self.bot.db.update_guild_settings(
                    interaction.guild.id, **fields
                )

        prices = await get_scan_prices(self.bot, interaction.guild.id)
        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        src = (
            "Server-Preise"
            if any(
                settings.get(k) is not None
                for k in (
                    "scan_price_14",
                    "scan_price_30",
                    "scan_credits_14",
                    "scan_credits_30",
                )
            )
            else "Config/.env Defaults"
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Scan-Premium Preise",
                f"**Quelle:** {src}\n\n"
                f"• **14 Tage** — {format_price(prices['price_14'])} "
                f"/ **{format_credits(prices['credits_14'])} Credits** "
                f"→ {config.SCAN_PREMIUM_DAILY}/Tag\n"
                f"• **30 Tage** — {format_price(prices['price_30'])} "
                f"/ **{format_credits(prices['credits_30'])} Credits** "
                f"→ unbegrenzt\n\n"
                "Setzen: `/scanprices price_14:… price_30:…` "
                "(optional `credits_14` / `credits_30`).\n"
                "Zurücksetzen: `/scanprices reset:True`\n"
                "Danach `/scanpanel` neu posten, damit das Panel die Preise zeigt.",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(ScannerCog(bot))
