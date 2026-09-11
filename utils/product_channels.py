from __future__ import annotations

import discord


async def grant_purchase_channels(
    member: discord.Member,
    order_items: list[dict],
) -> dict[str, list[str]]:
    """Schaltet Kategorie-/Item-verknüpfte private Channels für den Käufer frei."""
    granted: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    to_grant: dict[int, str] = {}
    for item in order_items:
        name = item.get("name_snapshot") or "Item"
        if item.get("category_channel_id"):
            cid = int(item["category_channel_id"])
            to_grant[cid] = f"Kategorie ({name})"
        if item.get("item_channel_id"):
            cid = int(item["item_channel_id"])
            to_grant[cid] = f"Item-Channel ({name})"

    guild = member.guild
    me = guild.me

    for cid, label in to_grant.items():
        channel = guild.get_channel(cid)
        if channel is None:
            failed.append(f"{label}: Channel `{cid}` nicht gefunden")
            continue
        if me is None or not channel.permissions_for(me).manage_roles:
            failed.append(
                f"{label}: Bot braucht „Rollen verwalten“ in <#{cid}>"
            )
            continue
        existing = channel.overwrites_for(member)
        if existing.view_channel:
            skipped.append(channel.name)
            continue
        try:
            await channel.set_permissions(
                member,
                view_channel=True,
                read_message_history=True,
                reason=f"Shop Kauf bestätigt — {label}",
            )
            granted.append(channel.name)
        except discord.Forbidden:
            failed.append(f"{label}: Keine Berechtigung für #{channel.name}")
        except discord.HTTPException as e:
            failed.append(f"{label}: Fehler ({e.status})")

    return {"granted": granted, "skipped": skipped, "failed": failed}


def collect_autochannel_mentions(order_items: list[dict]) -> list[str]:
    """Listet Item-/Kategorie-verknüpfte Channels für Ticket-Anzeige (vor Bestätigung)."""
    lines: list[str] = []
    seen: set[int] = set()
    for item in order_items:
        name = item.get("name_snapshot") or "Item"
        for key, kind in (
            ("item_channel_id", "Item"),
            ("category_channel_id", "Kategorie"),
        ):
            cid = item.get(key)
            if not cid or int(cid) in seen:
                continue
            seen.add(int(cid))
            lines.append(f"• {kind} **{name}**: <#{cid}>")
    return lines
