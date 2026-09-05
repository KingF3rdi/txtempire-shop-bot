from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import (
    order_cart_panel_embed,
    order_ref,
    payment_info_embed,
    success_embed,
)
from config import DEFAULT_PAYEE, PAYMENT_NOTICE
from views.ticket_views import TicketOrderView

if TYPE_CHECKING:
    from bot import ShopBot


async def _send_with_retry(
    channel: discord.TextChannel,
    *,
    attempts: int = 4,
    **kwargs,
) -> discord.Message:
    """Sendet direkt nach Channel-Erstellung oft erst nach kurzer Wartezeit."""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            return await channel.send(**kwargs)
        except (discord.Forbidden, discord.HTTPException) as e:
            last_err = e
            await asyncio.sleep(0.6 * (i + 1))
    assert last_err is not None
    raise last_err


async def create_order_ticket(
    bot: ShopBot,
    interaction: discord.Interaction,
    *,
    cart_rows: list[dict] | None = None,
    clear_cart: bool = True,
    credits_enabled: bool = False,
    order_kind: str = "shop",
    credits_amount: float | None = None,
    source_panel_slot: int | None = None,
) -> discord.TextChannel:
    """Erstellt Order + privates Ticket.

    cart_rows: optional vorbereitete Positionen (z.B. Daily Deal mit Rabattpreis).
    Wenn None, wird der normale Warenkorb verwendet.
    """
    guild = interaction.guild
    if guild is None:
        raise ValueError("Nur auf einem Server nutzbar.")

    me = guild.me
    if me is None:
        raise ValueError("Bot-Mitgliedschaft auf dem Server nicht gefunden.")

    settings = await bot.db.ensure_guild(guild.id)
    max_tickets = int(settings.get("max_open_tickets") or 1)
    open_count = await bot.db.count_open_orders(guild.id, interaction.user.id)
    if open_count >= max_tickets:
        raise ValueError(
            f"Ticket-Limit erreicht ({max_tickets} offene Bestellung(en)). "
            "Schließe oder warte auf bestehende Tickets."
        )

    cart = cart_rows if cart_rows is not None else await bot.db.cart_get(
        interaction.user.id, guild.id
    )
    if not cart:
        raise ValueError("Dein Warenkorb ist leer.")

    category_id = settings.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None

    staff_role_id = settings.get("staff_role_id")
    staff_role = guild.get_role(int(staff_role_id)) if staff_role_id else None

    bot_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        manage_channels=True,
        manage_messages=True,
    )
    buyer_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        attach_files=True,
        embed_links=True,
        read_message_history=True,
    )
    staff_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        attach_files=True,
        embed_links=True,
        read_message_history=True,
        manage_messages=True,
    )

    overwrites: dict[
        discord.Role | discord.Member | discord.Object,
        discord.PermissionOverwrite,
    ] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: bot_perms,
    }
    # Buyer muss Member sein für Overwrites
    buyer = interaction.user
    if isinstance(buyer, discord.Member):
        overwrites[buyer] = buyer_perms
    if staff_role:
        overwrites[staff_role] = staff_perms

    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in interaction.user.name.lower()
    )[:20]
    order_id = await bot.db.create_order(
        guild.id,
        interaction.user.id,
        cart,
        ticket_channel_id=None,
        credits_enabled=credits_enabled,
        order_kind=order_kind,
        credits_amount=credits_amount,
        source_panel_slot=source_panel_slot,
    )
    order = await bot.db.get_order(order_id)
    assert order is not None
    seq = int(order.get("order_number") or order_id)
    prefix = (
        "credits"
        if order_kind == "credits"
        else "scanprem"
        if order_kind == "scan_premium"
        else "order"
    )
    channel_name = f"{prefix}-{seq:04d}-{safe_name}"[:100]
    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Shop Kauf {order_ref(order)} von {interaction.user}",
        )
    except discord.Forbidden as e:
        await bot.db.update_order(order_id, status="cancelled")
        raise ValueError(
            "Bot darf keinen Ticket-Channel erstellen. "
            "Recht „Kanäle verwalten“ in der Ticket-Kategorie prüfen."
        ) from e
    except discord.HTTPException as e:
        await bot.db.update_order(order_id, status="cancelled")
        raise ValueError(f"Channel konnte nicht erstellt werden: {e}") from e

    await bot.db.update_order(order_id, ticket_channel_id=channel.id)
    if clear_cart:
        await bot.db.cart_clear(interaction.user.id, guild.id)

    # Rechte nochmal explizit setzen (Kategorie-Sync überschreibt oft)
    try:
        await channel.set_permissions(me, overwrite=bot_perms)
        if isinstance(buyer, discord.Member):
            await channel.set_permissions(buyer, overwrite=buyer_perms)
        if staff_role:
            await channel.set_permissions(staff_role, overwrite=staff_perms)
    except discord.HTTPException:
        pass

    items = await bot.db.get_order_items(order_id)

    await asyncio.sleep(0.5)

    # 1) Zahlungsinfos ganz oben
    try:
        from utils.ticket_faq import money_log_hint_enabled

        await _send_with_retry(
            channel,
            embed=payment_info_embed(
                order,
                settings,
                money_log_hint=money_log_hint_enabled(settings),
            ),
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        # Fallback ohne Embed
        try:
            a = settings.get("payee_a_label") or DEFAULT_PAYEE
            details = settings.get("payee_a_details") or "—"
            await _send_with_retry(
                channel,
                content=(
                    f"**Zahlungsinformationen** (Bestellung {order_ref(order)})\n"
                    f"**{PAYMENT_NOTICE}**\n"
                    f"Gesamt an {a}: **{order['total']}**\n"
                    f"{details}"
                )[:1900],
            )
        except (discord.Forbidden, discord.HTTPException) as e2:
            raise ValueError(
                f"Ticket erstellt ({channel.mention}), aber Bot darf dort "
                f"keine Nachrichten senden: {e2}\n"
                "Bot-Rolle höher schieben und „Nachrichten senden“ + "
                "„Links einbetten“ in der Ticket-Kategorie erlauben."
            ) from e

    # 2) Warenkorb-Panel für Admin + Käufer
    mention = staff_role.mention if staff_role else "Staff"
    cart_panel = order_cart_panel_embed(
        order, items, settings, interaction.user, guild
    )
    show_fast_buy = bool(credits_enabled) and order_kind == "shop"
    ticket_view = TicketOrderView(bot, show_fast_buy=show_fast_buy)

    credits_hint = ""
    if order_kind == "credits":
        credits_hint = (
            "\n🪙 **Credits-Kauf** — nach Staff-Bestätigung werden Credits "
            "gutgeschrieben."
        )
    elif order_kind == "scan_premium":
        from utils.scan_prices import premium_scan_label

        days = int(float(credits_amount or 14))
        credits_hint = (
            f"\n⭐ **Scan Premium ({days} Tage)** — nach Bestätigung "
            f"**{premium_scan_label(days=days)}**."
        )
    elif show_fast_buy:
        from utils.credits import credits_needed_for_total, format_credits

        need = credits_needed_for_total(float(order["total"]))
        bal = await bot.db.get_credits(guild.id, interaction.user.id)
        credits_hint = (
            f"\n🪙 **Quick Buy verfügbar** — kostet **{format_credits(need)} Credits** "
            f"(Guthaben: {format_credits(bal)})."
        )

    mc_link = await bot.db.get_mc_link(guild.id, interaction.user.id)
    if mc_link and int(settings.get("mc_auto_confirm") if settings.get("mc_auto_confirm") is not None else 1):
        credits_hint += (
            f"\n🔗 **MC verknüpft als `{mc_link['ign']}`** — "
            "bei korrekter Ingame-Zahlung wird dieses Ticket **automatisch bestätigt**."
        )
    elif not mc_link:
        credits_hint += (
            "\n💡 Tipp: Mit **Account verlinken** (MC-Link-Panel) wird dein Kauf "
            "nach der Zahlung automatisch bestätigt."
        )

    try:
        await _send_with_retry(
            channel,
            content=(
                f"{interaction.user.mention} {mention} — neue Bestellung {order_ref(order)}!\n"
                f"**{PAYMENT_NOTICE}**\n"
                "Admin sieht den Warenkorb · "
                "Käufer: **Bestellung anzeigen** / **Kauf abbrechen**."
                f"{credits_hint}"
            ),
            embed=cart_panel,
            view=ticket_view,
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        raise ValueError(
            f"Zahlungsinfo gesendet, Warenkorb-Panel fehlgeschlagen: {e}"
        ) from e

    return channel


class TicketsCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Erste 3 Kunden-Fragen im Ticket beantworten, sonst Staff pingen."""
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        content = (message.content or "").strip()
        has_attach = bool(message.attachments)

        order = await self.bot.db.get_order_by_channel(message.channel.id)
        service = None
        ticket_owner_id: int | None = None
        faq_turns = 0
        ticket_kind = ""

        if order is not None:
            if str(order.get("status") or "") in ("completed", "cancelled"):
                return
            ticket_owner_id = int(order["user_id"])
            faq_turns = int(order.get("faq_turns") or 0)
            ticket_kind = "order"
        else:
            service = await self.bot.db.get_service_ticket_by_channel(
                message.channel.id
            )
            if service is None:
                return
            if str(service.get("status") or "") != "open":
                return
            ticket_owner_id = int(service["user_id"])
            faq_turns = int(service.get("faq_turns") or 0)
            ticket_kind = "service"

        # Nur Ticket-Ersteller (nicht Staff)
        author = message.author
        if not isinstance(author, discord.Member):
            return
        if author.id != ticket_owner_id:
            return
        if author.guild_permissions.administrator:
            return
        settings = await self.bot.db.ensure_guild(message.guild.id)
        staff_id = settings.get("staff_role_id")
        if staff_id and any(r.id == int(staff_id) for r in author.roles):
            return

        from utils.embeds import base_embed, warn_embed
        from utils.ticket_faq import (
            MAX_FAQ_TURNS,
            MONEY_LOG_HINT,
            faq_cooldown_ok,
            looks_like_question,
            match_faq,
            money_log_hint_enabled,
        )

        money_on = money_log_hint_enabled(settings)

        # Nur Bild ohne Text → Money-Log Hinweis (zählt nicht als Frage-Turn)
        if not content and has_attach:
            if money_on and faq_cooldown_ok(message.channel.id):
                try:
                    await message.channel.send(
                        embed=base_embed("Money-Log", MONEY_LOG_HINT),
                        reference=message,
                        mention_author=False,
                    )
                except discord.HTTPException:
                    pass
            return

        if not content:
            return
        if faq_turns >= MAX_FAQ_TURNS:
            return
        if not looks_like_question(content) and not has_attach:
            # kurze Bestätigungen ignorieren
            low = content.lower().strip()
            if low in ("ok", "okay", "danke", "thx", "thanks", "jo", "ja", "nein"):
                return
            # trotzdem zählen wenn längerer Text
            if len(content) < 6:
                return

        if not faq_cooldown_ok(message.channel.id):
            return

        # Turn erhöhen
        new_turns = faq_turns + 1
        if ticket_kind == "order" and order is not None:
            await self.bot.db.update_order(int(order["id"]), faq_turns=new_turns)
        elif service is not None:
            await self.bot.db.update_service_ticket(
                int(service["id"]), faq_turns=new_turns
            )

        answer = match_faq(content)
        staff_role = (
            message.guild.get_role(int(staff_id)) if staff_id else None
        )
        staff_ping = staff_role.mention if staff_role else "Staff"

        try:
            if answer:
                body = answer
                if money_on and "Fullscreen" not in answer and "Money-Log" not in answer:
                    body = f"{answer}\n\n{MONEY_LOG_HINT}"
                await message.channel.send(
                    embed=base_embed(
                        f"Schnelle Hilfe ({new_turns}/{MAX_FAQ_TURNS})",
                        body,
                    ),
                    reference=message,
                    mention_author=False,
                )
            else:
                await message.channel.send(
                    content=staff_ping,
                    embed=warn_embed(
                        f"Staff nötig ({new_turns}/{MAX_FAQ_TURNS})",
                        "Dazu habe ich keine passende Auto-Antwort.\n"
                        f"{staff_ping} — bitte kurz helfen.\n\n"
                        + (f"{MONEY_LOG_HINT}\n\n" if money_on else "")
                        + f"Frage von {author.mention}:\n>>> {content[:500]}",
                    ),
                    reference=message,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
        except discord.HTTPException:
            pass

    order = app_commands.Group(
        name="order",
        description="Bestellung / Ticket per Command steuern",
    )

    @order.command(name="show", description="Warenkorb + Zahlung dieser Bestellung anzeigen")
    async def order_show(self, interaction: discord.Interaction) -> None:
        from views.ticket_views import action_show_order

        await action_show_order(self.bot, interaction, ephemeral=True)

    @order.command(
        name="panel",
        description="Zahlungsinfos + Warenkorb-Panel erneut ins Ticket posten",
    )
    async def order_panel(self, interaction: discord.Interaction) -> None:
        from views.ticket_views import action_post_panel

        await action_post_panel(self.bot, interaction)

    @order.command(
        name="confirm",
        description="Payment bestätigen (Staff) — Rollen + Packs",
    )
    async def order_confirm(self, interaction: discord.Interaction) -> None:
        from views.ticket_views import action_confirm_order

        await action_confirm_order(self.bot, interaction)

    @order.command(
        name="cancel",
        description="Kauf abbrechen (Käufer oder Staff)",
    )
    async def order_cancel(self, interaction: discord.Interaction) -> None:
        from views.ticket_views import action_cancel_order

        await action_cancel_order(self.bot, interaction)

    @order.command(
        name="close",
        description="Ticket schließen und Channel löschen (Staff)",
    )
    @app_commands.describe(
        delete_channel="Channel löschen (Standard: ja). Bei nein nur umbenennen."
    )
    async def order_close(
        self,
        interaction: discord.Interaction,
        delete_channel: bool = True,
    ) -> None:
        from views.ticket_views import action_close_ticket

        await action_close_ticket(
            self.bot, interaction, delete_channel=delete_channel
        )

    @app_commands.command(
        name="ticketlimit",
        description="Max. offene Kauf-Tickets pro User setzen",
    )
    @app_commands.describe(limit="Maximale Anzahl offener Tickets (1–10)")
    @app_commands.default_permissions(manage_guild=True)
    async def ticketlimit(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 10]) -> None:
        assert interaction.guild is not None
        await self.bot.db.update_guild_settings(
            interaction.guild.id, max_open_tickets=int(limit)
        )
        await interaction.response.send_message(
            embed=success_embed("Ticket-Limit", f"Max. offene Tickets pro User: **{limit}**"),
            ephemeral=True,
        )

    @app_commands.command(
        name="resettickets",
        description="Alle abgebrochenen Bestellungen löschen (DB + Rest-Channels)",
    )
    @app_commands.describe(
        delete_channels="Verbliebene Ticket-Channels der Abbrüche löschen (Standard: ja)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def resettickets(
        self,
        interaction: discord.Interaction,
        delete_channels: bool = True,
    ) -> None:
        from utils.embeds import error_embed
        from views.ticket_views import is_staff

        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        cancelled = await self.bot.db.list_cancelled_orders(interaction.guild.id)
        if not cancelled:
            await interaction.followup.send(
                embed=success_embed(
                    "Nichts zu löschen",
                    "Es gibt keine abgebrochenen Bestellungen.",
                ),
                ephemeral=True,
            )
            return

        channel_ids = [
            int(o["ticket_channel_id"])
            for o in cancelled
            if o.get("ticket_channel_id")
        ]
        deleted_db = await self.bot.db.delete_cancelled_orders(interaction.guild.id)

        channels_removed = 0
        if delete_channels and channel_ids:
            for ch_id in channel_ids:
                channel = interaction.guild.get_channel(ch_id)
                if channel is None:
                    continue
                try:
                    await channel.delete(
                        reason=f"/resettickets von {interaction.user}"
                    )
                    channels_removed += 1
                except discord.HTTPException:
                    pass

        body = f"**{deleted_db}** Bestellung(en) aus der DB entfernt."
        if delete_channels:
            body += f"\n**{channels_removed}** Ticket-Channel(s) gelöscht."
        await interaction.followup.send(
            embed=success_embed("Abgebrochene Bestellungen gelöscht", body),
            ephemeral=True,
        )

    @app_commands.command(
        name="supportpanel",
        description="Support-Ticket-Panel posten oder aktualisieren",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def supportpanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed("Kein Channel"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        from views.service_ticket_panel import post_or_refresh_service_panel

        msg = await post_or_refresh_service_panel(
            self.bot, interaction.guild, target, "support"
        )
        await interaction.followup.send(
            embed=success_embed(
                "Support-Panel",
                f"Panel in {target.mention}: {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="bewerbungspanel",
        description="Media/Creator-Bewerbungs-Panel posten oder aktualisieren",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def bewerbungspanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed("Kein Channel"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        from views.service_ticket_panel import post_or_refresh_service_panel

        msg = await post_or_refresh_service_panel(
            self.bot, interaction.guild, target, "application"
        )
        await interaction.followup.send(
            embed=success_embed(
                "Media/Creator-Bewerbungs-Panel",
                f"Panel in {target.mention}: {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="partnerpanel",
        description="Discord-Partner-Ticket-Panel posten oder aktualisieren",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def partnerpanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed("Kein Channel"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        from views.service_ticket_panel import post_or_refresh_service_panel

        msg = await post_or_refresh_service_panel(
            self.bot, interaction.guild, target, "partner"
        )
        await interaction.followup.send(
            embed=success_embed(
                "Partner-Panel",
                f"Panel in {target.mention}: {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="texturepackpanel",
        description="Texturepack Ankauf/Tausch-Panel posten oder aktualisieren",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def texturepackpanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed("Kein Channel"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        from views.service_ticket_panel import post_or_refresh_service_panel

        msg = await post_or_refresh_service_panel(
            self.bot, interaction.guild, target, "texturepack"
        )
        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        role_id = settings.get("texturepack_role_id")
        role = (
            interaction.guild.get_role(int(role_id)) if role_id else None
        )
        role_note = (
            f"\nRolle bei Annahme: {role.mention}"
            if role
            else "\n⚠️ Noch keine Annahme-Rolle — setze mit `/texturepackrole`."
        )
        await interaction.followup.send(
            embed=success_embed(
                "Texturepack-Panel",
                f"Panel in {target.mention}: {msg.jump_url}{role_note}\n"
                "Ankauf & Tausch sind für alle verfügbar.",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="texturepackrole",
        description="Rolle die bei Ankauf/Tausch-Annahme vergeben wird",
    )
    @app_commands.describe(
        role="Rolle die User bei Staff-Annahme erhalten (leer = anzeigen)",
        clear="Rolle entfernen",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def texturepackrole(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
        clear: bool = False,
    ) -> None:
        assert interaction.guild is not None
        if clear:
            await self.bot.db.update_guild_settings(
                interaction.guild.id, texturepack_role_id=None
            )
            await interaction.response.send_message(
                embed=success_embed(
                    "Texturepack-Rolle",
                    "Annahme-Rolle entfernt. Ankauf/Tausch bleiben für alle offen; "
                    "bei Annahme wird keine Rolle mehr vergeben.",
                ),
                ephemeral=True,
            )
            return
        if role is None:
            settings = await self.bot.db.ensure_guild(interaction.guild.id)
            rid = settings.get("texturepack_role_id")
            current = interaction.guild.get_role(int(rid)) if rid else None
            body = (
                f"Aktuell: {current.mention}"
                if current
                else "Aktuell: **nicht gesetzt**"
            )
            await interaction.response.send_message(
                embed=success_embed(
                    "Texturepack-Rolle",
                    f"{body}\n\nWird bei **Tausch annehmen** vergeben.\n"
                    "Setzen: `/texturepackrole role:@…`",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild_settings(
            interaction.guild.id, texturepack_role_id=role.id
        )
        # Panel-Text aktualisieren falls vorhanden
        from views.service_ticket_panel import post_or_refresh_service_panel

        row = await self.bot.db.get_service_panel(
            interaction.guild.id, "texturepack"
        )
        panel_note = ""
        if row and row.get("channel_id") and row.get("message_id"):
            ch = interaction.guild.get_channel(int(row["channel_id"]))
            if isinstance(ch, discord.TextChannel):
                try:
                    await post_or_refresh_service_panel(
                        self.bot, interaction.guild, ch, "texturepack"
                    )
                    panel_note = f"\nPanel in {ch.mention} aktualisiert."
                except Exception:
                    panel_note = "\nPanel konnte nicht auto-aktualisiert werden."
        await interaction.response.send_message(
            embed=success_embed(
                "Texturepack-Rolle",
                f"Bei Annahme erhalten User {role.mention}."
                f"{panel_note}",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="ticketfaq",
        description="Ticket-Auto-Hilfe: Money-Log-Hinweis an/aus (Staff)",
    )
    @app_commands.describe(
        money_log="Fullscreen Money-Log Hinweis in Tickets (Standard: an)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def ticketfaq(
        self,
        interaction: discord.Interaction,
        money_log: bool | None = None,
    ) -> None:
        assert interaction.guild is not None
        from views.ticket_views import is_staff

        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        if money_log is not None:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                ticket_money_log_hint=1 if money_log else 0,
            )
            settings = await self.bot.db.ensure_guild(interaction.guild.id)
        on = int(settings.get("ticket_money_log_hint") or 1) != 0
        await interaction.response.send_message(
            embed=success_embed(
                "Ticket-FAQ",
                f"**Money-Log-Hinweis:** {'an' if on else 'aus'}\n"
                f"Auto-Hilfe: erste **3** Kunden-Fragen beantworten, "
                f"sonst Staff-Ping.\n"
                f"Ändern: `/ticketfaq money_log:True|False`\n\n"
                f"Hinweis: **Message Content Intent** muss im Developer "
                f"Portal aktiv sein.",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(TicketsCog(bot))
