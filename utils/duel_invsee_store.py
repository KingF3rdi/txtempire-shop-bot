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
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bot import ShopBot


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
