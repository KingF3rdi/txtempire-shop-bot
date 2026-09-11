"""Duel Invsee — eigene Tabellen (keine Änderung an db/database.py nötig).

Alles hier ist strikt opt-in: ein Spieler taucht nur in
``duel_invsee_optin`` mit enabled=1 auf, wenn er selbst im Ingame-Mod
``/duelinvsee on`` ausgeführt hat. Ein Watch (jemand hat gekauft, das
Inventar von <opponent_ign> zu sehen) wird nur angelegt, wenn genau das
zutrifft — siehe cogs/duel_invsee.py.
"""
from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from bot import ShopBot


async def _ensure_column(bot: "ShopBot", table: str, column: str, ddl: str) -> None:
    try:
        await bot.db.db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        await bot.db.db.commit()
    except Exception as e:
        if "duplicate column" not in str(e).lower():
            raise


async def ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS duel_invsee_optin (
            guild_id INTEGER NOT NULL,
            ign TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (guild_id, ign)
        );
        CREATE TABLE IF NOT EXISTS duel_invsee_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            buyer_discord_id INTEGER NOT NULL,
            opponent_ign TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        """
    )
    await bot.db.db.commit()
    # DM-Tracking nachgerüstet — bestehende Tabellen bekommen die Spalten hier dazu.
    await _ensure_column(bot, "duel_invsee_watches", "dm_channel_id", "dm_channel_id INTEGER")
    await _ensure_column(bot, "duel_invsee_watches", "dm_message_id", "dm_message_id INTEGER")


async def is_opted_in(bot: "ShopBot", guild_id: int, ign: str) -> bool:
    row = await bot.db.fetchone(
        "SELECT enabled FROM duel_invsee_optin WHERE guild_id = ? AND lower(ign) = lower(?)",
        (guild_id, ign.strip()),
    )
    return bool(row and row["enabled"])


async def set_opt_in(bot: "ShopBot", guild_id: int, ign: str, enabled: bool) -> None:
    await bot.db.db.execute(
        """
        INSERT INTO duel_invsee_optin (guild_id, ign, enabled, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, ign) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at
        """,
        (guild_id, ign.strip(), 1 if enabled else 0, int(time.time())),
    )
    await bot.db.db.commit()


async def create_watch(
    bot: "ShopBot", guild_id: int, buyer_discord_id: int, opponent_ign: str, minutes: int
) -> str:
    token = secrets.token_urlsafe(9).replace("_", "").replace("-", "")[:12].lower()
    now = int(time.time())
    await bot.db.db.execute(
        """
        INSERT INTO duel_invsee_watches (guild_id, buyer_discord_id, opponent_ign, token, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (guild_id, buyer_discord_id, opponent_ign.strip(), token, now, now + minutes * 60),
    )
    await bot.db.db.commit()
    return token


async def find_active_watch_token(bot: "ShopBot", guild_id: int, ign: str) -> Optional[str]:
    row = await bot.db.fetchone(
        """
        SELECT token FROM duel_invsee_watches
        WHERE guild_id = ? AND lower(opponent_ign) = lower(?) AND expires_at > ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (guild_id, ign.strip(), int(time.time())),
    )
    return str(row["token"]) if row else None


async def find_active_watches(bot: "ShopBot", guild_id: int, ign: str) -> list[dict[str, Any]]:
    """Alle noch gültigen Watches für diesen IGN — normalerweise einer, aber
    mehrere Käufer könnten parallel denselben Gegner beobachten."""
    rows = await bot.db.fetchall(
        """
        SELECT id, buyer_discord_id, token, dm_channel_id, dm_message_id
        FROM duel_invsee_watches
        WHERE guild_id = ? AND lower(opponent_ign) = lower(?) AND expires_at > ?
        """,
        (guild_id, ign.strip(), int(time.time())),
    )
    return [dict(r) for r in rows]


async def set_watch_dm(bot: "ShopBot", watch_id: int, channel_id: int, message_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE duel_invsee_watches SET dm_channel_id = ?, dm_message_id = ? WHERE id = ?",
        (channel_id, message_id, watch_id),
    )
    await bot.db.db.commit()
