from __future__ import annotations

import json
import aiosqlite
from pathlib import Path
from typing import Any, Optional


OPEN_STATUSES = ("pending", "awaiting_proof", "awaiting_confirm")


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
                delete_on_cancel INTEGER NOT NULL DEFAULT 0
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
                PRIMARY KEY (guild_id, slot)
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
    ) -> None:
        ids_json = json.dumps(sorted({int(i) for i in category_ids}))
        existing = await self.get_buy_panel_slot(guild_id, slot)
        if channel_id is None and existing:
            channel_id = existing.get("channel_id")
        if message_id is None and existing:
            message_id = existing.get("message_id")
        await self.db.execute(
            """
            INSERT INTO buy_panel_slots (guild_id, slot, filter_mode, category_ids, title, channel_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, slot) DO UPDATE SET
                filter_mode = excluded.filter_mode,
                category_ids = excluded.category_ids,
                title = COALESCE(excluded.title, buy_panel_slots.title),
                channel_id = COALESCE(excluded.channel_id, buy_panel_slots.channel_id),
                message_id = COALESCE(excluded.message_id, buy_panel_slots.message_id)
            """,
            (guild_id, slot, filter_mode, ids_json, title, channel_id, message_id),
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
    ) -> int:
        total = sum(float(r["price"]) * int(r["qty"]) for r in cart_rows)
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
               order_number)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (guild_id, user_id, ticket_channel_id, total, total, 0.0, order_number),
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
                    r["item_id"],
                    r["category_id"],
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
