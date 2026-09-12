"""Erkennt eingehende Ingame-Zahlungen auch für die Mini-Shop-Tickets
(Pack, Spawner, Schematic, Account, Tier-Boost) — nicht nur die generische
`orders`-Tabelle.

Jede dieser Tabellen liefert eigene Tickets mit eigener Liefer-/
Abschlusslogik (Datei-Upload, Zugangsdaten übergeben, Boost durchführen),
die sich nicht automatisch fertigstellen lässt. Eine erkannte Zahlung wird
deshalb NICHT automatisch bestätigt (status bleibt 'pending'), sondern nur
als Hinweis ins Ticket gepostet — Staff bestätigt wie gewohnt per Button.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Optional

import discord

from utils.embeds import format_price, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


class _TableSpec(NamedTuple):
    table: str
    price_col: str
    label: str


_TABLES: tuple[_TableSpec, ...] = (
    _TableSpec("pack_orders", "price", "Custom-Pack-Ticket"),
    _TableSpec("spawner_tickets", "total", "Spawner-Ticket"),
    _TableSpec("schematic_tickets", "price", "Schematic-Ticket"),
    _TableSpec("account_tickets", "price", "Account-Ticket"),
    _TableSpec("boost_tickets", "price", "Boost-Ticket"),
)

_TOLERANCE = 1.0


async def find_matching_pending_tickets(
    bot: "ShopBot", guild_id: int, user_id: int, amount: float
) -> list[dict]:
    """Sucht in allen Mini-Shop-Ticket-Tabellen nach offenen (status='pending')
    Tickets dieses Nutzers, deren Preis in etwa zum gezahlten Betrag passt."""
    matches: list[dict] = []
    for spec in _TABLES:
        rows = await bot.db.fetchall(
            f"""
            SELECT id, ticket_channel_id, {spec.price_col} AS price
            FROM {spec.table}
            WHERE guild_id = ? AND user_id = ? AND status = 'pending'
              AND ticket_channel_id IS NOT NULL
              AND ABS({spec.price_col} - ?) <= ?
            ORDER BY id DESC
            """,
            (guild_id, user_id, amount, _TOLERANCE),
        )
        for row in rows:
            matches.append(
                {
                    "table": spec.table,
                    "label": spec.label,
                    "id": int(row["id"]),
                    "ticket_channel_id": int(row["ticket_channel_id"]),
                    "price": float(row["price"]),
                }
            )
    return matches


async def flag_detected_payments(
    bot: "ShopBot", guild: discord.Guild, matches: list[dict], *, ign: str, amount: float
) -> int:
    """Postet einen Hinweis in jedes erkannte Ticket. Ändert den Status NICHT —
    Staff bestätigt weiterhin manuell per Button (Datei-Upload/Zugangsdaten/
    Boost-Durchführung lassen sich nicht automatisch abschließen)."""
    notified = 0
    for match in matches:
        channel = guild.get_channel(match["ticket_channel_id"])
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            await channel.send(
                embed=success_embed(
                    "💰 Zahlung erkannt",
                    f"Ingame-Zahlung von **{ign}** über **{format_price(amount)}** erkannt "
                    f"({match['label']}, erwartet: {format_price(match['price'])}).\n"
                    "Bitte prüfen und mit **✅ Bestätigen** abschließen.",
                )
            )
            notified += 1
        except discord.HTTPException:
            pass
    return notified
