from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

import config
from utils.archive_scanner import is_scannable_filename, scan_archive_bytes
from utils.credits import format_credits
from utils.embeds import base_embed, error_embed, format_price, success_embed, warn_embed
from utils.scan_limits import consume_scan_quota, get_scan_quota
from utils.scan_premium_role import post_scan_log, sync_scan_premium_role
from utils.scan_prices import get_scan_prices, premium_scan_label
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


async def build_scan_panel_embed(bot: ShopBot, guild_id: int) -> discord.Embed:
    prices = await get_scan_prices(bot, guild_id)
    embed = base_embed(
        "File Scanner",
        "Scanne **ZIP / RAR / JAR** auf RATs, Stealer und verdächtige Dateien.\n\n"
        f"• Free: **{config.SCAN_FREE_DAILY} Scan/Tag**\n"
        f"• 14 Tage Premium: **{config.SCAN_PREMIUM_DAILY} Scans/Tag**\n"
        f"• 30 Tage Premium: **unbegrenzte Scans**\n\n"
        "**Datei hier droppen** — ohne DM, direkt im Channel\n"
        "**URL scannen** — Download-Link (Discord-CDN, Dropbox `dl=1`, Drive)\n"
        "Auch: `/scan url` · `/scan file`\n"
        "Ergebnis privat (ephemeral / DM). Erfolgreiche Scans landen im Log-Channel.\n\n"
        "⚠️ **Keine 100 %-Garantie** — Heuristik, kein vollständiger Virenscan.",
    )
    embed.add_field(
        name="Premium",
        value=(
            f"14 Tage — {format_price(prices['price_14'])} "
            f"oder **{format_credits(prices['credits_14'])} Credits** "
            f"({config.SCAN_PREMIUM_DAILY}/Tag)\n"
            f"30 Tage — {format_price(prices['price_30'])} "
            f"oder **{format_credits(prices['credits_30'])} Credits** "
            f"(unbegrenzt)"
        ),
        inline=False,
    )
    embed.set_footer(text="Auch möglich: /scan file · Keine 100%-Garantie auf „safe“")
    return embed


async def _dm_user(user: discord.abc.User) -> discord.DMChannel | None:
    try:
        return await user.create_dm()
    except discord.HTTPException:
        return None


async def _send_scan_result_dm(
    user: discord.abc.User,
    *,
    filename: str,
    result_summary: str,
    is_clean: bool,
    is_blocked: bool,
    quota_footer: str,
) -> bool:
    dm = await _dm_user(user)
    if dm is None:
        return False
    if is_clean:
        embed = success_embed(f"Scan: {filename}", result_summary)
    elif is_blocked:
        embed = warn_embed(f"⛔ Kritisch: {filename}", result_summary)
        embed.color = discord.Color.dark_red()
    else:
        embed = warn_embed(f"Scan: {filename}", result_summary)
    embed.set_footer(text=quota_footer)
    try:
        await dm.send(embed=embed)
    except discord.HTTPException:
        return False
    return True


async def deliver_scan_result(
    bot: ShopBot,
    interaction: discord.Interaction,
    *,
    data: bytes,
    filename: str,
    guild_id: int | None = None,
    reply_via_dm: discord.DMChannel | None = None,
) -> None:
    """Quota, Scan, User-Antwort + optional Scan-Log-Channel."""
    gid = guild_id or (interaction.guild.id if interaction.guild else None)
    if gid is None:
        target = reply_via_dm or interaction.followup
        if reply_via_dm:
            await reply_via_dm.send(embed=error_embed("Nur auf dem Server"))
        else:
            await interaction.followup.send(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
        return

    staff = await is_staff(bot, interaction)
    try:
        quota = await consume_scan_quota(
            bot, gid, interaction.user.id, is_staff=staff
        )
    except ValueError as e:
        if reply_via_dm:
            await reply_via_dm.send(embed=error_embed("Scan-Limit", str(e)))
        else:
            await interaction.followup.send(
                embed=error_embed("Scan-Limit", str(e)), ephemeral=True
            )
        return

    result = scan_archive_bytes(data, filename)
    footer = (
        f"Scans heute: {quota['used']}/{quota['limit']}"
        if not staff
        else "Staff — kein Limit"
    )

    from utils.scan_stats import log_scan_result

    await log_scan_result(bot, gid, interaction.user.id, result)

    if result.is_clean:
        embed = success_embed(f"Scan: {filename}", result.summary())
    elif result.is_blocked:
        embed = warn_embed(f"⛔ Kritisch: {filename}", result.summary())
        embed.color = discord.Color.dark_red()
    else:
        embed = warn_embed(f"Scan: {filename}", result.summary())
    embed.set_footer(text=footer)

    if reply_via_dm:
        await reply_via_dm.send(embed=embed)
    else:
        sent = await _send_scan_result_dm(
            interaction.user,
            filename=filename,
            result_summary=result.summary(),
            is_clean=result.is_clean,
            is_blocked=result.is_blocked,
            quota_footer=footer,
        )
        if sent:
            await interaction.followup.send(
                embed=success_embed(
                    "Scan fertig",
                    f"**{filename}** — Ergebnis per **DM**.\n_{footer}_",
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    guild = bot.get_guild(gid)
    if guild is not None and result.is_clean:
        await post_scan_log(
            bot,
            guild,
            user=interaction.user,
            filename=filename,
            summary=result.summary(),
            is_clean=True,
            is_blocked=False,
        )
    elif guild is not None and (result.is_blocked or not result.is_clean):
        # kritische / verdächtige Scans ebenfalls loggen
        await post_scan_log(
            bot,
            guild,
            user=interaction.user,
            filename=filename,
            summary=result.summary(),
            is_clean=False,
            is_blocked=result.is_blocked,
        )


# Backwards-compatible alias
run_scan_and_dm = deliver_scan_result


class ScanUrlModal(discord.ui.Modal, title="Datei-URL scannen"):
    url = discord.ui.TextInput(
        label="Datei-URL",
        placeholder="https://cdn.discordapp.com/…/file.zip",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(self, bot: ShopBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.url.value).strip()
        await interaction.response.defer(ephemeral=True)

        from utils.scan_download import download_archive_from_url

        try:
            data, path_name = await download_archive_from_url(raw)
        except ValueError as e:
            await interaction.followup.send(
                embed=error_embed("URL-Scan fehlgeschlagen", str(e)[:800]),
                ephemeral=True,
            )
            return
        except Exception as e:
            await interaction.followup.send(
                embed=error_embed(
                    "Download fehlgeschlagen",
                    f"`{type(e).__name__}: {e}`"[:800],
                ),
                ephemeral=True,
            )
            return

        await deliver_scan_result(
            self.bot,
            interaction,
            data=data,
            filename=path_name,
            guild_id=self.guild_id,
        )


class ScanPanelView(discord.ui.View):
    """Persistentes Scan-Panel."""

    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _quota_ok(self, interaction: discord.Interaction) -> bool:
        assert interaction.guild is not None
        staff = await is_staff(self.bot, interaction)
        quota = await get_scan_quota(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            is_staff=staff,
        )
        if not staff and quota["remaining"] <= 0:
            await interaction.response.send_message(
                embed=error_embed(
                    "Scan-Limit",
                    f"Tageslimit erreicht ({quota['used']}/{quota['limit']}).\n"
                    "Premium: Button **Premium kaufen**.",
                ),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Datei hier droppen",
        style=discord.ButtonStyle.success,
        custom_id="scanpanel:channel",
        emoji="📎",
        row=0,
    )
    async def scan_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Nur in Text-Channels"), ephemeral=True
            )
            return
        if not await self._quota_ok(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        user_id = interaction.user.id
        guild_id = interaction.guild.id

        await interaction.followup.send(
            embed=success_embed(
                "Datei droppen",
                f"{interaction.user.mention}: Sende jetzt eine "
                "**`.zip` / `.rar` / `.jar`** Datei **in diesen Channel** "
                "(innerhalb von **2 Minuten**).\n"
                "Ergebnis kommt privat zu dir.",
            ),
            ephemeral=True,
        )

        def check(message: discord.Message) -> bool:
            return (
                message.author.id == user_id
                and message.channel.id == channel.id
                and bool(message.attachments)
            )

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=120.0)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=error_embed(
                    "Zeit abgelaufen",
                    "Keine Datei erhalten. Bitte erneut starten.",
                ),
                ephemeral=True,
            )
            return

        att = msg.attachments[0]
        if not is_scannable_filename(att.filename):
            await interaction.followup.send(
                embed=error_embed(
                    "Falscher Dateityp",
                    "Bitte **.zip / .rar / .jar** senden.",
                ),
                ephemeral=True,
            )
            return

        try:
            data = await att.read()
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed("Download fehlgeschlagen", str(e)[:400]),
                ephemeral=True,
            )
            return

        try:
            await msg.add_reaction("✅")
        except discord.HTTPException:
            pass

        await deliver_scan_result(
            self.bot,
            interaction,
            data=data,
            filename=att.filename or "archive",
            guild_id=guild_id,
        )

    @discord.ui.button(
        label="Per DM scannen",
        style=discord.ButtonStyle.secondary,
        custom_id="scanpanel:dm",
        emoji="📩",
        row=0,
    )
    async def scan_dm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return

        if not await self._quota_ok(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        dm = await _dm_user(interaction.user)
        if dm is None:
            await interaction.followup.send(
                embed=error_embed(
                    "DMs geschlossen",
                    "Nutze **Datei hier droppen** oder **URL scannen**.",
                ),
                ephemeral=True,
            )
            return

        try:
            await dm.send(
                embed=success_embed(
                    "Datei zum Scannen",
                    "Sende jetzt eine **.zip / .rar / .jar** Datei "
                    "per Drag & Drop in diesen Chat "
                    "(innerhalb von **2 Minuten**).",
                )
            )
        except discord.HTTPException:
            await interaction.followup.send(
                embed=error_embed("DM fehlgeschlagen"),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=success_embed(
                "DM geöffnet",
                "Schau in deine **Direktnachrichten** und sende die Datei.",
            ),
            ephemeral=True,
        )

        guild_id = interaction.guild.id
        user_id = interaction.user.id

        def check(message: discord.Message) -> bool:
            return (
                message.author.id == user_id
                and isinstance(message.channel, discord.DMChannel)
                and bool(message.attachments)
            )

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=120.0)
        except asyncio.TimeoutError:
            try:
                await dm.send(
                    embed=error_embed(
                        "Zeit abgelaufen",
                        "Keine Datei erhalten. Bitte erneut über das Panel starten.",
                    )
                )
            except discord.HTTPException:
                pass
            return

        att = msg.attachments[0]
        if not is_scannable_filename(att.filename):
            await dm.send(
                embed=error_embed(
                    "Falscher Dateityp",
                    "Bitte **.zip / .rar / .jar** senden.",
                )
            )
            return

        try:
            data = await att.read()
        except discord.HTTPException as e:
            await dm.send(
                embed=error_embed("Download fehlgeschlagen", str(e)[:400])
            )
            return

        await deliver_scan_result(
            self.bot,
            interaction,
            data=data,
            filename=att.filename or "archive",
            guild_id=guild_id,
            reply_via_dm=dm,
        )

    @discord.ui.button(
        label="URL scannen",
        style=discord.ButtonStyle.primary,
        custom_id="scanpanel:url",
        emoji="🔗",
        row=0,
    )
    async def scan_url(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        await interaction.response.send_modal(
            ScanUrlModal(self.bot, interaction.guild.id)
        )

    @discord.ui.button(
        label="Mein Status",
        style=discord.ButtonStyle.secondary,
        custom_id="scanpanel:status",
        emoji="📊",
        row=1,
    )
    async def status(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
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
            body = f"**Staff** — kein Limit.\nHeute genutzt: **{quota['used']}**"
        elif quota["premium"] and quota.get("unlimited"):
            body = (
                f"**Premium** bis `{quota['expires_at']}`\n"
                f"**Unbegrenzte Scans**\n"
                f"Heute genutzt: **{quota['used']}**"
            )
        elif quota["premium"]:
            body = (
                f"**Premium** bis `{quota['expires_at']}`\n"
                f"**{config.SCAN_PREMIUM_DAILY} Scans/Tag**\n"
                f"Heute: **{quota['used']}/{quota['limit']}** "
                f"(noch {quota['remaining']})"
            )
        else:
            body = (
                f"**Free** — {config.SCAN_FREE_DAILY}/Tag\n"
                f"Heute: **{quota['used']}/{quota['limit']}** "
                f"(noch {quota['remaining']})"
            )
        await interaction.response.send_message(
            embed=success_embed("Scan-Status", body),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Premium kaufen",
        style=discord.ButtonStyle.primary,
        custom_id="scanpanel:premium",
        emoji="⭐",
        row=1,
    )
    async def premium(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        prices = await get_scan_prices(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed(
                "Scan Premium",
                f"• **14 Tage** — {format_price(prices['price_14'])} / "
                f"{format_credits(prices['credits_14'])} Credits → "
                f"**{config.SCAN_PREMIUM_DAILY}/Tag**\n"
                f"• **30 Tage** — {format_price(prices['price_30'])} / "
                f"{format_credits(prices['credits_30'])} Credits → "
                f"**unbegrenzt**\n\n"
                "Wähle Dauer und Zahlungsart:",
            ),
            view=ScanPremiumPanelBuyView(self.bot),
            ephemeral=True,
        )


class ScanPremiumPanelBuyView(discord.ui.View):
    """14/30 Tage — Zahlung (Ticket) oder Credits."""

    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(
        label="14 Tage · Zahlen",
        style=discord.ButtonStyle.primary,
        emoji="💳",
        row=0,
    )
    async def pay_14(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from cogs.scanner import open_scan_premium_ticket

        await open_scan_premium_ticket(self.bot, interaction, days=14)

    @discord.ui.button(
        label="14 Tage · Credits",
        style=discord.ButtonStyle.success,
        emoji="🪙",
        row=0,
    )
    async def credits_14(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await buy_scan_premium_with_credits(self.bot, interaction, days=14)

    @discord.ui.button(
        label="30 Tage · Zahlen",
        style=discord.ButtonStyle.primary,
        emoji="💳",
        row=1,
    )
    async def pay_30(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from cogs.scanner import open_scan_premium_ticket

        await open_scan_premium_ticket(self.bot, interaction, days=30)

    @discord.ui.button(
        label="30 Tage · Credits",
        style=discord.ButtonStyle.success,
        emoji="🪙",
        row=1,
    )
    async def credits_30(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await buy_scan_premium_with_credits(self.bot, interaction, days=30)


async def buy_scan_premium_with_credits(
    bot: ShopBot, interaction: discord.Interaction, *, days: int
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return
    days = 14 if days not in (14, 30) else days
    prices = await get_scan_prices(bot, interaction.guild.id)
    need = prices["credits_14"] if days == 14 else prices["credits_30"]
    need = round(float(need), 2)
    bal = await bot.db.get_credits(interaction.guild.id, interaction.user.id)
    if bal < need:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error_embed(
                    "Zu wenig Credits",
                    f"Benötigt: **{format_credits(need)}** · "
                    f"Guthaben: **{format_credits(bal)}**\n"
                    "Credits am Buy-Panel kaufen oder **Zahlen** wählen.",
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=error_embed(
                    "Zu wenig Credits",
                    f"Benötigt: **{format_credits(need)}** · "
                    f"Guthaben: **{format_credits(bal)}**",
                ),
                ephemeral=True,
            )
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    ok = await bot.db.try_deduct_credits(
        interaction.guild.id, interaction.user.id, need
    )
    if not ok:
        await interaction.followup.send(
            embed=error_embed("Zu wenig Credits", "Abzug fehlgeschlagen."),
            ephemeral=True,
        )
        return

    expires = await bot.db.extend_scan_premium(
        interaction.guild.id, interaction.user.id, days
    )
    role_note = ""
    role_status = await sync_scan_premium_role(
        bot, interaction.guild, interaction.user.id, force_grant=True
    )
    if role_status:
        role_note = f"\nRolle: {role_status}"
    new_bal = await bot.db.get_credits(interaction.guild.id, interaction.user.id)
    await interaction.followup.send(
        embed=success_embed(
            "Premium aktiviert",
            f"**{days} Tage** Scan Premium (per Credits)\n"
            f"−**{format_credits(need)}** Credits · Rest: **{format_credits(new_bal)}**\n"
            f"Aktiv bis `{expires}` · "
            f"**{premium_scan_label(days=days)}**{role_note}",
        ),
        ephemeral=True,
    )


async def post_or_refresh_scan_panel(
    bot: ShopBot,
    guild: discord.Guild,
    channel: discord.TextChannel,
    *,
    force_new: bool = False,
) -> discord.Message:
    """Posted oder editiert das Scan-Panel (kein Auto-Neu-Post außer force_new)."""
    embed = await build_scan_panel_embed(bot, guild.id)
    view = ScanPanelView(bot)
    row = await bot.db.get_scan_panel(guild.id)
    if (
        not force_new
        and row
        and row.get("channel_id")
        and row.get("message_id")
        and int(row["channel_id"]) == channel.id
    ):
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            await msg.edit(embed=embed, view=view)
            return msg
        except (discord.NotFound, discord.HTTPException):
            pass
    msg = await channel.send(embed=embed, view=view)
    await bot.db.set_scan_panel(
        guild.id, channel_id=channel.id, message_id=msg.id
    )
    return msg


async def refresh_scan_panel_on_ready(bot: ShopBot) -> list[str]:
    """Nur Edit — nie neu posten beim Bot-Start."""
    lines: list[str] = []
    for gid in await bot.db.list_guilds_with_scan_panel():
        guild = bot.get_guild(gid)
        if guild is None:
            continue
        row = await bot.db.get_scan_panel(gid)
        if not row or not row.get("channel_id") or not row.get("message_id"):
            continue
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            lines.append(f"Scan-Panel guild {gid}: Channel fehlt")
            continue
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
        except discord.NotFound:
            await bot.db.clear_scan_panel_message(gid)
            lines.append(
                f"Scan-Panel {guild.name}: Nachricht fehlt — kein Auto-Post"
            )
            continue
        try:
            await msg.edit(
                embed=await build_scan_panel_embed(bot, gid),
                view=ScanPanelView(bot),
            )
            lines.append(f"Scan-Panel aktualisiert in {channel.mention}")
        except discord.HTTPException as e:
            lines.append(f"Scan-Panel Edit fehlgeschlagen: {e}")
    return lines
