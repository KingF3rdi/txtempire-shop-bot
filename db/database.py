from __future__ import annotations

import json
import aiosqlite
from pathlib import Path
from typing import Any, Optional


OPEN_STATUSES = ("pending", "awaiting_proof", "awaiting_confirm")


def _utc_now_str() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._create_tables()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected")
        return self._db

    async def _create_tables(self) -> None:
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                customer_role_id INTEGER,
                staff_role_id INTEGER,
                ticket_category_id INTEGER,
                vouch_channel_id INTEGER,
                max_open_tickets INTEGER NOT NULL DEFAULT 1,
                payee_a_label TEXT NOT NULL DEFAULT 'TxtEmpire',
                payee_a_details TEXT NOT NULL DEFAULT '',
                payee_b_label TEXT NOT NULL DEFAULT '',
                payee_b_details TEXT NOT NULL DEFAULT '',
                delete_on_cancel INTEGER NOT NULL DEFAULT 0,
                scan_premium_role_id INTEGER,
                scan_log_channel_id INTEGER,
                changelog_channel_id INTEGER,
                msg_channel_id INTEGER,
                volume_role_10_id INTEGER,
                volume_role_15_id INTEGER,
                volume_role_20_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                role_id INTEGER,
                emoji TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price REAL NOT NULL,
                pack_dm_text TEXT NOT NULL DEFAULT '',
                pack_link TEXT NOT NULL DEFAULT '',
                pack_file TEXT NOT NULL DEFAULT '',
                role_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS carts (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                qty INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, guild_id, item_id),
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                ticket_channel_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                total REAL NOT NULL DEFAULT 0,
                half_a REAL NOT NULL DEFAULT 0,
                half_b REAL NOT NULL DEFAULT 0,
                ign TEXT,
                vouch_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT,
                order_number INTEGER
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                item_id INTEGER,
                category_id INTEGER,
                name_snapshot TEXT NOT NULL,
                price_snapshot REAL NOT NULL,
                qty INTEGER NOT NULL DEFAULT 1,
                pack_dm_text TEXT NOT NULL DEFAULT '',
                pack_link TEXT NOT NULL DEFAULT '',
                pack_file TEXT NOT NULL DEFAULT '',
                item_role_id INTEGER,
                category_role_id INTEGER,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS payment_proofs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                attachment_url TEXT NOT NULL,
                uploaded_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS buy_panel_slots (
                guild_id INTEGER NOT NULL,
                slot INTEGER NOT NULL CHECK (slot IN (1, 2)),
                filter_mode TEXT NOT NULL DEFAULT 'all',
                category_ids TEXT NOT NULL DEFAULT '[]',
                title TEXT,
                channel_id INTEGER,
                message_id INTEGER,
                credits_enabled INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, slot)
            );

            CREATE TABLE IF NOT EXISTS user_credits (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                balance REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS discount_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                discount_type TEXT NOT NULL CHECK (discount_type IN ('percent', 'amount')),
                discount_value REAL NOT NULL,
                max_uses INTEGER,
                uses INTEGER NOT NULL DEFAULT 0,
                max_per_user INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                label TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'rabatt',
                created_by INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (guild_id, code)
            );

            CREATE TABLE IF NOT EXISTS scan_premium (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                unlimited INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS scan_usage (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, day)
            );

            CREATE TABLE IF NOT EXISTS scan_panel (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                message_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS daily_deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                discount_type TEXT NOT NULL CHECK (discount_type IN ('percent', 'amount')),
                discount_value REAL NOT NULL,
                original_price REAL NOT NULL,
                deal_price REAL NOT NULL,
                channel_id INTEGER,
                message_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            );
            """
        )
        await self.db.commit()
        await self._ensure_columns()
        await self._ensure_order_numbers()

    async def _ensure_columns(self) -> None:
        """Adds columns for DBs created before pack_file existed."""
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS buy_panel_slots (
                    guild_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL CHECK (slot IN (1, 2)),
                    filter_mode TEXT NOT NULL DEFAULT 'all',
                    category_ids TEXT NOT NULL DEFAULT '[]',
                    title TEXT,
                    PRIMARY KEY (guild_id, slot)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    discount_type TEXT NOT NULL CHECK (discount_type IN ('percent', 'amount')),
                    discount_value REAL NOT NULL,
                    original_price REAL NOT NULL,
                    deal_price REAL NOT NULL,
                    channel_id INTEGER,
                    message_id INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    expires_at TEXT,
                    created_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        for table, column, typedef in (
            ("items", "pack_file", "TEXT NOT NULL DEFAULT ''"),
            ("order_items", "pack_file", "TEXT NOT NULL DEFAULT ''"),
            ("orders", "order_number", "INTEGER"),
            ("categories", "api_id", "INTEGER"),
            ("items", "api_id", "INTEGER"),
            ("buy_panel_slots", "channel_id", "INTEGER"),
            ("buy_panel_slots", "message_id", "INTEGER"),
            ("buy_panel_slots", "credits_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("orders", "credits_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("orders", "order_kind", "TEXT NOT NULL DEFAULT 'shop'"),
            ("orders", "credits_amount", "REAL"),
            ("orders", "paid_with_credits", "INTEGER NOT NULL DEFAULT 0"),
            ("orders", "source_panel_slot", "INTEGER"),
            ("orders", "discount_code", "TEXT"),
            ("orders", "discount_code_id", "INTEGER"),
            ("orders", "discount_amount", "REAL NOT NULL DEFAULT 0"),
            ("orders", "original_total", "REAL"),
            ("guild_settings", "scan_premium_role_id", "INTEGER"),
            ("guild_settings", "scan_log_channel_id", "INTEGER"),
            ("guild_settings", "changelog_channel_id", "INTEGER"),
            ("guild_settings", "msg_channel_id", "INTEGER"),
            ("guild_settings", "vouch_stats_message_id", "INTEGER"),
            ("orders", "vouch_rating", "INTEGER"),
            ("discount_codes", "kind", "TEXT NOT NULL DEFAULT 'rabatt'"),
            ("orders", "volume_discount_pct", "REAL NOT NULL DEFAULT 0"),
            ("orders", "volume_discount_amount", "REAL NOT NULL DEFAULT 0"),
            ("orders", "pack_qty", "INTEGER NOT NULL DEFAULT 0"),
            ("guild_settings", "volume_role_10_id", "INTEGER"),
            ("guild_settings", "volume_role_15_id", "INTEGER"),
            ("guild_settings", "volume_role_20_id", "INTEGER"),
            ("scan_premium", "unlimited", "INTEGER NOT NULL DEFAULT 0"),
            ("guild_settings", "scan_price_14", "REAL"),
            ("guild_settings", "scan_price_30", "REAL"),
            ("guild_settings", "scan_credits_14", "REAL"),
            ("guild_settings", "scan_credits_30", "REAL"),
            ("guild_settings", "texturepack_role_id", "INTEGER"),
            ("discount_codes", "owner_user_id", "INTEGER"),
        ):
            try:
                await self.db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {typedef}"
                )
                await self.db.commit()
            except Exception:
                pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_credits (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    balance REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS discount_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    discount_type TEXT NOT NULL CHECK (discount_type IN ('percent', 'amount')),
                    discount_value REAL NOT NULL,
                    max_uses INTEGER,
                    uses INTEGER NOT NULL DEFAULT 0,
                    max_per_user INTEGER NOT NULL DEFAULT 1,
                    active INTEGER NOT NULL DEFAULT 1,
                    label TEXT NOT NULL DEFAULT '',
                    created_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE (guild_id, code)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_premium (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    unlimited INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_usage (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, day)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_panel (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    message_id INTEGER
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'other',
                    finding_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS service_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    ticket_type TEXT NOT NULL,
                    channel_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'open',
                    subject TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS service_panels (
                    guild_id INTEGER NOT NULL,
                    panel_type TEXT NOT NULL,
                    channel_id INTEGER,
                    message_id INTEGER,
                    PRIMARY KEY (guild_id, panel_type)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS boost_claims (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    boost_count INTEGER NOT NULL DEFAULT 1,
                    packs_allowed INTEGER NOT NULL DEFAULT 5,
                    packs_claimed INTEGER NOT NULL DEFAULT 0,
                    claimed_item_ids TEXT NOT NULL DEFAULT '[]',
                    last_thanks_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS payback_xp (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    xp INTEGER NOT NULL DEFAULT 0,
                    last_daily TEXT,
                    rewards_claimed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS giveaways (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER,
                    message_id INTEGER,
                    item_id INTEGER NOT NULL,
                    prize_name TEXT NOT NULL DEFAULT '',
                    winners_count INTEGER NOT NULL DEFAULT 1,
                    ends_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    host_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS giveaway_entries (
                    giveaway_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (giveaway_id, user_id),
                    FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS giveaway_winners (
                    giveaway_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (giveaway_id, user_id)
                )
                """
            )
            await self.db.commit()
        except Exception:
            pass
        try:
            await self.db.execute(
                """
                UPDATE guild_settings
                SET payee_a_label = 'TxtEmpire'
                WHERE payee_a_label IN ('TxTHub', 'Empfänger A', '')
                """
            )
            await self.db.commit()
        except Exception:
            pass

    async def _ensure_order_numbers(self) -> None:
        """Vergibt fortlaufende Bestellnummern pro Server (1, 2, 3, …)."""
        rows = await self.fetchall(
            """
            SELECT id, guild_id FROM orders
            WHERE order_number IS NULL
            ORDER BY guild_id ASC, id ASC
            """
        )
        last_guild: int | None = None
        next_n = 0
        for row in rows:
            guild_id = int(row["guild_id"])
            if guild_id != last_guild:
                mx = await self.fetchone(
                    """
                    SELECT COALESCE(MAX(order_number), 0) AS mx
                    FROM orders WHERE guild_id = ?
                    """,
                    (guild_id,),
                )
                next_n = int(mx["mx"]) + 1 if mx else 1
                last_guild = guild_id
            await self.db.execute(
                "UPDATE orders SET order_number = ? WHERE id = ?",
                (next_n, int(row["id"])),
            )
            next_n += 1
        if rows:
            await self.db.commit()
        try:
            await self.db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_guild_number
                ON orders (guild_id, order_number)
                """
            )
            await self.db.commit()
        except Exception:
            pass

    # ── Guild settings ──────────────────────────────────────────────

    async def ensure_guild(self, guild_id: int) -> dict[str, Any]:
        row = await self.fetchone(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        if row:
            return dict(row)
        await self.db.execute(
            "INSERT INTO guild_settings (guild_id) VALUES (?)", (guild_id,)
        )
        await self.db.commit()
        row = await self.fetchone(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        return dict(row)  # type: ignore[arg-type]

    async def update_guild_settings(self, guild_id: int, **fields: Any) -> None:
        await self.ensure_guild(guild_id)
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self.db.execute(
            f"UPDATE guild_settings SET {cols} WHERE guild_id = ?", values
        )
        await self.db.commit()

    # ── Categories ──────────────────────────────────────────────────

    async def add_category(
        self,
        guild_id: int,
        name: str,
        description: str = "",
        role_id: int | None = None,
        emoji: str = "",
        sort_order: int = 0,
    ) -> int:
        cur = await self.db.execute(
            """
            INSERT INTO categories (guild_id, name, description, role_id, emoji, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, name, description, role_id, emoji, sort_order),
        )
        await self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def update_category(self, category_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [category_id]
        await self.db.execute(
            f"UPDATE categories SET {cols} WHERE id = ?", values
        )
        await self.db.commit()

    async def delete_category(self, category_id: int) -> None:
        await self.db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        await self.db.commit()

    async def get_category(self, category_id: int) -> dict[str, Any] | None:
        row = await self.fetchone("SELECT * FROM categories WHERE id = ?", (category_id,))
        return dict(row) if row else None

    async def list_categories(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT * FROM categories
            WHERE guild_id = ?
            ORDER BY sort_order ASC, name ASC
            """,
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def list_all_categories(self) -> list[dict[str, Any]]:
        """Alle Kategorien (für persistente Buy-Panel-Views beim Bot-Start)."""
        rows = await self.fetchall(
            """
            SELECT * FROM categories
            ORDER BY guild_id ASC, sort_order ASC, name ASC
            """
        )
        return [dict(r) for r in rows]

    # ── Buy panel slots (1 & 2) ───────────────────────────────────

    async def get_buy_panel_slot(
        self, guild_id: int, slot: int
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM buy_panel_slots WHERE guild_id = ? AND slot = ?",
            (guild_id, slot),
        )
        return dict(row) if row else None

    async def set_buy_panel_slot(
        self,
        guild_id: int,
        slot: int,
        *,
        filter_mode: str,
        category_ids: list[int],
        title: str | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
        credits_enabled: bool | None = None,
    ) -> None:
        ids_json = json.dumps(sorted({int(i) for i in category_ids}))
        existing = await self.get_buy_panel_slot(guild_id, slot)
        if channel_id is None and existing:
            channel_id = existing.get("channel_id")
        if message_id is None and existing:
            message_id = existing.get("message_id")
        if credits_enabled is None:
            credits_flag = int(existing.get("credits_enabled") or 0) if existing else 0
        else:
            credits_flag = 1 if credits_enabled else 0
        await self.db.execute(
            """
            INSERT INTO buy_panel_slots
              (guild_id, slot, filter_mode, category_ids, title, channel_id, message_id, credits_enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, slot) DO UPDATE SET
                filter_mode = excluded.filter_mode,
                category_ids = excluded.category_ids,
                title = COALESCE(excluded.title, buy_panel_slots.title),
                channel_id = COALESCE(excluded.channel_id, buy_panel_slots.channel_id),
                message_id = COALESCE(excluded.message_id, buy_panel_slots.message_id),
                credits_enabled = excluded.credits_enabled
            """,
            (
                guild_id,
                slot,
                filter_mode,
                ids_json,
                title,
                channel_id,
                message_id,
                credits_flag,
            ),
        )
        await self.db.commit()

    async def set_buy_panel_credits(
        self, guild_id: int, slot: int, enabled: bool
    ) -> None:
        await self.ensure_buy_panel_slot(guild_id, slot)
        await self.db.execute(
            """
            UPDATE buy_panel_slots
            SET credits_enabled = ?
            WHERE guild_id = ? AND slot = ?
            """,
            (1 if enabled else 0, guild_id, slot),
        )
        await self.db.commit()

    async def update_buy_panel_message(
        self, guild_id: int, slot: int, *, channel_id: int, message_id: int
    ) -> None:
        await self.ensure_buy_panel_slot(guild_id, slot)
        await self.db.execute(
            """
            UPDATE buy_panel_slots
            SET channel_id = ?, message_id = ?
            WHERE guild_id = ? AND slot = ?
            """,
            (channel_id, message_id, guild_id, slot),
        )
        await self.db.commit()

    async def ensure_buy_panel_slot(self, guild_id: int, slot: int) -> dict[str, Any]:
        row = await self.get_buy_panel_slot(guild_id, slot)
        if row:
            return dict(row)
        await self.set_buy_panel_slot(
            guild_id, slot, filter_mode="all", category_ids=[], title=None
        )
        row = await self.get_buy_panel_slot(guild_id, slot)
        if not row:
            raise RuntimeError(f"buy_panel_slots konnte nicht erstellt werden (slot {slot})")
        return dict(row)

    async def list_guilds_with_panel_messages(self) -> list[int]:
        rows = await self.fetchall(
            """
            SELECT DISTINCT guild_id FROM buy_panel_slots
            WHERE message_id IS NOT NULL AND channel_id IS NOT NULL
            """
        )
        return [int(r["guild_id"]) for r in rows]

    async def get_category_by_api_id(
        self, guild_id: int, api_id: int
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM categories WHERE guild_id = ? AND api_id = ?",
            (guild_id, api_id),
        )
        return dict(row) if row else None

    async def upsert_category_from_api(
        self,
        guild_id: int,
        api_id: int,
        name: str,
        description: str = "",
        sort_order: int = 0,
    ) -> int:
        existing = await self.get_category_by_api_id(guild_id, api_id)
        if existing:
            await self.update_category(
                int(existing["id"]),
                name=name,
                description=description,
                sort_order=sort_order,
            )
            return int(existing["id"])
        cur = await self.db.execute(
            """
            INSERT INTO categories (guild_id, name, description, role_id, emoji, sort_order, api_id)
            VALUES (?, ?, ?, NULL, '', ?, ?)
            """,
            (guild_id, name, description, sort_order, api_id),
        )
        await self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def delete_api_categories_not_in(
        self, guild_id: int, api_ids: list[int]
    ) -> int:
        if api_ids:
            placeholders = ",".join("?" for _ in api_ids)
            cur = await self.db.execute(
                f"""
                DELETE FROM categories
                WHERE guild_id = ? AND api_id IS NOT NULL
                  AND api_id NOT IN ({placeholders})
                """,
                (guild_id, *api_ids),
            )
        else:
            cur = await self.db.execute(
                """
                DELETE FROM categories
                WHERE guild_id = ? AND api_id IS NOT NULL
                """,
                (guild_id,),
            )
        await self.db.commit()
        return cur.rowcount

    async def get_item_by_api_id(
        self, guild_id: int, api_id: int
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM items WHERE guild_id = ? AND api_id = ?",
            (guild_id, api_id),
        )
        return dict(row) if row else None

    async def upsert_item_from_api(
        self,
        guild_id: int,
        category_id: int,
        api_id: int,
        name: str,
        price: float,
        description: str = "",
        pack_dm_text: str = "",
        pack_link: str = "",
        pack_file: str = "",
        role_id: int | None = None,
    ) -> int:
        fields = {
            "category_id": category_id,
            "name": name,
            "price": price,
            "description": description,
            "pack_dm_text": pack_dm_text,
            "pack_link": pack_link,
            "pack_file": pack_file,
            "role_id": role_id,
            "active": 1,
        }
        existing = await self.get_item_by_api_id(guild_id, api_id)
        if existing:
            await self.update_item(int(existing["id"]), **fields)
            return int(existing["id"])
        cur = await self.db.execute(
            """
            INSERT INTO items
              (category_id, guild_id, name, description, price,
               pack_dm_text, pack_link, pack_file, role_id, active, api_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                category_id,
                guild_id,
                name,
                description,
                price,
                pack_dm_text,
                pack_link,
                pack_file,
                role_id,
                api_id,
            ),
        )
        await self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def delete_api_items_not_in(self, guild_id: int, api_ids: list[int]) -> int:
        if api_ids:
            placeholders = ",".join("?" for _ in api_ids)
            cur = await self.db.execute(
                f"""
                DELETE FROM items
                WHERE guild_id = ? AND api_id IS NOT NULL
                  AND api_id NOT IN ({placeholders})
                """,
                (guild_id, *api_ids),
            )
        else:
            cur = await self.db.execute(
                """
                DELETE FROM items
                WHERE guild_id = ? AND api_id IS NOT NULL
                """,
                (guild_id,),
            )
        await self.db.commit()
        return cur.rowcount

    # ── Items ───────────────────────────────────────────────────────

    async def add_item(
        self,
        guild_id: int,
        category_id: int,
        name: str,
        price: float,
        description: str = "",
        pack_dm_text: str = "",
        pack_link: str = "",
        pack_file: str = "",
        role_id: int | None = None,
    ) -> int:
        cur = await self.db.execute(
            """
            INSERT INTO items
              (category_id, guild_id, name, description, price, pack_dm_text, pack_link, pack_file, role_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                category_id,
                guild_id,
                name,
                description,
                price,
                pack_dm_text,
                pack_link,
                pack_file,
                role_id,
            ),
        )
        await self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def update_item(self, item_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [item_id]
        await self.db.execute(f"UPDATE items SET {cols} WHERE id = ?", values)
        await self.db.commit()

    async def delete_item(self, item_id: int) -> None:
        await self.db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        await self.db.commit()

    async def get_item(self, item_id: int) -> dict[str, Any] | None:
        row = await self.fetchone("SELECT * FROM items WHERE id = ?", (item_id,))
        return dict(row) if row else None

    async def list_items(
        self, guild_id: int, category_id: int | None = None, active_only: bool = True
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM items WHERE guild_id = ?"
        params: list[Any] = [guild_id]
        if category_id is not None:
            query += " AND category_id = ?"
            params.append(category_id)
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY name ASC"
        rows = await self.fetchall(query, tuple(params))
        return [dict(r) for r in rows]

    # ── Cart ────────────────────────────────────────────────────────

    async def cart_add(
        self, user_id: int, guild_id: int, item_id: int, qty: int = 1
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO carts (user_id, guild_id, item_id, qty)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, guild_id, item_id)
            DO UPDATE SET qty = qty + excluded.qty
            """,
            (user_id, guild_id, item_id, qty),
        )
        await self.db.commit()

    async def cart_set_qty(
        self, user_id: int, guild_id: int, item_id: int, qty: int
    ) -> None:
        if qty <= 0:
            await self.cart_remove(user_id, guild_id, item_id)
            return
        await self.db.execute(
            """
            UPDATE carts SET qty = ?
            WHERE user_id = ? AND guild_id = ? AND item_id = ?
            """,
            (qty, user_id, guild_id, item_id),
        )
        await self.db.commit()

    async def cart_remove(self, user_id: int, guild_id: int, item_id: int) -> None:
        await self.db.execute(
            "DELETE FROM carts WHERE user_id = ? AND guild_id = ? AND item_id = ?",
            (user_id, guild_id, item_id),
        )
        await self.db.commit()

    async def cart_clear(self, user_id: int, guild_id: int) -> None:
        await self.db.execute(
            "DELETE FROM carts WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        await self.db.commit()

    async def cart_get(self, user_id: int, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT c.item_id, c.qty, i.name, i.price, i.category_id,
                   i.pack_dm_text, i.pack_link, i.pack_file, i.role_id AS item_role_id,
                   cat.role_id AS category_role_id, cat.name AS category_name
            FROM carts c
            JOIN items i ON i.id = c.item_id
            JOIN categories cat ON cat.id = i.category_id
            WHERE c.user_id = ? AND c.guild_id = ? AND i.active = 1
            ORDER BY i.name
            """,
            (user_id, guild_id),
        )
        return [dict(r) for r in rows]

    async def cart_total(self, user_id: int, guild_id: int) -> float:
        row = await self.fetchone(
            """
            SELECT COALESCE(SUM(c.qty * i.price), 0) AS total
            FROM carts c
            JOIN items i ON i.id = c.item_id
            WHERE c.user_id = ? AND c.guild_id = ? AND i.active = 1
            """,
            (user_id, guild_id),
        )
        return float(row["total"]) if row else 0.0

    # ── Orders ──────────────────────────────────────────────────────

    async def create_order(
        self,
        guild_id: int,
        user_id: int,
        cart_rows: list[dict[str, Any]],
        ticket_channel_id: int | None = None,
        *,
        credits_enabled: bool = False,
        order_kind: str = "shop",
        credits_amount: float | None = None,
        source_panel_slot: int | None = None,
    ) -> int:
        kind = order_kind if order_kind in ("shop", "credits", "scan_premium") else "shop"
        subtotal = sum(float(r["price"]) * int(r["qty"]) for r in cart_rows)
        pack_qty = sum(int(r.get("qty") or 1) for r in cart_rows)
        volume_pct = 0.0
        volume_amt = 0.0
        total = round(float(subtotal), 2)
        original_total = None
        if kind == "shop":
            from utils.volume_discount import apply_volume_discount

            total, volume_amt, volume_pct = apply_volume_discount(subtotal, pack_qty)
            if volume_amt > 0:
                original_total = round(float(subtotal), 2)

        mx = await self.fetchone(
            """
            SELECT COALESCE(MAX(order_number), 0) AS mx
            FROM orders WHERE guild_id = ?
            """,
            (guild_id,),
        )
        order_number = int(mx["mx"]) + 1 if mx else 1
        cur = await self.db.execute(
            """
            INSERT INTO orders
              (guild_id, user_id, ticket_channel_id, status, total, half_a, half_b,
               order_number, credits_enabled, order_kind, credits_amount, source_panel_slot,
               original_total, volume_discount_pct, volume_discount_amount, pack_qty)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                ticket_channel_id,
                total,
                total,
                0.0,
                order_number,
                1 if credits_enabled else 0,
                kind,
                credits_amount,
                source_panel_slot,
                original_total,
                float(volume_pct),
                float(volume_amt),
                int(pack_qty) if kind == "shop" else 0,
            ),
        )
        order_id = cur.lastrowid
        for r in cart_rows:
            await self.db.execute(
                """
                INSERT INTO order_items
                  (order_id, item_id, category_id, name_snapshot, price_snapshot, qty,
                   pack_dm_text, pack_link, pack_file, item_role_id, category_role_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    r.get("item_id"),
                    r.get("category_id"),
                    r["name"],
                    float(r["price"]),
                    int(r["qty"]),
                    r.get("pack_dm_text") or "",
                    r.get("pack_link") or "",
                    r.get("pack_file") or "",
                    r.get("item_role_id"),
                    r.get("category_role_id"),
                ),
            )
        await self.db.commit()
        return order_id  # type: ignore[return-value]

    # ── User credits ────────────────────────────────────────────────

    async def get_credits(self, guild_id: int, user_id: int) -> float:
        row = await self.fetchone(
            "SELECT balance FROM user_credits WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return float(row["balance"]) if row else 0.0

    async def set_credits(self, guild_id: int, user_id: int, balance: float) -> float:
        bal = max(0.0, round(float(balance), 2))
        await self.db.execute(
            """
            INSERT INTO user_credits (guild_id, user_id, balance)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = excluded.balance
            """,
            (guild_id, user_id, bal),
        )
        await self.db.commit()
        return bal

    async def add_credits(self, guild_id: int, user_id: int, amount: float) -> float:
        """Addiert Credits (auch negativ zum Abziehen ohne Balance-Check)."""
        current = await self.get_credits(guild_id, user_id)
        return await self.set_credits(guild_id, user_id, current + float(amount))

    async def try_deduct_credits(
        self, guild_id: int, user_id: int, amount: float
    ) -> bool:
        """Zieht Credits atomar ab. False wenn Guthaben nicht reicht."""
        need = round(float(amount), 2)
        if need <= 0:
            return True
        await self.db.execute(
            """
            INSERT INTO user_credits (guild_id, user_id, balance)
            VALUES (?, ?, 0)
            ON CONFLICT(guild_id, user_id) DO NOTHING
            """,
            (guild_id, user_id),
        )
        cur = await self.db.execute(
            """
            UPDATE user_credits
            SET balance = ROUND(balance - ?, 2)
            WHERE guild_id = ? AND user_id = ? AND balance >= ?
            """,
            (need, guild_id, user_id, need),
        )
        await self.db.commit()
        return (cur.rowcount or 0) > 0

    # ── Discount / creator codes ────────────────────────────────────

    async def create_discount_code(
        self,
        guild_id: int,
        code: str,
        *,
        discount_type: str,
        discount_value: float,
        max_uses: int | None = None,
        max_per_user: int = 1,
        label: str = "",
        created_by: int | None = None,
        kind: str = "rabatt",
        owner_user_id: int | None = None,
    ) -> int:
        normalized = code.strip().upper()
        if not normalized:
            raise ValueError("Code darf nicht leer sein.")
        if discount_type not in ("percent", "amount"):
            raise ValueError("discount_type muss percent oder amount sein.")
        kind_n = "creator" if kind == "creator" else "rabatt"
        cur = await self.db.execute(
            """
            INSERT INTO discount_codes
              (guild_id, code, discount_type, discount_value, max_uses,
               max_per_user, label, created_by, kind, owner_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                normalized,
                discount_type,
                float(discount_value),
                max_uses,
                max(1, int(max_per_user)),
                (label or "").strip()[:100],
                created_by,
                kind_n,
                owner_user_id,
            ),
        )
        await self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_discount_code(
        self, guild_id: int, code: str
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            """
            SELECT * FROM discount_codes
            WHERE guild_id = ? AND code = ?
            """,
            (guild_id, code.strip().upper()),
        )
        return dict(row) if row else None

    async def get_discount_code_by_id(
        self, code_id: int
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM discount_codes WHERE id = ?", (code_id,)
        )
        return dict(row) if row else None

    async def list_discount_codes(
        self, guild_id: int, *, active_only: bool = False, kind: str | None = None
    ) -> list[dict[str, Any]]:
        q = """
            SELECT * FROM discount_codes
            WHERE guild_id = ?
        """
        params: list[Any] = [guild_id]
        if active_only:
            q += " AND active = 1"
        if kind:
            q += " AND COALESCE(kind, 'rabatt') = ?"
            params.append(kind)
        q += " ORDER BY active DESC, code ASC"
        rows = await self.fetchall(q, tuple(params))
        return [dict(r) for r in rows]

    async def update_discount_code(self, code_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [code_id]
        await self.db.execute(
            f"UPDATE discount_codes SET {cols} WHERE id = ?", values
        )
        await self.db.commit()

    async def count_user_code_uses(
        self, guild_id: int, code_id: int, user_id: int
    ) -> int:
        row = await self.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM orders
            WHERE guild_id = ? AND discount_code_id = ? AND user_id = ?
              AND status != 'cancelled'
            """,
            (guild_id, code_id, user_id),
        )
        return int(row["cnt"]) if row else 0

    async def try_increment_code_use(self, code_id: int) -> bool:
        cur = await self.db.execute(
            """
            UPDATE discount_codes
            SET uses = uses + 1
            WHERE id = ? AND active = 1
              AND (max_uses IS NULL OR uses < max_uses)
            """,
            (code_id,),
        )
        await self.db.commit()
        return (cur.rowcount or 0) > 0

    async def decrement_code_use(self, code_id: int) -> None:
        await self.db.execute(
            """
            UPDATE discount_codes
            SET uses = MAX(uses - 1, 0)
            WHERE id = ?
            """,
            (code_id,),
        )
        await self.db.commit()

    async def get_creator_commission_stats(
        self,
        guild_id: int,
        *,
        code_id: int | None = None,
        owner_user_id: int | None = None,
        month_key: str | None = None,
        commission_pct: float = 10.0,
    ) -> list[dict[str, Any]]:
        """
        Creator-Verdienst je Code für einen Kalendermonat (UTC).
        Provision = commission_pct % vom Preis vor Code-Rabatt
        (total + discount_amount).
        """
        from datetime import datetime, timezone

        month = month_key or datetime.now(timezone.utc).strftime("%Y-%m")
        rate = float(commission_pct) / 100.0
        q = """
            SELECT
              c.id AS code_id,
              c.code,
              c.label,
              c.owner_user_id,
              c.uses AS code_uses,
              COUNT(o.id) AS orders_month,
              COALESCE(SUM(
                CAST(o.total AS REAL) + CAST(COALESCE(o.discount_amount, 0) AS REAL)
              ), 0) AS sales_base,
              COALESCE(SUM(
                (CAST(o.total AS REAL) + CAST(COALESCE(o.discount_amount, 0) AS REAL))
                * ?
              ), 0) AS earnings
            FROM discount_codes c
            LEFT JOIN orders o ON o.discount_code_id = c.id
              AND o.guild_id = c.guild_id
              AND o.status = 'completed'
              AND strftime('%Y-%m', COALESCE(o.completed_at, o.created_at)) = ?
            WHERE c.guild_id = ?
              AND COALESCE(c.kind, 'rabatt') = 'creator'
        """
        params: list[Any] = [rate, month, guild_id]
        if code_id is not None:
            q += " AND c.id = ?"
            params.append(int(code_id))
        if owner_user_id is not None:
            q += " AND c.owner_user_id = ?"
            params.append(int(owner_user_id))
        q += " GROUP BY c.id ORDER BY earnings DESC, c.code ASC"
        rows = await self.fetchall(q, tuple(params))
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["month"] = month
            d["commission_pct"] = float(commission_pct)
            d["sales_base"] = round(float(d.get("sales_base") or 0), 2)
            d["earnings"] = round(float(d.get("earnings") or 0), 2)
            d["orders_month"] = int(d.get("orders_month") or 0)
            out.append(d)
        return out

    async def apply_discount_to_order(
        self,
        order_id: int,
        *,
        code_id: int,
        code: str,
        original_total: float,
        new_total: float,
        discount_amount: float,
    ) -> None:
        order = await self.get_order(order_id)
        # original_total = Warenwert vor Rabatten (Mengenrabatt-Basis behalten)
        keep_orig = None
        if order and order.get("original_total") is not None:
            keep_orig = float(order["original_total"])
        elif order and float(order.get("volume_discount_amount") or 0) > 0:
            keep_orig = round(
                float(order["total"])
                + float(order.get("volume_discount_amount") or 0)
                + float(order.get("discount_amount") or 0),
                2,
            )
        else:
            keep_orig = float(original_total)
        await self.db.execute(
            """
            UPDATE orders SET
              discount_code = ?,
              discount_code_id = ?,
              original_total = ?,
              total = ?,
              half_a = ?,
              discount_amount = ?
            WHERE id = ?
            """,
            (
                code.strip().upper(),
                code_id,
                keep_orig,
                float(new_total),
                float(new_total),
                float(discount_amount),
                order_id,
            ),
        )
        await self.db.commit()

    async def clear_order_discount(self, order_id: int) -> None:
        order = await self.get_order(order_id)
        if not order:
            return
        merch = float(order.get("original_total") or order["total"])
        vol = float(order.get("volume_discount_amount") or 0)
        restore = round(merch - vol, 2) if vol > 0 else merch
        # original_total / volume_* behalten wenn Mengenrabatt aktiv
        if vol > 0 and order.get("original_total") is not None:
            await self.db.execute(
                """
                UPDATE orders SET
                  discount_code = NULL,
                  discount_code_id = NULL,
                  total = ?,
                  half_a = ?,
                  discount_amount = 0
                WHERE id = ?
                """,
                (restore, restore, order_id),
            )
        else:
            await self.db.execute(
                """
                UPDATE orders SET
                  discount_code = NULL,
                  discount_code_id = NULL,
                  original_total = NULL,
                  total = ?,
                  half_a = ?,
                  discount_amount = 0
                WHERE id = ?
                """,
                (restore, restore, order_id),
            )
        await self.db.commit()

    # ── Scan premium & daily usage ──────────────────────────────────

    def _utc_day(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def get_scan_premium_expires(
        self, guild_id: int, user_id: int
    ) -> str | None:
        row = await self.fetchone(
            """
            SELECT expires_at FROM scan_premium
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return str(row["expires_at"]) if row else None

    async def is_scan_premium(self, guild_id: int, user_id: int) -> bool:
        from datetime import datetime, timezone

        expires = await self.get_scan_premium_expires(guild_id, user_id)
        if not expires:
            return False
        try:
            exp = datetime.strptime(expires, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            try:
                exp = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
            except ValueError:
                return False
        return exp > datetime.now(timezone.utc)

    async def extend_scan_premium(
        self, guild_id: int, user_id: int, days: int
    ) -> str:
        """Verlängert Premium. 30-Tage-Käufe → unlimited Scans."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        current = await self.get_scan_premium_expires(guild_id, user_id)
        start = now
        if current:
            try:
                exp = datetime.strptime(current, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                if exp > now:
                    start = exp
            except ValueError:
                pass
        new_exp = start + timedelta(days=max(1, int(days)))
        stamp = new_exp.strftime("%Y-%m-%d %H:%M:%S")
        # 30+ Tage = unlimited; sonst unlimited behalten wenn schon aktiv unlimited
        want_unlimited = int(days) >= 30
        existing = await self.fetchone(
            """
            SELECT unlimited FROM scan_premium
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        keep_unlim = False
        if existing and int(existing["unlimited"] or 0) and current:
            try:
                exp = datetime.strptime(str(current), "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                keep_unlim = exp > now
            except ValueError:
                pass
        unlimited = 1 if (want_unlimited or keep_unlim) else 0
        await self.db.execute(
            """
            INSERT INTO scan_premium (guild_id, user_id, expires_at, unlimited)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
              expires_at = excluded.expires_at,
              unlimited = excluded.unlimited
            """,
            (guild_id, user_id, stamp, unlimited),
        )
        await self.db.commit()
        return stamp

    async def is_scan_premium_unlimited(
        self, guild_id: int, user_id: int
    ) -> bool:
        if not await self.is_scan_premium(guild_id, user_id):
            return False
        row = await self.fetchone(
            """
            SELECT unlimited FROM scan_premium
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return bool(row and int(row["unlimited"] or 0))

    async def get_scan_usage_today(self, guild_id: int, user_id: int) -> int:
        row = await self.fetchone(
            """
            SELECT count FROM scan_usage
            WHERE guild_id = ? AND user_id = ? AND day = ?
            """,
            (guild_id, user_id, self._utc_day()),
        )
        return int(row["count"]) if row else 0

    async def increment_scan_usage(self, guild_id: int, user_id: int) -> int:
        day = self._utc_day()
        await self.db.execute(
            """
            INSERT INTO scan_usage (guild_id, user_id, day, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(guild_id, user_id, day) DO UPDATE SET count = count + 1
            """,
            (guild_id, user_id, day),
        )
        await self.db.commit()
        return await self.get_scan_usage_today(guild_id, user_id)

    async def record_scan_result(
        self,
        guild_id: int,
        user_id: int,
        *,
        filename: str,
        outcome: str,
        category: str,
        finding_count: int = 0,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO scan_results
              (guild_id, user_id, filename, outcome, category, finding_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                (filename or "")[:200],
                outcome,
                category[:64],
                int(finding_count),
            ),
        )
        await self.db.commit()

    async def get_scan_stats(self, guild_id: int) -> dict[str, Any]:
        total_scans = await self.fetchone(
            "SELECT COUNT(*) AS cnt FROM scan_results WHERE guild_id = ?",
            (guild_id,),
        )
        by_outcome = await self.fetchall(
            """
            SELECT outcome, COUNT(*) AS cnt
            FROM scan_results WHERE guild_id = ?
            GROUP BY outcome
            """,
            (guild_id,),
        )
        by_category = await self.fetchall(
            """
            SELECT category, COUNT(*) AS cnt
            FROM scan_results
            WHERE guild_id = ? AND outcome != 'clean'
            GROUP BY category
            ORDER BY cnt DESC
            """,
            (guild_id,),
        )
        usage_sum = await self.fetchone(
            "SELECT COALESCE(SUM(count), 0) AS total FROM scan_usage WHERE guild_id = ?",
            (guild_id,),
        )
        premium_buys = await self.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM orders
            WHERE guild_id = ? AND order_kind = 'scan_premium'
              AND status = 'completed'
            """,
            (guild_id,),
        )
        premium_buyers = await self.fetchone(
            """
            SELECT COUNT(DISTINCT user_id) AS cnt FROM orders
            WHERE guild_id = ? AND order_kind = 'scan_premium'
              AND status = 'completed'
            """,
            (guild_id,),
        )
        active_premium = await self.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM scan_premium
            WHERE guild_id = ?
              AND datetime(expires_at) > datetime('now')
            """,
            (guild_id,),
        )
        outcome_map = {str(r["outcome"]): int(r["cnt"]) for r in by_outcome}
        return {
            "total_logged": int(total_scans["cnt"]) if total_scans else 0,
            "usage_total": int(usage_sum["total"]) if usage_sum else 0,
            "clean": outcome_map.get("clean", 0),
            "suspicious": outcome_map.get("suspicious", 0),
            "blocked": outcome_map.get("blocked", 0),
            "error": outcome_map.get("error", 0),
            "categories": [
                (str(r["category"]), int(r["cnt"])) for r in by_category
            ],
            "premium_purchases": int(premium_buys["cnt"]) if premium_buys else 0,
            "premium_buyers": int(premium_buyers["cnt"]) if premium_buyers else 0,
            "premium_active": int(active_premium["cnt"]) if active_premium else 0,
        }

    async def get_scan_panel(self, guild_id: int) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM scan_panel WHERE guild_id = ?", (guild_id,)
        )
        return dict(row) if row else None

    async def set_scan_panel(
        self, guild_id: int, *, channel_id: int, message_id: int
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO scan_panel (guild_id, channel_id, message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id
            """,
            (guild_id, channel_id, message_id),
        )
        await self.db.commit()

    async def clear_scan_panel_message(self, guild_id: int) -> None:
        await self.db.execute(
            "UPDATE scan_panel SET message_id = NULL WHERE guild_id = ?",
            (guild_id,),
        )
        await self.db.commit()

    async def list_guilds_with_scan_panel(self) -> list[int]:
        rows = await self.fetchall(
            """
            SELECT guild_id FROM scan_panel
            WHERE message_id IS NOT NULL AND channel_id IS NOT NULL
            """
        )
        return [int(r["guild_id"]) for r in rows]

    # ── Service tickets (Support / Bewerbung) ───────────────────────

    async def create_service_ticket(
        self,
        guild_id: int,
        user_id: int,
        ticket_type: str,
        *,
        subject: str = "",
        channel_id: int | None = None,
    ) -> int:
        cur = await self.db.execute(
            """
            INSERT INTO service_tickets
              (guild_id, user_id, ticket_type, channel_id, subject)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, ticket_type, channel_id, subject[:500]),
        )
        await self.db.commit()
        return int(cur.lastrowid)  # type: ignore[arg-type]

    async def update_service_ticket(self, ticket_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [ticket_id]
        await self.db.execute(
            f"UPDATE service_tickets SET {cols} WHERE id = ?", values
        )
        await self.db.commit()

    async def get_service_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM service_tickets WHERE id = ?", (ticket_id,)
        )
        return dict(row) if row else None

    async def get_service_ticket_by_channel(
        self, channel_id: int
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM service_tickets WHERE channel_id = ?",
            (channel_id,),
        )
        return dict(row) if row else None

    async def count_open_service_tickets(
        self, guild_id: int, user_id: int, ticket_type: str
    ) -> int:
        row = await self.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM service_tickets
            WHERE guild_id = ? AND user_id = ? AND ticket_type = ?
              AND status = 'open'
            """,
            (guild_id, user_id, ticket_type),
        )
        return int(row["cnt"]) if row else 0

    async def get_service_panel(
        self, guild_id: int, panel_type: str
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            """
            SELECT * FROM service_panels
            WHERE guild_id = ? AND panel_type = ?
            """,
            (guild_id, panel_type),
        )
        return dict(row) if row else None

    async def set_service_panel(
        self,
        guild_id: int,
        panel_type: str,
        *,
        channel_id: int,
        message_id: int,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO service_panels (guild_id, panel_type, channel_id, message_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, panel_type) DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id
            """,
            (guild_id, panel_type, channel_id, message_id),
        )
        await self.db.commit()

    async def list_service_panels(self) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT * FROM service_panels
            WHERE message_id IS NOT NULL AND channel_id IS NOT NULL
            """
        )
        return [dict(r) for r in rows]

    # ── Boost rewards ───────────────────────────────────────────────

    async def get_boost_claim(
        self, guild_id: int, user_id: int
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            """
            SELECT * FROM boost_claims
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return dict(row) if row else None

    async def upsert_boost_claim(
        self,
        guild_id: int,
        user_id: int,
        *,
        boost_count: int,
        packs_allowed: int,
        thanks: bool = False,
    ) -> dict[str, Any]:
        existing = await self.get_boost_claim(guild_id, user_id)
        if existing is None:
            await self.db.execute(
                """
                INSERT INTO boost_claims
                  (guild_id, user_id, boost_count, packs_allowed, packs_claimed,
                   claimed_item_ids, last_thanks_at, updated_at)
                VALUES (?, ?, ?, ?, 0, '[]', ?, datetime('now'))
                """,
                (
                    guild_id,
                    user_id,
                    int(boost_count),
                    int(packs_allowed),
                    _utc_now_str() if thanks else None,
                ),
            )
        else:
            new_count = max(int(existing["boost_count"]), int(boost_count))
            new_allowed = max(int(existing["packs_allowed"]), int(packs_allowed))
            if thanks:
                await self.db.execute(
                    """
                    UPDATE boost_claims SET
                      boost_count = ?,
                      packs_allowed = ?,
                      last_thanks_at = datetime('now'),
                      updated_at = datetime('now')
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (new_count, new_allowed, guild_id, user_id),
                )
            else:
                await self.db.execute(
                    """
                    UPDATE boost_claims SET
                      boost_count = ?,
                      packs_allowed = ?,
                      updated_at = datetime('now')
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (new_count, new_allowed, guild_id, user_id),
                )
        await self.db.commit()
        row = await self.get_boost_claim(guild_id, user_id)
        assert row is not None
        return row

    async def add_boost_claimed_items(
        self, guild_id: int, user_id: int, item_ids: list[int]
    ) -> dict[str, Any] | None:
        import json

        row = await self.get_boost_claim(guild_id, user_id)
        if not row:
            return None
        try:
            claimed = json.loads(row.get("claimed_item_ids") or "[]")
        except json.JSONDecodeError:
            claimed = []
        if not isinstance(claimed, list):
            claimed = []
        for iid in item_ids:
            if int(iid) not in claimed:
                claimed.append(int(iid))
        new_count = len(claimed)
        await self.db.execute(
            """
            UPDATE boost_claims SET
              claimed_item_ids = ?,
              packs_claimed = ?,
              updated_at = datetime('now')
            WHERE guild_id = ? AND user_id = ?
            """,
            (json.dumps(claimed), new_count, guild_id, user_id),
        )
        await self.db.commit()
        return await self.get_boost_claim(guild_id, user_id)

    # ── Giveaways ───────────────────────────────────────────────────

    async def create_giveaway(
        self,
        guild_id: int,
        *,
        item_id: int,
        prize_name: str,
        winners_count: int,
        ends_at: str,
        host_id: int,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> int:
        cur = await self.db.execute(
            """
            INSERT INTO giveaways
              (guild_id, channel_id, message_id, item_id, prize_name,
               winners_count, ends_at, host_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                message_id,
                item_id,
                prize_name[:200],
                max(1, int(winners_count)),
                ends_at,
                host_id,
            ),
        )
        await self.db.commit()
        return int(cur.lastrowid)  # type: ignore[arg-type]

    async def update_giveaway(self, giveaway_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [giveaway_id]
        await self.db.execute(
            f"UPDATE giveaways SET {cols} WHERE id = ?", values
        )
        await self.db.commit()

    async def get_giveaway(self, giveaway_id: int) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
        )
        return dict(row) if row else None

    async def get_giveaway_by_message(
        self, message_id: int
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM giveaways WHERE message_id = ?", (message_id,)
        )
        return dict(row) if row else None

    async def list_active_giveaways(
        self, guild_id: int | None = None
    ) -> list[dict[str, Any]]:
        if guild_id is None:
            rows = await self.fetchall(
                "SELECT * FROM giveaways WHERE status = 'active'"
            )
        else:
            rows = await self.fetchall(
                """
                SELECT * FROM giveaways
                WHERE guild_id = ? AND status = 'active'
                """,
                (guild_id,),
            )
        return [dict(r) for r in rows]

    async def list_due_giveaways(self) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT * FROM giveaways
            WHERE status = 'active'
              AND datetime(ends_at) <= datetime('now')
            """
        )
        return [dict(r) for r in rows]

    async def add_giveaway_entry(
        self, giveaway_id: int, user_id: int
    ) -> bool:
        try:
            await self.db.execute(
                """
                INSERT INTO giveaway_entries (giveaway_id, user_id)
                VALUES (?, ?)
                """,
                (giveaway_id, user_id),
            )
            await self.db.commit()
            return True
        except Exception:
            return False

    async def count_giveaway_entries(self, giveaway_id: int) -> int:
        row = await self.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM giveaway_entries
            WHERE giveaway_id = ?
            """,
            (giveaway_id,),
        )
        return int(row["cnt"]) if row else 0

    async def list_giveaway_entries(self, giveaway_id: int) -> list[int]:
        rows = await self.fetchall(
            """
            SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?
            """,
            (giveaway_id,),
        )
        return [int(r["user_id"]) for r in rows]

    async def has_giveaway_entry(
        self, giveaway_id: int, user_id: int
    ) -> bool:
        row = await self.fetchone(
            """
            SELECT 1 FROM giveaway_entries
            WHERE giveaway_id = ? AND user_id = ?
            """,
            (giveaway_id, user_id),
        )
        return row is not None

    async def save_giveaway_winners(
        self, giveaway_id: int, user_ids: list[int]
    ) -> None:
        for uid in user_ids:
            await self.db.execute(
                """
                INSERT OR IGNORE INTO giveaway_winners (giveaway_id, user_id)
                VALUES (?, ?)
                """,
                (giveaway_id, uid),
            )
        await self.db.commit()

    async def mark_giveaway_winner_delivered(
        self, giveaway_id: int, user_id: int
    ) -> None:
        await self.db.execute(
            """
            UPDATE giveaway_winners SET delivered = 1
            WHERE giveaway_id = ? AND user_id = ?
            """,
            (giveaway_id, user_id),
        )
        await self.db.commit()

    # ── Payback XP ──────────────────────────────────────────────────

    async def get_payback(
        self, guild_id: int, user_id: int
    ) -> dict[str, Any]:
        row = await self.fetchone(
            """
            SELECT * FROM payback_xp
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        if row:
            return dict(row)
        return {
            "guild_id": guild_id,
            "user_id": user_id,
            "xp": 0,
            "last_daily": None,
            "rewards_claimed": 0,
        }

    async def claim_daily_xp(
        self, guild_id: int, user_id: int, *, xp_gain: int
    ) -> dict[str, Any]:
        """Claim daily XP once per UTC day. Raises ValueError if already claimed."""
        import config as _cfg
        from utils.credits import currency_to_credits

        day = self._utc_day()
        row = await self.get_payback(guild_id, user_id)
        if row.get("last_daily") == day:
            raise ValueError(
                f"Daily schon abgeholt (UTC-Tag `{day}`). Komm morgen wieder."
            )
        xp = int(row.get("xp") or 0) + int(xp_gain)
        rewards = 0
        credits_granted = 0.0
        threshold = int(_cfg.PAYBACK_REWARD_XP)
        reward_currency = float(_cfg.PAYBACK_REWARD_CURRENCY)
        reward_credits = currency_to_credits(reward_currency)
        while xp >= threshold:
            xp -= threshold
            rewards += 1
            credits_granted += reward_credits
        claimed = int(row.get("rewards_claimed") or 0) + rewards
        await self.db.execute(
            """
            INSERT INTO payback_xp
              (guild_id, user_id, xp, last_daily, rewards_claimed)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
              xp = excluded.xp,
              last_daily = excluded.last_daily,
              rewards_claimed = excluded.rewards_claimed
            """,
            (guild_id, user_id, xp, day, claimed),
        )
        await self.db.commit()
        balance = None
        if credits_granted > 0:
            balance = await self.add_credits(
                guild_id, user_id, credits_granted
            )
        return {
            "xp": xp,
            "gained": int(xp_gain),
            "rewards": rewards,
            "credits_granted": credits_granted,
            "currency_granted": rewards * reward_currency,
            "balance": balance,
            "rewards_claimed": claimed,
            "day": day,
        }

    async def get_order(self, order_id: int) -> dict[str, Any] | None:
        row = await self.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))
        return dict(row) if row else None

    async def get_order_by_channel(self, channel_id: int) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT * FROM orders WHERE ticket_channel_id = ?", (channel_id,)
        )
        return dict(row) if row else None

    async def get_order_items(self, order_id: int) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
        )
        return [dict(r) for r in rows]

    async def update_order(self, order_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [order_id]
        await self.db.execute(f"UPDATE orders SET {cols} WHERE id = ?", values)
        await self.db.commit()

    async def count_open_orders(self, guild_id: int, user_id: int) -> int:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        row = await self.fetchone(
            f"""
            SELECT COUNT(*) AS cnt FROM orders
            WHERE guild_id = ? AND user_id = ? AND status IN ({placeholders})
            """,
            (guild_id, user_id, *OPEN_STATUSES),
        )
        return int(row["cnt"]) if row else 0

    async def list_cancelled_orders(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            """
            SELECT * FROM orders
            WHERE guild_id = ? AND status = 'cancelled'
            ORDER BY id ASC
            """,
            (guild_id,),
        )
        return [dict(r) for r in rows]

    async def delete_cancelled_orders(self, guild_id: int) -> int:
        """Löscht alle stornierten Bestellungen (inkl. Items/Proofs via CASCADE)."""
        cur = await self.db.execute(
            "DELETE FROM orders WHERE guild_id = ? AND status = 'cancelled'",
            (guild_id,),
        )
        await self.db.commit()
        return int(cur.rowcount or 0)

    async def add_payment_proof(
        self, order_id: int, attachment_url: str, uploaded_by: int
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO payment_proofs (order_id, attachment_url, uploaded_by)
            VALUES (?, ?, ?)
            """,
            (order_id, attachment_url, uploaded_by),
        )
        await self.db.commit()

    async def get_payment_proofs(self, order_id: int) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT * FROM payment_proofs WHERE order_id = ? ORDER BY id",
            (order_id,),
        )
        return [dict(r) for r in rows]

    async def get_unused_vouch_order(
        self, guild_id: int, user_id: int
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            """
            SELECT * FROM orders
            WHERE guild_id = ? AND user_id = ? AND status = 'completed' AND vouch_used = 0
            ORDER BY completed_at ASC, id ASC
            LIMIT 1
            """,
            (guild_id, user_id),
        )
        return dict(row) if row else None

    async def get_unused_vouch_order_for_user(
        self, user_id: int, guild_id: int | None = None
    ) -> dict[str, Any] | None:
        """Offene Vouch-Bestellung — optional auf eine Guild beschränkt."""
        if guild_id is not None:
            return await self.get_unused_vouch_order(guild_id, user_id)
        row = await self.fetchone(
            """
            SELECT * FROM orders
            WHERE user_id = ? AND status = 'completed' AND vouch_used = 0
            ORDER BY completed_at ASC, id ASC
            LIMIT 1
            """,
            (user_id,),
        )
        return dict(row) if row else None

    async def mark_vouch_used(self, order_id: int) -> None:
        await self.update_order(order_id, vouch_used=1)

    async def mark_vouch_used_with_rating(
        self, order_id: int, rating: int
    ) -> None:
        await self.update_order(
            order_id,
            vouch_used=1,
            vouch_rating=max(1, min(5, int(rating))),
        )

    async def get_vouch_and_order_stats(self, guild_id: int) -> dict[str, Any]:
        completed = await self.fetchone(
            """
            SELECT COUNT(*) AS cnt,
                   COALESCE(SUM(total), 0) AS revenue
            FROM orders
            WHERE guild_id = ? AND status = 'completed'
              AND COALESCE(order_kind, 'shop') = 'shop'
            """,
            (guild_id,),
        )
        buyers = await self.fetchone(
            """
            SELECT COUNT(DISTINCT user_id) AS cnt FROM orders
            WHERE guild_id = ? AND status = 'completed'
              AND COALESCE(order_kind, 'shop') = 'shop'
            """,
            (guild_id,),
        )
        vouches_db = await self.fetchone(
            """
            SELECT COUNT(*) AS cnt,
                   AVG(vouch_rating) AS avg_rating
            FROM orders
            WHERE guild_id = ? AND vouch_used = 1
              AND vouch_rating IS NOT NULL
            """,
            (guild_id,),
        )
        vouch_used = await self.fetchone(
            """
            SELECT COUNT(*) AS cnt FROM orders
            WHERE guild_id = ? AND vouch_used = 1
            """,
            (guild_id,),
        )
        return {
            "orders_completed": int(completed["cnt"]) if completed else 0,
            "revenue": float(completed["revenue"]) if completed else 0.0,
            "unique_buyers": int(buyers["cnt"]) if buyers else 0,
            "vouches_used": int(vouch_used["cnt"]) if vouch_used else 0,
            "avg_rating": (
                float(vouches_db["avg_rating"])
                if vouches_db and vouches_db["avg_rating"] is not None
                else None
            ),
            "rated_count": int(vouches_db["cnt"]) if vouches_db else 0,
        }

    # ── Daily deals ─────────────────────────────────────────────────

    async def create_daily_deal(
        self,
        guild_id: int,
        item_id: int,
        *,
        discount_type: str,
        discount_value: float,
        original_price: float,
        deal_price: float,
        created_by: int | None = None,
        expires_at: str | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> int:
        cur = await self.db.execute(
            """
            INSERT INTO daily_deals
              (guild_id, item_id, discount_type, discount_value,
               original_price, deal_price, channel_id, message_id,
               created_by, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                item_id,
                discount_type,
                discount_value,
                original_price,
                deal_price,
                channel_id,
                message_id,
                created_by,
                expires_at,
            ),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def update_daily_deal(self, deal_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [deal_id]
        await self.db.execute(
            f"UPDATE daily_deals SET {cols} WHERE id = ?", values
        )
        await self.db.commit()

    async def get_daily_deal(self, deal_id: int) -> dict[str, Any] | None:
        row = await self.fetchone("SELECT * FROM daily_deals WHERE id = ?", (deal_id,))
        return dict(row) if row else None

    async def list_active_daily_deals(
        self, guild_id: int | None = None
    ) -> list[dict[str, Any]]:
        if guild_id is None:
            rows = await self.fetchall(
                """
                SELECT d.*, i.name AS item_name
                FROM daily_deals d
                JOIN items i ON i.id = d.item_id
                WHERE d.active = 1
                ORDER BY d.id DESC
                """
            )
        else:
            rows = await self.fetchall(
                """
                SELECT d.*, i.name AS item_name
                FROM daily_deals d
                JOIN items i ON i.id = d.item_id
                WHERE d.guild_id = ? AND d.active = 1
                ORDER BY d.id DESC
                """,
                (guild_id,),
            )
        return [dict(r) for r in rows]

    async def deactivate_daily_deal(self, deal_id: int) -> None:
        await self.update_daily_deal(deal_id, active=0)

    async def build_cart_row_for_item(
        self, item_id: int, *, price_override: float | None = None, qty: int = 1
    ) -> dict[str, Any] | None:
        """Baut eine cart_get-kompatible Zeile (optional mit Deal-Preis)."""
        item = await self.get_item(item_id)
        if not item or not int(item.get("active") or 0):
            return None
        cat = await self.get_category(int(item["category_id"]))
        price = (
            float(price_override)
            if price_override is not None
            else float(item["price"])
        )
        return {
            "item_id": int(item["id"]),
            "qty": int(qty),
            "name": item["name"],
            "price": price,
            "category_id": int(item["category_id"]),
            "pack_dm_text": item.get("pack_dm_text") or "",
            "pack_link": item.get("pack_link") or "",
            "pack_file": item.get("pack_file") or "",
            "item_role_id": item.get("role_id"),
            "category_role_id": cat.get("role_id") if cat else None,
            "category_name": (cat.get("name") if cat else "") or "",
        }

    # ── Helpers ─────────────────────────────────────────────────────

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        async with self.db.execute(query, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
        async with self.db.execute(query, params) as cur:
            return await cur.fetchall()
