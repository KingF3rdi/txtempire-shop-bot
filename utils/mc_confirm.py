"""Minecraft-Payment → automatische Ticket-Bestätigung."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import discord

from utils.delivery import deliver_packs
from utils.embeds import (
    error_embed,
    format_price,
    order_ref,
    purchase_success_embed,
    success_embed,
    warn_embed,
)
from utils.roles import grant_purchase_roles
from views.ticket_views import enrich_order_item_roles, _delete_channel_later

if TYPE_CHECKING:
    from bot import ShopBot

_confirm_locks: dict[int, asyncio.Lock] = {}

_PAYMENT_REASON_LABELS: dict[str, str] = {
    "ok": "Auto-bestätigt",
    "auto_confirmed": "Auto-bestätigt",
    "ign_not_linked": "IGN nicht verknüpft",
    "auto_confirm_disabled": "Auto-Confirm aus",
    "no_matching_order": "Kein passendes Ticket (Betrag)",
    "already_completed": "Order schon bestätigt",
    "cancelled": "Order storniert",
    "order_not_found": "Order nicht gefunden",
    "guild_unavailable": "Guild nicht erreichbar",
    "confirm_failed": "Bestätigung fehlgeschlagen",
}


async def _post_mc_payment_log(
    bot: ShopBot,
    *,
    guild_id: int,
    ign: str,
    amount: float,
    raw_text: str,
    reason: str,
    auto_confirmed: bool,
    user_id: int | None = None,
    order: dict | None = None,
    event_id: int | None = None,
) -> None:
    """Jede erkannte Zahlung in den konfigurierten Log-Channel posten."""
    settings = await bot.db.ensure_guild(guild_id)
    log_ch_id = settings.get("mc_payment_log_channel_id")
    if not log_ch_id:
        return
    guild = bot.get_guild(guild_id)
    if guild is None:
        return
    log_ch = guild.get_channel(int(log_ch_id))
    if not isinstance(log_ch, discord.TextChannel):
        return

    label = _PAYMENT_REASON_LABELS.get(reason or "", reason or "—")
    lines = [
        f"**IGN:** `{ign}`",
        f"**Betrag:** {format_price(amount)}",
    ]
    if user_id:
        lines.append(f"**Discord:** <@{user_id}>")
    if order:
        lines.append(f"**Order:** {order_ref(order)}")
    lines.append(f"**Status:** {label}")
    if event_id:
        lines.append(f"**Event-ID:** `{event_id}`")
    if raw_text:
        lines.append(f"**Chat:** `{raw_text[:200]}`")

    title = "MC-Zahlung · Auto-Confirm" if auto_confirmed else "MC-Zahlung erkannt"
    if auto_confirmed:
        embed = success_embed(title, "\n".join(lines))
    elif reason in ("no_matching_order", "ign_not_linked", "auto_confirm_disabled"):
        embed = warn_embed(title, "\n".join(lines))
    else:
        embed = error_embed(title, "\n".join(lines))

    try:
        await log_ch.send(embed=embed)
    except discord.HTTPException:
        pass


def _lock_for(order_id: int) -> asyncio.Lock:
    lock = _confirm_locks.get(order_id)
    if lock is None:
        lock = asyncio.Lock()
        _confirm_locks[order_id] = lock
    return lock


async def confirm_order_by_id(
    bot: ShopBot,
    order_id: int,
    *,
    source: str = "mc_auto",
    raw_payment: str = "",
) -> dict[str, Any]:
    """Bestätigt eine offene Bestellung ohne Discord-Interaction.

    Returns dict mit keys: ok, reason?, order?, channel_id?
    """
    async with _lock_for(order_id):
        order = await bot.db.get_order(order_id)
        if not order:
            return {"ok": False, "reason": "order_not_found"}
        status = str(order.get("status") or "")
        if status == "completed":
            return {"ok": False, "reason": "already_completed", "order": order}
        if status == "cancelled":
            return {"ok": False, "reason": "cancelled", "order": order}
        if status not in ("pending", "awaiting_proof", "awaiting_confirm"):
            return {"ok": False, "reason": f"bad_status:{status}", "order": order}

        guild_id = int(order["guild_id"])
        guild = bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(guild_id)
            except discord.HTTPException:
                return {"ok": False, "reason": "guild_unavailable", "order": order}

        settings = await bot.db.ensure_guild(guild_id)
        order_items = await bot.db.get_order_items(order_id)
        order_items = await enrich_order_item_roles(bot, order_items)
        order_kind = str(order.get("order_kind") or "shop")

        update_fields: dict[str, Any] = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        }
        await bot.db.update_order(order_id, **update_fields)

        credits_granted: float | None = None
        credits_balance = None
        scan_premium_until: str | None = None
        if order_kind == "credits":
            from utils.credits import currency_to_credits, format_credits

            amount = float(order.get("credits_amount") or 0)
            if amount <= 0:
                amount = currency_to_credits(float(order["total"]))
            credits_balance = await bot.db.add_credits(
                guild_id, int(order["user_id"]), amount
            )
            credits_granted = amount
        elif order_kind == "scan_premium":
            days = int(float(order.get("credits_amount") or 14))
            if days not in (14, 30):
                days = 14 if days < 30 else 30
            scan_premium_until = await bot.db.extend_scan_premium(
                guild_id, int(order["user_id"]), days
            )

        member: discord.Member | None = None
        try:
            member = await guild.fetch_member(int(order["user_id"]))
        except discord.HTTPException:
            member = None

        if order_kind == "scan_premium" and member is not None:
            from utils.scan_premium_role import sync_scan_premium_role

            await sync_scan_premium_role(
                bot, guild, int(order["user_id"]), force_grant=True
            )

        role_result: dict = {"granted": [], "skipped": [], "failed": []}
        delivery_info: dict = {}
        non_product = order_kind in ("credits", "scan_premium")
        channel: discord.TextChannel | None = None
        ch_id = order.get("ticket_channel_id")
        if ch_id:
            ch = guild.get_channel(int(ch_id))
            if isinstance(ch, discord.TextChannel):
                channel = ch

        if member and not non_product:
            role_result = await grant_purchase_roles(
                member,
                settings,
                order_items,
                pack_qty=int(order.get("pack_qty") or 0)
                or sum(int(i.get("qty") or 1) for i in order_items),
            )
            if channel is not None:
                delivery_info = await deliver_packs(
                    member, channel, order_items, bot=bot
                )
        elif member and non_product:
            role_result = await grant_purchase_roles(member, settings, [])
        elif not member:
            role_result["failed"].append(
                "Käufer nicht auf dem Server — Rollen konnten nicht vergeben werden."
            )

        buyer: discord.abc.User | None = member
        if buyer is None:
            try:
                buyer = await bot.fetch_user(int(order["user_id"]))
            except discord.HTTPException:
                buyer = None

        order = await bot.db.get_order(order_id) or order
        if buyer is not None:
            success = purchase_success_embed(order, order_items, buyer, role_result)
        else:
            success = success_embed(
                "Kauf bestätigt (Auto)",
                f"Bestellung **{order_ref(order)}** wurde automatisch bestätigt.",
            )

        from utils.credits import format_credits

        extra_parts: list[str] = [
            f"🤖 Auto-Bestätigung via Minecraft-Payment (`{source}`)."
        ]
        if raw_payment:
            extra_parts.append(f"Chat: `{raw_payment[:180]}`")
        if credits_granted is not None:
            extra_parts.append(
                f"🪙 **{format_credits(credits_granted)} Credits** gutgeschrieben "
                f"(Guthaben: **{format_credits(credits_balance or 0)}**)."
            )
        if scan_premium_until is not None:
            from utils.scan_prices import premium_scan_label

            days = int(float(order.get("credits_amount") or 14))
            extra_parts.append(
                f"⭐ **Scan Premium** bis `{scan_premium_until}` "
                f"({days} Tage · {premium_scan_label(days=days)})."
            )
        if delivery_info.get("dm_sent"):
            extra_parts.append("Pack-DM gesendet.")
        if delivery_info.get("files_sent"):
            extra_parts.append("Pack-Datei(en) gesendet.")
        if delivery_info.get("links_posted"):
            extra_parts.append("Pack-Links im Ticket gepostet.")
        if not non_product:
            extra_parts.append("Käufer kann einmalig `/vouch` nutzen.")
        extra_parts.append("⏳ Dieses Ticket wird in 5 Sekunden automatisch gelöscht.")
        success.add_field(
            name="Lieferung / Hinweise",
            value="\n".join(extra_parts),
            inline=False,
        )

        if channel is not None:
            try:
                mention = member.mention if member else f"<@{order['user_id']}>"
                await channel.send(content=mention, embed=success)
            except discord.HTTPException:
                pass
            asyncio.create_task(
                _delete_channel_later(
                    channel,
                    5.0,
                    reason="Kauf auto-bestätigt (MC-Payment) — Ticket geschlossen",
                )
            )

        if buyer is not None and not non_product:
            had_pack_dm = bool(
                delivery_info.get("dm_sent") or delivery_info.get("files_sent")
            ) and not delivery_info.get("dm_failed")
            if not had_pack_dm:
                from utils.vouch_request import send_vouch_request_dm

                product_names = ", ".join(
                    str(item.get("name_snapshot") or "Produkt")
                    for item in order_items[:3]
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

        # Discord-Log für MC-Zahlungen läuft über handle_mc_payment (_post_mc_payment_log)
        if source != "mc_payment":
            log_ch_id = settings.get("mc_payment_log_channel_id")
            if log_ch_id:
                log_ch = guild.get_channel(int(log_ch_id))
                if isinstance(log_ch, discord.TextChannel):
                    try:
                        await log_ch.send(
                            embed=success_embed(
                                "MC Auto-Confirm",
                                f"Order **{order_ref(order)}** · "
                                f"<@{order['user_id']}> · "
                                f"`{source}`\n"
                                + (f"`{raw_payment[:200]}`" if raw_payment else ""),
                            )
                        )
                    except discord.HTTPException:
                        pass

        return {
            "ok": True,
            "order": order,
            "channel_id": int(ch_id) if ch_id else None,
        }


async def handle_mc_payment(
    bot: ShopBot,
    *,
    guild_id: int,
    ign: str,
    amount: float,
    raw_text: str = "",
) -> dict[str, Any]:
    """Verarbeitet eine erkannte Ingame-Zahlung (DB + Discord-Log)."""
    settings = await bot.db.ensure_guild(guild_id)
    link = await bot.db.get_mc_link_by_ign(guild_id, ign)
    user_id: int | None = int(link["user_id"]) if link else None
    order: dict | None = None
    auto_confirmed = False
    reason = "ign_not_linked"
    open_orders = 0

    if not link:
        event_id = await bot.db.log_mc_payment(
            guild_id, ign=ign, amount=amount, raw_text=raw_text
        )
        await _post_mc_payment_log(
            bot,
            guild_id=guild_id,
            ign=ign,
            amount=amount,
            raw_text=raw_text,
            reason=reason,
            auto_confirmed=False,
            event_id=event_id,
        )
        return {
            "ok": True,
            "auto_confirmed": False,
            "reason": reason,
            "event_id": event_id,
        }

    assert user_id is not None
    auto = int(
        settings.get("mc_auto_confirm")
        if settings.get("mc_auto_confirm") is not None
        else 1
    )
    if not auto:
        reason = "auto_confirm_disabled"
        event_id = await bot.db.log_mc_payment(
            guild_id, ign=ign, amount=amount, raw_text=raw_text
        )
        await _post_mc_payment_log(
            bot,
            guild_id=guild_id,
            ign=ign,
            amount=amount,
            raw_text=raw_text,
            reason=reason,
            auto_confirmed=False,
            user_id=user_id,
            event_id=event_id,
        )
        return {
            "ok": True,
            "auto_confirmed": False,
            "reason": reason,
            "event_id": event_id,
            "user_id": user_id,
        }

    order = await bot.db.find_open_order_by_amount(guild_id, user_id, amount)
    if not order:
        reason = "no_matching_order"
        event_id = await bot.db.log_mc_payment(
            guild_id, ign=ign, amount=amount, raw_text=raw_text
        )
        opens = await bot.db.list_open_orders_for_user(guild_id, user_id)
        open_orders = len(opens)
        if opens:
            await _notify_payment_mismatch(
                bot, opens[0], ign=ign, amount=amount, raw_text=raw_text
            )
        await _post_mc_payment_log(
            bot,
            guild_id=guild_id,
            ign=ign,
            amount=amount,
            raw_text=raw_text,
            reason=reason,
            auto_confirmed=False,
            user_id=user_id,
            event_id=event_id,
        )
        return {
            "ok": True,
            "auto_confirmed": False,
            "reason": reason,
            "event_id": event_id,
            "user_id": user_id,
            "open_orders": open_orders,
        }

    await bot.db.update_order(int(order["id"]), ign=ign.strip()[:32])

    result = await confirm_order_by_id(
        bot,
        int(order["id"]),
        source="mc_payment",
        raw_payment=raw_text,
    )
    auto_confirmed = bool(result.get("ok"))
    reason = str(result.get("reason") or ("ok" if auto_confirmed else "confirm_failed"))
    if auto_confirmed:
        reason = "ok"
    event_id = await bot.db.log_mc_payment(
        guild_id,
        ign=ign,
        amount=amount,
        raw_text=raw_text,
        order_id=int(order["id"]),
        auto_confirmed=auto_confirmed,
    )
    await _post_mc_payment_log(
        bot,
        guild_id=guild_id,
        ign=ign,
        amount=amount,
        raw_text=raw_text,
        reason=reason if not auto_confirmed else "auto_confirmed",
        auto_confirmed=auto_confirmed,
        user_id=user_id,
        order=order,
        event_id=event_id,
    )
    return {
        "ok": True,
        "auto_confirmed": auto_confirmed,
        "reason": reason if not auto_confirmed else "ok",
        "order_id": int(order["id"]),
        "order_number": order.get("order_number"),
        "user_id": user_id,
        "event_id": event_id,
    }


async def _notify_payment_mismatch(
    bot: ShopBot,
    order: dict,
    *,
    ign: str,
    amount: float,
    raw_text: str,
) -> None:
    ch_id = order.get("ticket_channel_id")
    if not ch_id:
        return
    guild = bot.get_guild(int(order["guild_id"]))
    if guild is None:
        return
    channel = guild.get_channel(int(ch_id))
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.send(
            embed=warn_embed(
                "MC-Zahlung erkannt — Betrag passt nicht",
                f"IGN **{ign}** hat **{format_price(amount)}** gesendet, "
                f"Ticket erwartet **{format_price(float(order['total']))}**.\n"
                "Bitte Betrag prüfen oder Staff bestätigen.\n"
                + (f"`{raw_text[:160]}`" if raw_text else ""),
            )
        )
    except discord.HTTPException:
        pass


async def handle_mc_link_redeem(
    bot: ShopBot,
    *,
    code: str,
    ign: str,
) -> dict[str, Any]:
    """Löst einen Link-Code ein (vom Ingame-Bot/Mod)."""
    data = await bot.db.peek_mc_link_code(code)
    if not data:
        return {"ok": False, "reason": "invalid_or_expired_code"}

    expected = str(data.get("ign") or "").strip()
    if expected.lower() != ign.strip().lower():
        return {
            "ok": False,
            "reason": "ign_mismatch",
            "expected_ign": expected,
            "got_ign": ign.strip(),
        }

    data = await bot.db.consume_mc_link_code(code)
    if not data:
        return {"ok": False, "reason": "invalid_or_expired_code"}

    guild_id = int(data["guild_id"])
    user_id = int(data["user_id"])
    ign_clean = ign.strip()[:32]
    await bot.db.set_mc_link(guild_id, user_id, ign_clean)

    confirm = success_embed(
        "✅ Minecraft-Account verknüpft",
        f"Dein Discord ist jetzt mit IGN **{ign_clean}** verknüpft.\n\n"
        "Bei offenen Kauf-Tickets mit **passendem Betrag** wird die Zahlung "
        "automatisch bestätigt — ohne Screenshot.",
    )
    confirm.set_footer(text=f"Code {code.strip().upper()} eingelöst")

    dm_ok = False
    log_ok = False

    user = None
    try:
        user = await bot.fetch_user(user_id)
    except discord.HTTPException:
        pass

    # Nur privat an den User (DM) — keine öffentliche Channel-Bestätigung
    if user is not None:
        try:
            await user.send(embed=confirm)
            dm_ok = True
        except discord.HTTPException:
            pass

    guild = bot.get_guild(guild_id)
    if guild is not None:
        try:
            from views.mc_link_views import refresh_mc_link_panel_status

            await refresh_mc_link_panel_status(bot, guild_id)
        except Exception:
            pass

        # Optional: Staff-Log (nicht öffentlich im Panel-Channel)
        settings = await bot.db.ensure_guild(guild_id)
        log_ch_id = settings.get("mc_payment_log_channel_id")
        if log_ch_id:
            log_ch = guild.get_channel(int(log_ch_id))
            if isinstance(log_ch, discord.TextChannel):
                try:
                    staff_embed = success_embed(
                        "Minecraft-Account verknüpft",
                        f"<@{user_id}> ↔ **{ign_clean}**\n"
                        f"Code `{code.strip().upper()}`",
                    )
                    await log_ch.send(embed=staff_embed)
                    log_ok = True
                except discord.HTTPException:
                    pass

    return {
        "ok": True,
        "guild_id": guild_id,
        "user_id": user_id,
        "ign": ign_clean,
        "notified_dm": dm_ok,
        "notified_channel": False,
        "notified_log": log_ok,
    }
