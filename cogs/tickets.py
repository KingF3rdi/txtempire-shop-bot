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
        guild.id, interaction.user.id, cart, ticket_channel_id=None
    )
    order = await bot.db.get_order(order_id)
    assert order is not None
    seq = int(order.get("order_number") or order_id)
    channel_name = f"order-{seq:04d}-{safe_name}"[:100]
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
        await _send_with_retry(
            channel, embed=payment_info_embed(order, settings)
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
    try:
        await _send_with_retry(
            channel,
            content=(
                f"{interaction.user.mention} {mention} — neue Bestellung {order_ref(order)}!\n"
                f"**{PAYMENT_NOTICE}**\n"
                "Admin sieht den Warenkorb · "
                "Käufer: **Bestellung anzeigen** / **Kauf abbrechen**."
            ),
            embed=cart_panel,
            view=TicketOrderView(bot),
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        raise ValueError(
            f"Zahlungsinfo gesendet, Warenkorb-Panel fehlgeschlagen: {e}"
        ) from e

    return channel


class TicketsCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

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


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(TicketsCog(bot))
