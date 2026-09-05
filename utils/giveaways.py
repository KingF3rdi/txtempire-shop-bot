from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from utils.embeds import base_embed, format_price, success_embed
from utils.packs import resolve_pack_path
from utils.roles import grant_purchase_roles

if TYPE_CHECKING:
    from bot import ShopBot


def parse_duration(text: str) -> int:
    """Parst Dauer wie 30m, 2h, 1d → Sekunden."""
    raw = (text or "").strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("Dauer fehlt (z.B. 30m, 2h, 1d).")
    total = 0
    num = ""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    for ch in raw:
        if ch.isdigit():
            num += ch
            continue
        if ch in units and num:
            total += int(num) * units[ch]
            num = ""
        else:
            raise ValueError(f"Ungültige Dauer: `{text}` (z.B. `30m`, `2h`, `1d`).")
    if num:
        total += int(num) * 60  # reine Zahl = Minuten
    if total < 60:
        raise ValueError("Mindestens 1 Minute.")
    if total > 60 * 60 * 24 * 30:
        raise ValueError("Maximal 30 Tage.")
    return total


def item_to_order_snapshot(item: dict) -> dict:
    return {
        "item_id": int(item["id"]),
        "category_id": item.get("category_id"),
        "name_snapshot": item.get("name") or "Preis",
        "name": item.get("name") or "Preis",
        "qty": 1,
        "pack_dm_text": item.get("pack_dm_text") or "",
        "pack_link": item.get("pack_link") or "",
        "pack_file": item.get("pack_file") or "",
        "item_role_id": item.get("role_id"),
        "category_role_id": None,
        "price_snapshot": float(item.get("price") or 0),
    }


async def enrich_item_roles(bot: ShopBot, item: dict) -> dict:
    snap = item_to_order_snapshot(item)
    cat_id = item.get("category_id")
    if cat_id:
        cat = await bot.db.get_category(int(cat_id))
        if cat and cat.get("role_id"):
            snap["category_role_id"] = cat["role_id"]
    return snap


def build_giveaway_embed(
    *,
    prize_name: str,
    price: float,
    ends_at: datetime,
    winners_count: int,
    entries: int,
    host: discord.abc.User | None,
    status: str = "active",
    winners_mentions: str | None = None,
) -> discord.Embed:
    ends_ts = int(ends_at.replace(tzinfo=timezone.utc).timestamp()) if ends_at.tzinfo is None else int(ends_at.timestamp())
    if status == "active":
        embed = base_embed(
            "🎁 Giveaway",
            f"**Preis:** {prize_name}\n"
            f"**Wert:** {format_price(price)}\n"
            f"**Gewinner:** {winners_count}\n"
            f"**Ende:** <t:{ends_ts}:R> (<t:{ends_ts}:f>)\n"
            f"**Teilnehmer:** {entries}\n\n"
            "Klicke **Teilnehmen**, um mitzumachen.",
        )
    else:
        embed = success_embed(
            "🎁 Giveaway beendet",
            f"**Preis:** {prize_name}\n"
            f"**Teilnehmer:** {entries}\n"
            + (
                f"**Gewinner:** {winners_mentions}"
                if winners_mentions
                else "**Keine Teilnehmer** — kein Gewinner."
            ),
        )
    if host:
        embed.set_footer(text=f"Host: {host}")
    return embed


async def deliver_giveaway_prize(
    bot: ShopBot,
    guild: discord.Guild,
    member: discord.Member,
    item: dict,
) -> dict[str, str | bool]:
    """Rollen + Pack per DM an den Gewinner."""
    settings = await bot.db.ensure_guild(guild.id)
    snap = await enrich_item_roles(bot, item)
    role_result = await grant_purchase_roles(member, settings, [snap])

    name = snap["name_snapshot"]
    dm_text = (snap.get("pack_dm_text") or "").strip()
    link = (snap.get("pack_link") or "").strip()
    path = resolve_pack_path(snap.get("pack_file"))

    parts = [
        f"🎉 **Glückwunsch!** Du hast das Giveaway gewonnen.",
        f"**Preis:** {name}",
    ]
    if dm_text:
        parts.append(dm_text)
    if link:
        parts.append(f"Link: {link}")

    dm_ok = False
    try:
        content = "\n\n".join(parts)
        if path is not None:
            await member.send(
                content=content,
                file=discord.File(path, filename=path.name),
            )
        else:
            await member.send(content=content)
        dm_ok = True
    except discord.HTTPException:
        dm_ok = False

    return {
        "dm_ok": dm_ok,
        "roles_granted": ", ".join(role_result.get("granted") or []) or "—",
        "roles_failed": ", ".join(role_result.get("failed") or []) or "",
    }


async def finish_giveaway(
    bot: ShopBot,
    giveaway: dict,
    *,
    force: bool = False,
) -> list[int]:
    """Zieht Gewinner, liefert Preise, updated Message. Returns winner user ids."""
    gid = int(giveaway["id"])
    fresh = await bot.db.get_giveaway(gid)
    if not fresh or fresh["status"] != "active":
        return []

    # Atomar beenden
    await bot.db.update_giveaway(gid, status="ending")

    guild = bot.get_guild(int(fresh["guild_id"]))
    entries = await bot.db.list_giveaway_entries(gid)
    winners_n = min(int(fresh["winners_count"] or 1), len(entries))
    winners: list[int] = (
        random.sample(entries, winners_n) if winners_n > 0 else []
    )
    await bot.db.save_giveaway_winners(gid, winners)
    await bot.db.update_giveaway(gid, status="ended")

    item = await bot.db.get_item(int(fresh["item_id"]))
    mentions: list[str] = []
    channel = None
    if guild and fresh.get("channel_id"):
        ch = guild.get_channel(int(fresh["channel_id"]))
        if isinstance(ch, discord.TextChannel):
            channel = ch

    for uid in winners:
        member = None
        if guild:
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except discord.HTTPException:
                    member = None
        mentions.append(f"<@{uid}>")
        if member and item and guild:
            result = await deliver_giveaway_prize(bot, guild, member, item)
            await bot.db.mark_giveaway_winner_delivered(gid, uid)
            if channel and not result["dm_ok"]:
                try:
                    await channel.send(
                        f"{member.mention} Gewonnen — DMs geschlossen. "
                        f"Preis: **{fresh['prize_name']}**. Bitte Staff anschreiben."
                    )
                except discord.HTTPException:
                    pass

    # Message updaten
    if channel and fresh.get("message_id"):
        try:
            ends = datetime.strptime(
                str(fresh["ends_at"]), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            ends = datetime.now(timezone.utc)
        price = float(item["price"]) if item else 0.0
        host = bot.get_user(int(fresh["host_id"]))
        embed = build_giveaway_embed(
            prize_name=str(fresh["prize_name"]),
            price=price,
            ends_at=ends,
            winners_count=int(fresh["winners_count"] or 1),
            entries=len(entries),
            host=host,
            status="ended",
            winners_mentions=", ".join(mentions) if mentions else None,
        )
        try:
            msg = await channel.fetch_message(int(fresh["message_id"]))
            await msg.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass
        if mentions:
            try:
                await channel.send(
                    f"🎉 Giveaway beendet! Gewinner: {', '.join(mentions)}\n"
                    f"Preis **{fresh['prize_name']}** wurde per DM zugestellt "
                    "(Rollen + Pack)."
                )
            except discord.HTTPException:
                pass

    return winners
