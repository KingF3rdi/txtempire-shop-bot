from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from utils.delivery import deliver_packs
from utils.embeds import (
    error_embed,
    order_cart_panel_embed,
    order_ref,
    order_ticket_embed,
    payment_info_embed,
    purchase_success_embed,
    success_embed,
    warn_embed,
)
from utils.roles import grant_purchase_roles

if TYPE_CHECKING:
    from bot import ShopBot


async def enrich_order_item_roles(bot: ShopBot, order_items: list[dict]) -> list[dict]:
    """Aktualisiert Item-/Kategorie-Rollen aus der live DB vor der Vergabe."""
    enriched: list[dict] = []
    for item in order_items:
        row = dict(item)
        item_id = row.get("item_id")
        if item_id:
            live = await bot.db.get_item(int(item_id))
            if live:
                if live.get("role_id"):
                    row["item_role_id"] = live["role_id"]
                cat_id = live.get("category_id") or row.get("category_id")
                if cat_id:
                    cat = await bot.db.get_category(int(cat_id))
                    if cat and cat.get("role_id"):
                        row["category_role_id"] = cat["role_id"]
        elif row.get("category_id") and not row.get("category_role_id"):
            cat = await bot.db.get_category(int(row["category_id"]))
            if cat and cat.get("role_id"):
                row["category_role_id"] = cat["role_id"]
        enriched.append(row)
    return enriched


async def is_staff(bot: ShopBot, interaction: discord.Interaction) -> bool:
    assert interaction.guild is not None
    if interaction.user.guild_permissions.administrator:  # type: ignore[union-attr]
        return True
    settings = await bot.db.ensure_guild(interaction.guild.id)
    staff_id = settings.get("staff_role_id")
    if not staff_id:
        return False
    member = interaction.user
    if isinstance(member, discord.Member):
        return any(r.id == int(staff_id) for r in member.roles)
    return False


async def is_buyer_or_staff(
    bot: ShopBot, interaction: discord.Interaction, order: dict
) -> bool:
    if interaction.user.id == int(order["user_id"]):
        return True
    return await is_staff(bot, interaction)


async def get_order_for_interaction(
    bot: ShopBot, interaction: discord.Interaction
) -> dict | None:
    return await bot.db.get_order_by_channel(interaction.channel_id)


async def action_show_order(
    bot: ShopBot, interaction: discord.Interaction, *, ephemeral: bool = True
) -> None:
    order = await get_order_for_interaction(bot, interaction)
    if not order:
        await interaction.response.send_message(
            embed=error_embed("Keine Bestellung", "Kein Order für dieses Ticket."),
            ephemeral=True,
        )
        return
    if not await is_buyer_or_staff(bot, interaction, order):
        await interaction.response.send_message(
            embed=error_embed("Keine Berechtigung"), ephemeral=True
        )
        return

    settings = await bot.db.ensure_guild(int(order["guild_id"]))
    items = await bot.db.get_order_items(int(order["id"]))
    buyer: discord.abc.User = interaction.user
    if interaction.guild:
        try:
            buyer = await interaction.guild.fetch_member(int(order["user_id"]))
        except discord.HTTPException:
            pass

    await interaction.response.send_message(
        embeds=[
            payment_info_embed(order, settings),
            order_cart_panel_embed(order, items, settings, buyer, interaction.guild),
        ],
        ephemeral=ephemeral,
    )


async def action_post_panel(bot: ShopBot, interaction: discord.Interaction) -> None:
    """Postet Zahlungsinfos + Warenkorb-Panel öffentlich ins Ticket."""
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            embed=error_embed("Nur in einem Ticket-Channel nutzbar."),
            ephemeral=True,
        )
        return

    order = await get_order_for_interaction(bot, interaction)
    if not order:
        await interaction.response.send_message(
            embed=error_embed("Keine Bestellung", "Kein Order für dieses Ticket."),
            ephemeral=True,
        )
        return
    if not await is_buyer_or_staff(bot, interaction, order):
        await interaction.response.send_message(
            embed=error_embed("Keine Berechtigung"), ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    settings = await bot.db.ensure_guild(int(order["guild_id"]))
    items = await bot.db.get_order_items(int(order["id"]))
    buyer: discord.abc.User = interaction.user
    assert interaction.guild is not None
    try:
        buyer = await interaction.guild.fetch_member(int(order["user_id"]))
    except discord.HTTPException:
        pass

    staff_role_id = settings.get("staff_role_id")
    staff_role = (
        interaction.guild.get_role(int(staff_role_id)) if staff_role_id else None
    )
    mention = staff_role.mention if staff_role else "Staff"

    try:
        await channel.send(embed=payment_info_embed(order, settings))
        await channel.send(
            content=(
                f"{buyer.mention} {mention} — Bestellung **{order_ref(order)}**\n"
                "Commands: `/order show` · `/order confirm` · `/order cancel` · `/order close`"
            ),
            embed=order_cart_panel_embed(
                order, items, settings, buyer, interaction.guild
            ),
            view=TicketOrderView(
                bot,
                show_fast_buy=bool(int(order.get("credits_enabled") or 0))
                and str(order.get("order_kind") or "shop") == "shop",
            ),
        )
    except discord.Forbidden:
        await interaction.followup.send(
            embed=error_embed(
                "Keine Berechtigung",
                "Bot darf hier keine Nachrichten senden "
                "(„Nachrichten senden“ + „Links einbetten“).",
            ),
            ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(
            embed=error_embed("Senden fehlgeschlagen", str(e)[:500]),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=success_embed("Panel gepostet", "Zahlungsinfos + Warenkorb sind im Ticket."),
        ephemeral=True,
    )


async def _delete_channel_later(channel: discord.abc.GuildChannel, delay: float, reason: str) -> None:
    """Löscht einen Channel nach kurzer Zeit (Fehler werden ignoriert)."""
    await asyncio.sleep(delay)
    try:
        await channel.delete(reason=reason)
    except (discord.HTTPException, discord.NotFound):
        pass


async def action_confirm_order(
    bot: ShopBot,
    interaction: discord.Interaction,
    *,
    require_staff: bool = True,
    paid_with_credits: bool = False,
    credits_charged: float | None = None,
) -> None:
    if require_staff and not await is_staff(bot, interaction):
        await interaction.response.send_message(
            embed=error_embed("Keine Berechtigung", "Nur Staff/Admin."),
            ephemeral=True,
        )
        return
    order = await get_order_for_interaction(bot, interaction)
    if not order:
        await interaction.response.send_message(
            embed=error_embed("Keine Bestellung"), ephemeral=True
        )
        return
    if order["status"] == "completed":
        await interaction.response.send_message(
            embed=warn_embed("Bereits bestätigt"), ephemeral=True
        )
        return
    if order["status"] == "cancelled":
        await interaction.response.send_message(
            embed=error_embed("Abgebrochen", "Bestellung wurde abgebrochen."),
            ephemeral=True,
        )
        return

    from utils.credits import credits_needed_for_total, format_credits

    charged = credits_charged
    if paid_with_credits:
        if charged is None:
            charged = credits_needed_for_total(float(order["total"]))

    if not interaction.response.is_done():
        await interaction.response.defer()

    if paid_with_credits and charged is not None:
        ok = await bot.db.try_deduct_credits(
            int(order["guild_id"]), int(order["user_id"]), charged
        )
        if not ok:
            bal = await bot.db.get_credits(
                int(order["guild_id"]), int(order["user_id"])
            )
            await interaction.followup.send(
                embed=error_embed(
                    "Zu wenig Credits",
                    f"Benötigt: **{format_credits(charged)}** · "
                    f"Guthaben: **{format_credits(bal)}**\n"
                    "Kaufe Credits über den **Credits**-Button auf dem Panel.",
                ),
                ephemeral=True,
            )
            return

    assert interaction.guild is not None
    settings = await bot.db.ensure_guild(interaction.guild.id)
    order_items = await bot.db.get_order_items(int(order["id"]))
    order_items = await enrich_order_item_roles(bot, order_items)
    order_kind = str(order.get("order_kind") or "shop")

    update_fields: dict = {
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    if paid_with_credits:
        update_fields["paid_with_credits"] = 1
    await bot.db.update_order(int(order["id"]), **update_fields)

    # Credits-Kauf: Guthaben gutschreiben statt Packs
    credits_granted: float | None = None
    credits_balance = None
    if order_kind == "credits":
        amount = float(order.get("credits_amount") or 0)
        if amount <= 0:
            from utils.credits import currency_to_credits

            amount = currency_to_credits(float(order["total"]))
        credits_balance = await bot.db.add_credits(
            int(order["guild_id"]), int(order["user_id"]), amount
        )
        credits_granted = amount

    member = None
    try:
        member = await interaction.guild.fetch_member(int(order["user_id"]))
    except discord.HTTPException:
        member = None

    role_result: dict = {"granted": [], "skipped": [], "failed": []}
    delivery_info: dict = {}
    if member and order_kind != "credits":
        role_result = await grant_purchase_roles(member, settings, order_items)
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            delivery_info = await deliver_packs(member, channel, order_items)
    elif member and order_kind == "credits":
        role_result = await grant_purchase_roles(member, settings, [])
    elif not member:
        role_result["failed"].append(
            "Käufer nicht auf dem Server — Rollen konnten nicht vergeben werden."
        )

    buyer: discord.abc.User = member or interaction.user
    if member is None:
        try:
            buyer = await bot.fetch_user(int(order["user_id"]))
        except discord.HTTPException:
            buyer = interaction.user

    order = await bot.db.get_order(int(order["id"])) or order
    success = purchase_success_embed(order, order_items, buyer, role_result)

    extra_parts: list[str] = []
    if credits_granted is not None:
        extra_parts.append(
            f"🪙 **{format_credits(credits_granted)} Credits** gutgeschrieben "
            f"(Guthaben jetzt: **{format_credits(credits_balance or 0)}**)."
        )
    if paid_with_credits and charged is not None:
        bal = await bot.db.get_credits(int(order["guild_id"]), int(order["user_id"]))
        extra_parts.append(
            f"⚡ Quick Buy: **{format_credits(charged)} Credits** abgezogen "
            f"(Rest: **{format_credits(bal)}**)."
        )
    if delivery_info.get("dm_sent"):
        extra_parts.append("Pack-DM gesendet.")
    if delivery_info.get("files_sent"):
        extra_parts.append("Pack-Datei(en) gesendet.")
    if delivery_info.get("links_posted"):
        extra_parts.append("Pack-Links im Ticket gepostet.")
    if order_kind != "credits" and not any(
        role_result.get(k) for k in ("granted", "skipped", "failed")
    ):
        extra_parts.append(
            "Keine Autorole hinterlegt — setze sie mit `/item setrole` "
            "oder Admin-Panel → Item → **Autorole**."
        )
    if order_kind != "credits":
        extra_parts.append("Käufer kann einmalig `/vouch` nutzen.")
    extra_parts.append("⏳ Dieses Ticket wird in 5 Sekunden automatisch gelöscht.")
    if extra_parts:
        success.add_field(
            name="Lieferung / Hinweise",
            value="\n".join(extra_parts),
            inline=False,
        )

    await interaction.followup.send(embed=success)

    if order_kind != "credits":
        from utils.vouch_request import send_vouch_request_dm

        product_names = ", ".join(
            str(item.get("name_snapshot") or "Produkt") for item in order_items[:3]
        )
        if len(order_items) > 3:
            product_names += " …"
        asyncio.create_task(
            send_vouch_request_dm(
                bot,
                buyer,
                order_ref_text=order_ref(order),
                product_hint=product_names or "dein Kauf",
            )
        )

    channel = interaction.channel
    if isinstance(channel, discord.TextChannel):
        asyncio.create_task(
            _delete_channel_later(
                channel, 5.0, reason="Kauf bestätigt — Ticket automatisch geschlossen"
            )
        )


async def action_fast_buy(bot: ShopBot, interaction: discord.Interaction) -> None:
    """Käufer bezahlt Sofort mit Credits und bestätigt die Bestellung."""
    order = await get_order_for_interaction(bot, interaction)
    if not order:
        await interaction.response.send_message(
            embed=error_embed("Keine Bestellung"), ephemeral=True
        )
        return
    if interaction.user.id != int(order["user_id"]):
        await interaction.response.send_message(
            embed=error_embed("Nur Käufer", "Nur der Käufer kann Quick Buy nutzen."),
            ephemeral=True,
        )
        return
    if not int(order.get("credits_enabled") or 0):
        await interaction.response.send_message(
            embed=error_embed(
                "Nicht verfügbar",
                "Quick Buy ist nur bei Credits-aktivierten Buy-Panels verfügbar.",
            ),
            ephemeral=True,
        )
        return
    if str(order.get("order_kind") or "shop") != "shop":
        await interaction.response.send_message(
            embed=error_embed(
                "Nicht verfügbar",
                "Quick Buy gilt nur für Produkt-Käufe, nicht für Credits-Tickets.",
            ),
            ephemeral=True,
        )
        return
    if order["status"] in ("completed", "cancelled"):
        await interaction.response.send_message(
            embed=error_embed("Geschlossen", "Diese Bestellung ist bereits beendet."),
            ephemeral=True,
        )
        return

    from utils.credits import credits_needed_for_total, format_credits

    need = credits_needed_for_total(float(order["total"]))
    balance = await bot.db.get_credits(int(order["guild_id"]), int(order["user_id"]))
    if balance < need:
        await interaction.response.send_message(
            embed=error_embed(
                "Zu wenig Credits",
                f"Benötigt: **{format_credits(need)}** · "
                f"Guthaben: **{format_credits(balance)}**\n"
                "Kaufe Credits über **Buy Credits** auf dem Panel.",
            ),
            ephemeral=True,
        )
        return

    await action_confirm_order(
        bot,
        interaction,
        require_staff=False,
        paid_with_credits=True,
        credits_charged=need,
    )


async def action_cancel_order(bot: ShopBot, interaction: discord.Interaction) -> None:
    order = await get_order_for_interaction(bot, interaction)
    if not order:
        await interaction.response.send_message(
            embed=error_embed("Keine Bestellung"), ephemeral=True
        )
        return
    if not await is_buyer_or_staff(bot, interaction, order):
        await interaction.response.send_message(
            embed=error_embed("Keine Berechtigung"), ephemeral=True
        )
        return
    if order["status"] in ("completed", "cancelled"):
        await interaction.response.send_message(
            embed=error_embed("Bereits beendet"), ephemeral=True
        )
        return

    await interaction.response.defer()
    await bot.db.update_order(int(order["id"]), status="cancelled")

    await interaction.followup.send(
        embed=warn_embed(
            "Kauf abgebrochen",
            f"Abgebrochen von {interaction.user.mention}.\n"
            f"Bestellung **{order_ref(order)}** wurde storniert.\n\n"
            "⏳ Dieses Ticket wird in 5 Sekunden automatisch geschlossen.",
        )
    )

    channel = interaction.channel
    if isinstance(channel, discord.TextChannel):
        asyncio.create_task(
            _delete_channel_later(
                channel,
                5.0,
                reason="Kauf abgebrochen — Ticket automatisch geschlossen",
            )
        )


async def action_close_ticket(
    bot: ShopBot, interaction: discord.Interaction, *, delete_channel: bool = True
) -> None:
    """Staff schließt das Ticket (Channel löschen). Offene Orders werden storniert."""
    if not await is_staff(bot, interaction):
        await interaction.response.send_message(
            embed=error_embed("Keine Berechtigung", "Nur Staff/Admin."),
            ephemeral=True,
        )
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            embed=error_embed("Nur in einem Ticket-Channel nutzbar."),
            ephemeral=True,
        )
        return

    order = await get_order_for_interaction(bot, interaction)
    await interaction.response.defer(ephemeral=True)

    note = ""
    if order and order["status"] not in ("completed", "cancelled"):
        await bot.db.update_order(int(order["id"]), status="cancelled")
        note = f" Offene Bestellung **{order_ref(order)}** wurde storniert."

    if delete_channel:
        await interaction.followup.send(
            embed=success_embed("Ticket wird geschlossen", f"Channel wird gelöscht.{note}"),
            ephemeral=True,
        )
        try:
            await channel.delete(reason=f"Ticket geschlossen von {interaction.user}")
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed("Löschen fehlgeschlagen", str(e)[:500]),
                ephemeral=True,
            )
    else:
        try:
            await channel.edit(name=f"closed-{channel.name}"[:100])
        except discord.HTTPException:
            pass
        await interaction.followup.send(
            embed=success_embed("Ticket geschlossen", f"Channel umbenannt.{note}"),
            ephemeral=True,
        )


class TicketOrderView(discord.ui.View):
    """Persistente Ticket-Buttons für Bestellungen."""

    def __init__(self, bot: ShopBot, *, show_fast_buy: bool = False) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.show_fast_buy = show_fast_buy

        if show_fast_buy:
            fast_btn = discord.ui.Button(
                label="Quick Buy",
                style=discord.ButtonStyle.success,
                custom_id="ticket:fast_buy",
                emoji="⚡",
                row=1,
            )
            fast_btn.callback = self._fast_buy  # type: ignore[method-assign]
            self.add_item(fast_btn)

    @discord.ui.button(
        label="Bestellung anzeigen",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:show_cart",
        emoji="🛒",
        row=0,
    )
    async def show_cart(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await action_show_order(self.bot, interaction)

    @discord.ui.button(
        label="Payment beweisen",
        style=discord.ButtonStyle.primary,
        custom_id="ticket:proof",
        emoji="📎",
        row=0,
    )
    async def proof(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        order = await get_order_for_interaction(self.bot, interaction)
        if not order:
            await interaction.response.send_message(
                embed=error_embed("Keine Bestellung", "Kein Order für dieses Ticket."),
                ephemeral=True,
            )
            return
        if order["status"] in ("completed", "cancelled"):
            await interaction.response.send_message(
                embed=error_embed("Geschlossen", "Diese Bestellung ist bereits beendet."),
                ephemeral=True,
            )
            return
        if interaction.user.id != int(order["user_id"]):
            await interaction.response.send_message(
                embed=error_embed("Nur Käufer", "Nur der Käufer kann Payment beweisen."),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(PaymentProofModal(self.bot, int(order["id"])))

    @discord.ui.button(
        label="Kauf abbrechen",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:cancel",
        emoji="✖️",
        row=0,
    )
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await action_cancel_order(self.bot, interaction)

    @discord.ui.button(
        label="Payment bestätigen",
        style=discord.ButtonStyle.success,
        custom_id="ticket:confirm",
        emoji="✅",
        row=1,
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await action_confirm_order(self.bot, interaction)

    async def _fast_buy(self, interaction: discord.Interaction) -> None:
        await action_fast_buy(self.bot, interaction)


class PaymentProofModal(discord.ui.Modal, title="Payment beweisen"):
    ign = discord.ui.TextInput(
        label="In-Game Name (IGN)",
        placeholder="Dein IGN",
        max_length=64,
        required=True,
    )
    note = discord.ui.TextInput(
        label="Hinweis (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300,
        placeholder="Optionaler Kommentar zur Zahlung",
    )

    def __init__(self, bot: ShopBot, order_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.order_id = order_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.db.update_order(
            self.order_id,
            ign=str(self.ign.value).strip(),
            status="awaiting_proof",
        )
        view = ProofImageWaitView(self.bot, self.order_id)
        note = str(self.note.value).strip() if self.note.value else ""
        msg = (
            f"IGN gespeichert: **{self.ign.value}**\n"
            "Lade jetzt ein **Bild** als Proof als Anhang in diesem Ticket hoch "
            "und klicke auf **Bild bestätigen**.\n"
            "Oder Staff: `/order confirm` nach Prüfung."
        )
        if note:
            msg += f"\nHinweis: {note}"
        await interaction.response.send_message(msg, view=view)


class ProofImageWaitView(discord.ui.View):
    def __init__(self, bot: ShopBot, order_id: int) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.order_id = order_id

    @discord.ui.button(label="Bild bestätigen", style=discord.ButtonStyle.primary)
    async def confirm_image(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        order = await self.bot.db.get_order(self.order_id)
        if not order:
            await interaction.response.send_message(
                embed=error_embed("Order nicht gefunden"), ephemeral=True
            )
            return
        if interaction.user.id != int(order["user_id"]):
            await interaction.response.send_message(
                embed=error_embed("Nur Käufer"), ephemeral=True
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Channel"), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        image_url = None
        async for message in channel.history(limit=30):
            if message.author.id != interaction.user.id:
                continue
            for att in message.attachments:
                ct = (att.content_type or "").lower()
                name = (att.filename or "").lower()
                if ct.startswith("image/") or name.endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                ):
                    image_url = att.url
                    break
            if image_url:
                break

        if not image_url:
            await interaction.followup.send(
                embed=error_embed(
                    "Kein Bild gefunden",
                    "Lade zuerst ein Bild (PNG/JPG/…) in dieses Ticket hoch, "
                    "dann klicke erneut auf **Bild bestätigen**.",
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.add_payment_proof(
            self.order_id, image_url, interaction.user.id
        )
        await self.bot.db.update_order(self.order_id, status="awaiting_confirm")

        settings = await self.bot.db.ensure_guild(interaction.guild_id)  # type: ignore[arg-type]
        items = await self.bot.db.get_order_items(self.order_id)
        order = await self.bot.db.get_order(self.order_id)
        assert order is not None
        embed = order_ticket_embed(
            order, items, settings, interaction.user, interaction.guild
        )
        embed.add_field(name="Payment Proof", value=f"[Bild öffnen]({image_url})", inline=False)
        embed.set_image(url=image_url)

        await channel.send(
            content="Payment Proof eingereicht — Staff: Button oder `/order confirm`.",
            embed=embed,
        )
        await interaction.followup.send(
            embed=success_embed(
                "Proof gespeichert",
                "Staff wurde benachrichtigt. Warte auf Bestätigung.",
            ),
            ephemeral=True,
        )
        self.stop()
