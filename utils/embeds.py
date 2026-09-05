from __future__ import annotations

import discord

from config import DEFAULT_PAYEE, EMBED_COLOR, EMBED_ERROR, EMBED_SUCCESS, EMBED_WARN, PAYMENT_NOTICE


def base_embed(title: str, description: str = "", color: int = EMBED_COLOR) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or None, color=color)
    return embed


def success_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, EMBED_SUCCESS)


def error_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, EMBED_ERROR)


def warn_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, EMBED_WARN)


def order_ref(order: dict) -> str:
    """Fortlaufende Bestellnummer (#1, #2, ...) - nicht die interne DB-ID."""
    n = order.get("order_number")
    if n is None:
        n = order.get("id")
    return f"#{n}"


def format_price(amount: float) -> str:
    from utils.price import format_compact_number

    a = float(amount)
    if abs(a) >= 1000:
        return f"{format_compact_number(a)} €"
    return f"{a:.2f} €"


def cart_embed(items: list[dict], total: float) -> discord.Embed:
    embed = base_embed("Warenkorb")
    if not items:
        embed.description = "Dein Warenkorb ist leer."
        embed.set_footer(text=PAYMENT_NOTICE)
        return embed
    lines = []
    pack_qty = 0
    for row in items:
        sub = float(row["price"]) * int(row["qty"])
        pack_qty += int(row["qty"])
        lines.append(
            f"- **{row['name']}** x {row['qty']} - {format_price(sub)}"
        )
    embed.description = "\n".join(lines)
    from utils.volume_discount import apply_volume_discount

    new_total, savings, pct = apply_volume_discount(total, pack_qty)
    if savings > 0:
        embed.add_field(
            name="Mengenrabatt",
            value=(
                f"**{pack_qty}** Packs → **−{pct:g} %** "
                f"(−{format_price(savings)})\n"
                f"Statt {format_price(total)} → **{format_price(new_total)}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Gesamtpreis", value=format_price(new_total), inline=False
        )
    else:
        embed.add_field(name="Gesamtpreis", value=format_price(total), inline=False)
        if pack_qty < 5:
            embed.add_field(
                name="Tipp",
                value=f"Noch **{5 - pack_qty}** Pack(s) bis 5 % Mengenrabatt.",
                inline=False,
            )
    embed.set_footer(text=PAYMENT_NOTICE)
    return embed


def _order_item_lines(items: list[dict]) -> list[str]:
    lines = []
    for row in items:
        name = row.get("name_snapshot") or row.get("name") or "Item"
        price = float(row.get("price_snapshot", row.get("price", 0)))
        qty = int(row.get("qty") or 1)
        lines.append(f"- **{name}** x {qty} - {format_price(price * qty)}")
    return lines


def _field_value(text: str, limit: int = 1024) -> str:
    text = (text or "").strip() or "-"
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def payee_name(settings: dict) -> str:
    name = (settings.get("payee_a_label") or "").strip()
    if not name or name in ("TxTHub", "Empfänger A"):
        return DEFAULT_PAYEE
    return name[:256]


def payee_details_text(settings: dict) -> str:
    return (settings.get("payee_a_details") or "").strip()


def payment_info_embed(
    order: dict, settings: dict, *, money_log_hint: bool = True
) -> discord.Embed:
    """Zahlungsinformationen - wird oben im Ticket gepostet."""
    from utils.ticket_faq import MONEY_LOG_HINT

    name = payee_name(settings)
    details = payee_details_text(settings) or "_Keine Details hinterlegt_"
    embed = base_embed(
        "Zahlungsinformationen",
        f"# {PAYMENT_NOTICE}\n\n"
        f"**{PAYMENT_NOTICE}**\n\n"
        "Bitte den **gesamten Betrag** überweisen, danach **Payment beweisen** "
        "(IGN + Bild).",
    )
    embed.add_field(
        name="Gesamtbetrag",
        value=f"# {format_price(float(order['total']))}",
        inline=True,
    )
    embed.add_field(
        name="Bestellung",
        value=order_ref(order),
        inline=True,
    )
    if float(order.get("volume_discount_amount") or 0) > 0:
        pct = float(order.get("volume_discount_pct") or 0)
        qty = int(order.get("pack_qty") or 0)
        orig = float(order.get("original_total") or 0)
        embed.add_field(
            name="Mengenrabatt",
            value=(
                f"**{qty}** Packs → **−{pct:g} %** "
                f"(−{format_price(float(order['volume_discount_amount']))})"
                + (f"\nWarenwert: {format_price(orig)}" if orig else "")
            ),
            inline=False,
        )
    if order.get("discount_code") and float(order.get("discount_amount") or 0) > 0:
        vol = float(order.get("volume_discount_amount") or 0)
        orig = float(order.get("original_total") or 0)
        before_code = round(orig - vol, 2) if orig and vol else (
            orig if orig else 0
        )
        embed.add_field(
            name="Rabatt / Creator-Code",
            value=(
                f"`{order['discount_code']}` — "
                f"−{format_price(float(order['discount_amount']))}"
                + (
                    f" (nach Mengenrabatt {format_price(before_code)})"
                    if before_code
                    else ""
                )
            ),
            inline=False,
        )
    embed.add_field(
        name=f"Zahlung an {name}",
        value=_field_value(
            f"# {PAYMENT_NOTICE}\n"
            f"**{format_price(float(order['total']))}**\n{details}"
        ),
        inline=False,
    )
    if money_log_hint:
        embed.add_field(
            name="Money-Log",
            value=MONEY_LOG_HINT,
            inline=False,
        )
    embed.set_footer(text=PAYMENT_NOTICE)
    return embed


def order_cart_panel_embed(
    order: dict,
    items: list[dict],
    settings: dict,
    buyer: discord.abc.User,
    guild: discord.Guild | None = None,
) -> discord.Embed:
    """Warenkorb / Bestell-Panel — nur für Staff (ephemeral / Staff-Button)."""
    embed = base_embed(
        f"Kauf-Warenkorb - Bestellung {order_ref(order)}",
        f"# {PAYMENT_NOTICE}\n\n"
        f"Käufer: {buyer.mention}\n"
        f"Status: `{order['status']}`\n"
        f"**{PAYMENT_NOTICE}**",
    )
    lines = _order_item_lines(items)
    embed.add_field(
        name="Artikel im Warenkorb",
        value="\n".join(lines) or "-",
        inline=False,
    )
    embed.add_field(
        name="Gesamt",
        value=format_price(float(order["total"])),
        inline=True,
    )
    if float(order.get("volume_discount_amount") or 0) > 0:
        embed.add_field(
            name="Mengenrabatt",
            value=(
                f"{int(order.get('pack_qty') or 0)} Packs · "
                f"−{float(order.get('volume_discount_pct') or 0):g} % "
                f"(−{format_price(float(order['volume_discount_amount']))})"
            ),
            inline=True,
        )
    if order.get("discount_code") and float(order.get("discount_amount") or 0) > 0:
        embed.add_field(
            name="Code",
            value=(
                f"`{order['discount_code']}` "
                f"(−{format_price(float(order['discount_amount']))})"
            ),
            inline=True,
        )
    if guild is not None:
        from utils.roles import collect_autorole_mentions

        role_lines = collect_autorole_mentions(guild, items)
        pack_qty = int(order.get("pack_qty") or 0)
        if pack_qty <= 0:
            pack_qty = sum(int(i.get("qty") or 1) for i in items)
        from utils.volume_discount import volume_role_ids_for_qty

        for rid, label in volume_role_ids_for_qty(settings, pack_qty):
            role = guild.get_role(rid)
            if role:
                role_lines.append(f"- {label}: {role.mention}")
        customer_id = settings.get("customer_role_id")
        if customer_id:
            cr = guild.get_role(int(customer_id))
            if cr:
                role_lines.insert(0, f"- Customer: {cr.mention}")
        if role_lines:
            embed.add_field(
                name="Rollen nach Bestätigung",
                value="\n".join(role_lines),
                inline=False,
            )
    if order.get("ign"):
        embed.add_field(name="IGN", value=order["ign"], inline=True)
    embed.set_footer(
        text=f"{PAYMENT_NOTICE} · Nur Staff · Payment bestätigen"
    )
    return embed


def purchase_success_embed(
    order: dict,
    items: list[dict],
    buyer: discord.abc.User,
    role_result: dict | None = None,
) -> discord.Embed:
    """Panel nach erfolgreicher Staff-Bestätigung."""
    embed = success_embed(
        "Erfolgreicher Kauf",
        f"Bestellung **{order_ref(order)}** von {buyer.mention} wurde bestätigt.",
    )
    lines = _order_item_lines(items)
    embed.add_field(
        name="Gekaufter Warenkorb",
        value="\n".join(lines) or "-",
        inline=False,
    )
    embed.add_field(
        name="Gesamt",
        value=format_price(float(order["total"])),
        inline=True,
    )
    if role_result:
        if role_result.get("granted"):
            embed.add_field(
                name="Rollen vergeben",
                value=", ".join(f"`{n}`" for n in role_result["granted"]),
                inline=False,
            )
        if role_result.get("skipped"):
            embed.add_field(
                name="Bereits vorhanden",
                value=", ".join(f"`{n}`" for n in role_result["skipped"]),
                inline=False,
            )
        if role_result.get("failed"):
            embed.add_field(
                name="Rollen-Fehler",
                value="\n".join(f"- {f}" for f in role_result["failed"]),
                inline=False,
            )
    if order.get("ign"):
        embed.add_field(name="IGN", value=order["ign"], inline=True)
    embed.set_footer(text=PAYMENT_NOTICE)
    return embed


def order_ticket_embed(
    order: dict,
    items: list[dict],
    settings: dict,
    buyer: discord.abc.User,
    guild: discord.Guild | None = None,
) -> discord.Embed:
    """Komplettes Ticket-Embed (z. B. nach Payment-Proof)."""
    embed = order_cart_panel_embed(order, items, settings, buyer, guild)
    name = payee_name(settings)
    details = payee_details_text(settings) or "_Keine Details hinterlegt_"
    embed.add_field(
        name=f"Zahlung an {name}",
        value=_field_value(
            f"**{format_price(float(order['total']))}**\n{details}"
        ),
        inline=False,
    )
    return embed
