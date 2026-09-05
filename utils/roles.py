from __future__ import annotations

import discord


async def grant_purchase_roles(
    member: discord.Member,
    settings: dict,
    order_items: list[dict],
    *,
    pack_qty: int | None = None,
) -> dict[str, list[str]]:
    """
    Vergibt Customer-, Kategorie-, Item- und Mengen-Autoroles nach Kauf.
    """
    granted: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    to_grant: dict[int, str] = {}

    customer_id = settings.get("customer_role_id")
    if customer_id:
        to_grant[int(customer_id)] = "Customer"

    for item in order_items:
        name = item.get("name_snapshot") or "Item"
        if item.get("category_role_id"):
            rid = int(item["category_role_id"])
            to_grant[rid] = f"Kategorie ({name})"
        if item.get("item_role_id"):
            rid = int(item["item_role_id"])
            to_grant[rid] = f"Item-Autorole ({name})"

    if pack_qty is None:
        pack_qty = sum(int(i.get("qty") or 1) for i in order_items)
    if pack_qty > 0:
        from utils.volume_discount import volume_role_ids_for_qty

        for rid, label in volume_role_ids_for_qty(settings, pack_qty):
            to_grant[rid] = label

    me = member.guild.me
    bot_top = me.top_role if me else None

    for rid, label in to_grant.items():
        role = member.guild.get_role(rid)
        if role is None:
            failed.append(f"{label}: Rolle `{rid}` nicht gefunden")
            continue
        if role >= member.guild.default_role and role.is_default():
            failed.append(f"{label}: @everyone nicht vergebbar")
            continue
        if role in member.roles:
            skipped.append(role.name)
            continue
        if bot_top is not None and role >= bot_top:
            failed.append(
                f"{label}: `{role.name}` liegt über/gleich der Bot-Rolle "
                "(Bot-Rolle höher schieben)"
            )
            continue
        if me and not me.guild_permissions.manage_roles:
            failed.append(f"{label}: Bot braucht Recht „Rollen verwalten“")
            continue
        try:
            await member.add_roles(
                role, reason=f"Shop Kauf bestätigt — {label}"
            )
            granted.append(role.name)
        except discord.Forbidden:
            failed.append(f"{label}: Keine Berechtigung für `{role.name}`")
        except discord.HTTPException as e:
            failed.append(f"{label}: Fehler ({e.status})")

    return {"granted": granted, "skipped": skipped, "failed": failed}



def collect_autorole_mentions(
    guild: discord.Guild, order_items: list[dict]
) -> list[str]:
    """Listet Item-/Kategorie-Autoroles für Ticket-Anzeige."""
    lines: list[str] = []
    seen: set[int] = set()
    for item in order_items:
        name = item.get("name_snapshot") or "Item"
        for key, kind in (
            ("item_role_id", "Item"),
            ("category_role_id", "Kategorie"),
        ):
            rid = item.get(key)
            if not rid or int(rid) in seen:
                continue
            seen.add(int(rid))
            role = guild.get_role(int(rid))
            if role:
                lines.append(f"• {kind} **{name}**: {role.mention}")
            else:
                lines.append(f"• {kind} **{name}**: Rolle `{rid}`")
    return lines
