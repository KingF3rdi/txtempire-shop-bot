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
    price_cols: tuple[str, ...]
    label: str


_TABLES: tuple[_TableSpec, ...] = (
    # pack_orders: Echtgeld-Preis ODER fester Ingame-Preis (custom_pack.py).
    _TableSpec("pack_orders", ("price", "ingame_price"), "Custom-Pack-Ticket"),
    _TableSpec("spawner_tickets", ("total",), "Spawner-Ticket"),
    _TableSpec("schematic_tickets", ("price",), "Schematic-Ticket"),
    _TableSpec("account_tickets", ("price",), "Account-Ticket"),
    _TableSpec("boost_tickets", ("price",), "Boost-Ticket"),
)

_TOLERANCE = 1.0


async def find_matching_pending_tickets(
    bot: "ShopBot", guild_id: int, user_id: int, amount: float
) -> list[dict]:
    """Sucht in allen Mini-Shop-Ticket-Tabellen nach offenen (status='pending')
    Tickets dieses Nutzers, deren Preis (irgendeine der price_cols) in etwa
    zum gezahlten Betrag passt."""
    matches: list[dict] = []
    for spec in _TABLES:
        cols = spec.price_cols
        where_price = " OR ".join(f"ABS({c} - ?) <= ?" for c in cols)
        params: list = [guild_id, user_id]
        for _ in cols:
            params.extend([amount, _TOLERANCE])
        rows = await bot.db.fetchall(
            f"""
            SELECT id, ticket_channel_id, {", ".join(cols)}
            FROM {spec.table}
            WHERE guild_id = ? AND user_id = ? AND status = 'pending'
              AND ticket_channel_id IS NOT NULL
              AND ({where_price})
            ORDER BY id DESC
            """,
            tuple(params),
        )
        for row in rows:
            # Bei mehreren price_cols die anzeigen, die am nächsten am gezahlten Betrag liegt.
            candidates = [float(row[c]) for c in cols if row[c] is not None]
            price = min(candidates, key=lambda p: abs(p - amount)) if candidates else 0.0
            matches.append(
                {
                    "table": spec.table,
                    "label": spec.label,
                    "id": int(row["id"]),
                    "ticket_channel_id": int(row["ticket_channel_id"]),
                    "price": price,
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


async def _self_check() -> None:
    """ponytail: Nachweis, dass die Mehrspalten-OR-Suche (price/ingame_price)
    sowohl den Echtgeld- als auch den Ingame-Preis eines pack_orders-Tickets
    findet und den jeweils passenderen Wert für die Anzeige zurückgibt."""
    import aiosqlite

    class _FakeDB:
        def __init__(self, conn: aiosqlite.Connection) -> None:
            self.db = conn

        async def fetchall(self, query: str, params: tuple = ()) -> list:
            async with self.db.execute(query, params) as cur:
                return await cur.fetchall()

    class _FakeBot:
        def __init__(self, db: _FakeDB) -> None:
            self.db = db

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        """
        CREATE TABLE pack_orders (
            id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER,
            ticket_channel_id INTEGER, status TEXT, price REAL, ingame_price REAL
        )
        """
    )
    # Echtgeld-Preis 0.30, fester Ingame-Preis 50000.
    await conn.execute(
        "INSERT INTO pack_orders VALUES (1, 1, 1, 111, 'pending', 0.30, 50000)"
    )
    # Andere Mini-Shop-Tabellen müssen existieren (auch ohne Zeilen), da
    # find_matching_pending_tickets alle _TABLES abfragt.
    for table, col in (
        ("spawner_tickets", "total"),
        ("schematic_tickets", "price"),
        ("account_tickets", "price"),
        ("boost_tickets", "price"),
    ):
        await conn.execute(
            f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, guild_id INTEGER, "
            f"user_id INTEGER, ticket_channel_id INTEGER, status TEXT, {col} REAL)"
        )
    await conn.commit()
    bot = _FakeBot(_FakeDB(conn))

    money_hits = await find_matching_pending_tickets(bot, 1, 1, 0.30)
    assert len(money_hits) == 1 and abs(money_hits[0]["price"] - 0.30) < 1e-6

    ingame_hits = await find_matching_pending_tickets(bot, 1, 1, 50000)
    assert len(ingame_hits) == 1 and ingame_hits[0]["price"] == 50000.0

    no_hits = await find_matching_pending_tickets(bot, 1, 1, 12345)
    assert no_hits == []

    await conn.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_self_check())
    print("OK")
