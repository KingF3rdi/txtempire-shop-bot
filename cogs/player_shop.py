"""
player_shop.py
================

Settings/Modpacks/Coachings von bestimmten Spielern (Content-Creatorn) — eine
eigene, vom normalen Kategorie/Item-Shop getrennte Struktur.

Jedes Item hat zwei Preise:
  - price:        normaler Shop-Währungspreis (per /pay Ingame-Befehl),
                   optional zeitlich befristet reduziert (/spieleritem sale).
  - PayPal-Preis:  FEST config.PLAYER_ITEM_PAYPAL_PRICE (Standard 1,00 €),
                    unabhängig vom Shop-Preis — jedes Item ist immer auch
                    für diesen festen Betrag per PayPal kaufbar.

Drei Arten (art): Settings, Modpack (beides mit Pack-Datei/-Link + optionaler
Keybind-Liste), Coaching (kein Pack — Käufer trägt im Kauf-Modal einen
Wunschtermin/Notiz ein, Staff stimmt den Termin im Ticket ab).

Provisions-Anteil: /spieler auszahlung legt pro Spieler/Creator einen
Prozentsatz + Ingame-Namen fest — kein automatischer Auszahlungsweg, aber
Ticket und Bestätigung zeigen Staff den fälligen Betrag + fertigen /pay-Befehl.

Weitere Extras:
  - /spieler verifizieren markiert einen Spieler mit einem 🎖️-Badge überall,
    wo er im Panel/Listen auftaucht.
  - /spieler stats zeigt Umsatz/Käufe/Provision pro Spieler oder gesamt.
  - /spieler gruppenrabatt gibt Trägern einer Rolle automatisch Rabatt,
    sobald N andere Rollen-Träger dasselbe Item bereits bestätigt gekauft
    haben (Klan-/Gilden-Rabatt).
  - /spieler tauschwert lässt Käufer beim Kaufen einen früheren bestätigten
    Kauf gegen einen %-Rabatt eintauschen (einmalig pro altem Kauf,
    Ablehnung des neuen Tickets gibt den alten Kauf wieder frei).
  - /spieler salelog meldet automatisch (alle 10 Min. Sweep), wenn ein Sale
    abgelaufen ist, in einen konfigurierten Channel.

Ablauf: Panel (gesamt via /spielerpanel oder pro Spieler via
/spielerpanel spieler:X) mit "Kaufen"-Button -> ggf. Spieler wählen ->
Item wählen -> Modal (Ingame-Name [+ Notiz]) -> privates Ticket mit beiden
Zahlungswegen wird erstellt, Team bestätigt -> Pack wird per DM geliefert
(deliver_packs) bzw. bei Coaching der Termin im Ticket abgestimmt.

Eigene Tabellen (shop_players, shop_player_items, player_shop_settings,
player_shop_tickets) — keine Änderung an db/database.py nötig.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.delivery import deliver_packs
from utils.embeds import base_embed, error_embed, format_price, success_embed, warn_embed
from utils.giveaways import parse_duration
from utils.packs import resolve_preview_path, save_pack_attachment, save_preview_attachment
from utils.price import parse_price
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot

KIND_LABELS = {"settings": "Settings", "modpack": "Modpack", "coaching": "Coaching"}


# ── DB Bootstrap & Helpers (eigene Tabellen, kein Eingriff in database.py) ──

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS shop_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            payout_percent REAL NOT NULL DEFAULT 0,
            payout_recipient TEXT NOT NULL DEFAULT '',
            verified INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 99,
            UNIQUE(guild_id, name)
        );
        CREATE TABLE IF NOT EXISTS shop_player_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'settings',
            name TEXT NOT NULL,
            price REAL NOT NULL,
            sale_price REAL,
            sale_until TEXT,
            sale_notified INTEGER NOT NULL DEFAULT 0,
            pack_dm_text TEXT NOT NULL DEFAULT '',
            pack_link TEXT NOT NULL DEFAULT '',
            pack_file TEXT NOT NULL DEFAULT '',
            preview_file TEXT NOT NULL DEFAULT '',
            keybinds_text TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 99,
            FOREIGN KEY (player_id) REFERENCES shop_players(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS player_shop_settings (
            guild_id INTEGER PRIMARY KEY,
            staff_role_id INTEGER,
            sale_log_channel_id INTEGER,
            trade_in_percent REAL NOT NULL DEFAULT 0,
            group_role_id INTEGER,
            group_discount_percent REAL NOT NULL DEFAULT 0,
            group_threshold INTEGER NOT NULL DEFAULT 3,
            next_ticket_number INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS player_shop_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            item_id INTEGER,
            player_name TEXT NOT NULL,
            item_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            price REAL NOT NULL,
            ign TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            pack_dm_text TEXT NOT NULL DEFAULT '',
            pack_link TEXT NOT NULL DEFAULT '',
            pack_file TEXT NOT NULL DEFAULT '',
            preview_file TEXT NOT NULL DEFAULT '',
            keybinds_text TEXT NOT NULL DEFAULT '',
            payout_percent REAL NOT NULL DEFAULT 0,
            payout_recipient TEXT NOT NULL DEFAULT '',
            discount_note TEXT NOT NULL DEFAULT '',
            traded_in INTEGER NOT NULL DEFAULT 0,
            traded_in_ticket_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            ticket_channel_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        """
    )
    await bot.db.db.commit()
    # Additiv für Installs, bei denen die Tabellen schon ohne diese Spalten liefen.
    for table, column, ddl in (
        ("shop_player_items", "preview_file", "TEXT NOT NULL DEFAULT ''"),
        ("shop_player_items", "sale_price", "REAL"),
        ("shop_player_items", "sale_until", "TEXT"),
        ("shop_player_items", "sale_notified", "INTEGER NOT NULL DEFAULT 0"),
        ("shop_player_items", "keybinds_text", "TEXT NOT NULL DEFAULT ''"),
        ("shop_players", "payout_percent", "REAL NOT NULL DEFAULT 0"),
        ("shop_players", "payout_recipient", "TEXT NOT NULL DEFAULT ''"),
        ("shop_players", "verified", "INTEGER NOT NULL DEFAULT 0"),
        ("player_shop_settings", "sale_log_channel_id", "INTEGER"),
        ("player_shop_settings", "trade_in_percent", "REAL NOT NULL DEFAULT 0"),
        ("player_shop_settings", "group_role_id", "INTEGER"),
        ("player_shop_settings", "group_discount_percent", "REAL NOT NULL DEFAULT 0"),
        ("player_shop_settings", "group_threshold", "INTEGER NOT NULL DEFAULT 3"),
        ("player_shop_tickets", "item_id", "INTEGER"),
        ("player_shop_tickets", "preview_file", "TEXT NOT NULL DEFAULT ''"),
        ("player_shop_tickets", "keybinds_text", "TEXT NOT NULL DEFAULT ''"),
        ("player_shop_tickets", "note", "TEXT NOT NULL DEFAULT ''"),
        ("player_shop_tickets", "payout_percent", "REAL NOT NULL DEFAULT 0"),
        ("player_shop_tickets", "payout_recipient", "TEXT NOT NULL DEFAULT ''"),
        ("player_shop_tickets", "discount_note", "TEXT NOT NULL DEFAULT ''"),
        ("player_shop_tickets", "traded_in", "INTEGER NOT NULL DEFAULT 0"),
        ("player_shop_tickets", "traded_in_ticket_id", "INTEGER"),
    ):
        try:
            await bot.db.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            await bot.db.db.commit()
        except Exception:
            pass


async def _get_staff_role_id(bot: "ShopBot", guild_id: int) -> Optional[int]:
    row = await bot.db.fetchone(
        "SELECT staff_role_id FROM player_shop_settings WHERE guild_id = ?", (guild_id,)
    )
    return int(row["staff_role_id"]) if row and row["staff_role_id"] else None


async def _get_settings(bot: "ShopBot", guild_id: int) -> dict:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO player_shop_settings (guild_id) VALUES (?)", (guild_id,)
    )
    await bot.db.db.commit()
    row = await bot.db.fetchone(
        "SELECT * FROM player_shop_settings WHERE guild_id = ?", (guild_id,)
    )
    return dict(row) if row else {}


async def _set_staff_role_id(bot: "ShopBot", guild_id: int, role_id: int) -> None:
    await bot.db.db.execute(
        """
        INSERT INTO player_shop_settings (guild_id, staff_role_id) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET staff_role_id = excluded.staff_role_id
        """,
        (guild_id, role_id),
    )
    await bot.db.db.commit()


async def _next_ticket_number(bot: "ShopBot", guild_id: int) -> int:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO player_shop_settings (guild_id) VALUES (?)", (guild_id,)
    )
    row = await bot.db.fetchone(
        "SELECT next_ticket_number FROM player_shop_settings WHERE guild_id = ?", (guild_id,)
    )
    n = int(row["next_ticket_number"]) if row else 1
    await bot.db.db.execute(
        "UPDATE player_shop_settings SET next_ticket_number = ? WHERE guild_id = ?",
        (n + 1, guild_id),
    )
    await bot.db.db.commit()
    return n


async def list_players(bot: "ShopBot", guild_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        "SELECT * FROM shop_players WHERE guild_id = ? ORDER BY sort_order ASC, name ASC",
        (guild_id,),
    )
    return [dict(r) for r in rows]


async def get_player(bot: "ShopBot", guild_id: int, player_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM shop_players WHERE id = ? AND guild_id = ?", (player_id, guild_id)
    )
    return dict(row) if row else None


async def find_player_by_name(bot: "ShopBot", guild_id: int, name: str) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM shop_players WHERE guild_id = ? AND lower(name) = lower(?)",
        (guild_id, name.strip()),
    )
    return dict(row) if row else None


async def list_player_items(bot: "ShopBot", guild_id: int, player_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        """
        SELECT * FROM shop_player_items
        WHERE guild_id = ? AND player_id = ?
        ORDER BY sort_order ASC, name ASC
        """,
        (guild_id, player_id),
    )
    return [dict(r) for r in rows]


async def _group_discount_info(
    bot: "ShopBot", guild: discord.Guild, member: discord.abc.User, item: dict
) -> tuple[float, str]:
    """Gruppen-/Clan-Rabatt: ab N bestätigten Käufen desselben Items durch Träger
    einer konfigurierten Rolle bekommen weitere Träger dieser Rolle einen Rabatt."""
    settings = await _get_settings(bot, guild.id)
    role_id = settings.get("group_role_id")
    discount_pct = float(settings.get("group_discount_percent") or 0)
    threshold = int(settings.get("group_threshold") or 3)
    if not role_id or discount_pct <= 0:
        return 0.0, ""
    if not isinstance(member, discord.Member) or not any(r.id == int(role_id) for r in member.roles):
        return 0.0, ""
    row = await bot.db.fetchone(
        "SELECT COUNT(DISTINCT user_id) AS c FROM player_shop_tickets "
        "WHERE guild_id = ? AND item_id = ? AND status = 'confirmed'",
        (guild.id, item["id"]),
    )
    count = int(row["c"]) if row else 0
    if count < threshold:
        return 0.0, ""
    role = guild.get_role(int(role_id))
    role_name = role.name if role else "Gruppe"
    return discount_pct, f"Gruppen-Rabatt ({role_name}, {count}+ Käufe): -{discount_pct:g}%"


async def _eligible_trade_ins(bot: "ShopBot", guild_id: int, user_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        """
        SELECT id, player_name, item_name, price FROM player_shop_tickets
        WHERE guild_id = ? AND user_id = ? AND status = 'confirmed' AND traded_in = 0
        ORDER BY confirmed_at DESC
        """,
        (guild_id, user_id),
    )
    return [dict(r) for r in rows]


async def _notify_expired_sales(bot: "ShopBot") -> int:
    """Meldet abgelaufene Sales einmalig im konfigurierten Log-Channel (falls gesetzt)."""
    rows = await bot.db.fetchall(
        "SELECT * FROM shop_player_items WHERE sale_price IS NOT NULL AND sale_until IS NOT NULL AND sale_notified = 0"
    )
    now = datetime.now(timezone.utc)
    notified = 0
    for r in rows:
        item = dict(r)
        try:
            until = datetime.strptime(item["sale_until"], _SALE_TIME_FMT).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if now < until:
            continue

        await bot.db.db.execute(
            "UPDATE shop_player_items SET sale_notified = 1 WHERE id = ?", (item["id"],)
        )
        await bot.db.db.commit()
        notified += 1

        guild_id = int(item["guild_id"])
        settings = await _get_settings(bot, guild_id)
        channel_id = settings.get("sale_log_channel_id")
        if not channel_id:
            continue
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            continue
        player = await get_player(bot, guild_id, int(item["player_id"]))
        player_name = player["name"] if player else "?"
        try:
            await channel.send(
                embed=warn_embed(
                    "Sale abgelaufen",
                    f"**{item['name']}** ({player_name}) — Sale-Preis war "
                    f"{format_price(float(item['sale_price']))}, jetzt wieder "
                    f"{format_price(float(item['price']))}.\n"
                    f"Verlängern: `/spieleritem sale item:{item['id']} preis:… dauer:…`",
                )
            )
        except discord.HTTPException:
            pass
    return notified


async def get_player_item(bot: "ShopBot", guild_id: int, item_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM shop_player_items WHERE id = ? AND guild_id = ?", (item_id, guild_id)
    )
    return dict(row) if row else None


def _kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind.capitalize())


_SALE_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _effective_price(item: dict) -> tuple[float, bool]:
    """Normalpreis, außer eine aktive Sale ist hinterlegt — dann (Sale-Preis, True)."""
    price = float(item["price"])
    sale_price = item.get("sale_price")
    sale_until = item.get("sale_until")
    if sale_price is None or not sale_until:
        return price, False
    try:
        until = datetime.strptime(sale_until, _SALE_TIME_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return price, False
    if datetime.now(timezone.utc) >= until:
        return price, False
    return float(sale_price), True


def _item_line(item: dict) -> str:
    preview_mark = " 🖼️" if item.get("preview_file") else ""
    price, on_sale = _effective_price(item)
    price_text = (
        f"~~{format_price(float(item['price']))}~~ **{format_price(price)}** 🔥"
        if on_sale else format_price(price)
    )
    return (
        f"`{item['id']}` **{item['name']}** ({_kind_label(item['kind'])}) · "
        f"{price_text} · PayPal "
        f"{config.PLAYER_ITEM_PAYPAL_PRICE:.2f} €{preview_mark}"
    )


# ── UI: Panel, Spieler-/Item-Auswahl, IGN-Modal, Ticket-Buttons ─────────

def _player_badge(player: dict) -> str:
    return " 🎖️" if int(player.get("verified") or 0) else ""


def _panel_embed(players: list[dict]) -> discord.Embed:
    body = "\n".join(f"• **{p['name']}**{_player_badge(p)}" for p in players) or "_Noch keine Spieler konfiguriert._"
    return base_embed(
        "🎮 Settings & Modpacks",
        "Settings und Modpacks von unseren Spielern — wähle einen Spieler, dann das "
        "gewünschte Settings/Modpack.\n\n"
        f"{body}\n\n"
        "🎖️ = vom Team verifizierter Spieler\n\n"
        f"Jedes Settings/Modpack kostet zusätzlich zum Shop-Preis immer auch nur "
        f"**{config.PLAYER_ITEM_PAYPAL_PRICE:.2f} € per PayPal**.",
    )


def _player_panel_embed(player: dict, items: list[dict]) -> discord.Embed:
    body = "\n".join(_item_line(i) for i in items) or "_Noch keine Settings/Modpacks hinterlegt._"
    return base_embed(
        f"🎮 {player['name']}{_player_badge(player)}",
        f"Settings, Modpacks & mehr von **{player['name']}**{_player_badge(player)}.\n\n"
        f"{body}\n\n"
        f"Jedes Settings/Modpack kostet zusätzlich zum Shop-Preis immer auch nur "
        f"**{config.PLAYER_ITEM_PAYPAL_PRICE:.2f} € per PayPal**.",
    )


class PlayerItemIGNModal(discord.ui.Modal):
    def __init__(
        self, bot: "ShopBot", item_id: int, kind: str, old_ticket_id: Optional[int] = None,
    ) -> None:
        super().__init__(
            title="Termin anfragen" if kind == "coaching" else "Minecraft-Name"
        )
        self.bot = bot
        self.item_id = item_id
        self.old_ticket_id = old_ticket_id
        self.ign = discord.ui.TextInput(
            label="Dein Minecraft-Name (für /pay)", max_length=32, required=True,
        )
        self.add_item(self.ign)
        self.note = discord.ui.TextInput(
            label="Wunschtermin / Notiz" if kind == "coaching" else "Notiz (optional)",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=(kind == "coaching"),
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ign = str(self.ign.value).strip()
        if not ign:
            await interaction.response.send_message(
                embed=error_embed("Minecraft-Name fehlt"), ephemeral=True
            )
            return
        assert interaction.guild is not None
        item = await get_player_item(self.bot, interaction.guild.id, self.item_id)
        if not item:
            await interaction.response.send_message(
                embed=error_embed("Nicht mehr verfügbar"), ephemeral=True
            )
            return
        player = await get_player(self.bot, interaction.guild.id, int(item["player_id"]))
        if not player:
            await interaction.response.send_message(
                embed=error_embed("Spieler nicht gefunden"), ephemeral=True
            )
            return
        await _create_player_item_ticket(
            self.bot, interaction, player=player, item=item, ign=ign,
            note=str(self.note.value).strip(), old_ticket_id=self.old_ticket_id,
        )


class PlayerTradeInSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", item_id: int, kind: str, eligible: list[dict], trade_in_pct: float) -> None:
        self.bot = bot
        self.item_id = item_id
        self.kind = kind
        options = [
            discord.SelectOption(label="Ohne Eintausch", value="none", emoji="➡️"),
        ]
        for t in eligible[:24]:
            options.append(
                discord.SelectOption(
                    label=f"{t['item_name']} ({t['player_name']})"[:100],
                    value=str(t["id"]),
                    description=f"{format_price(float(t['price']))} · -{trade_in_pct:g}% Rabatt"[:100],
                )
            )
        super().__init__(
            placeholder="Alten Kauf eintauschen? (optional)",
            options=options,
            custom_id="playershop:tradein",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        old_ticket_id = None if value == "none" else int(value)
        await interaction.response.send_modal(
            PlayerItemIGNModal(self.bot, self.item_id, self.kind, old_ticket_id)
        )


class PlayerItemSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", items: list[dict]) -> None:
        self.bot = bot
        self._kinds = {int(i["id"]): i["kind"] for i in items}
        options = []
        for i in items[:25]:
            price, on_sale = _effective_price(i)
            desc = f"{format_price(price)}{' 🔥Sale' if on_sale else ''} · PayPal {config.PLAYER_ITEM_PAYPAL_PRICE:.2f} €"
            options.append(
                discord.SelectOption(
                    label=f"{i['name']} ({_kind_label(i['kind'])})"[:100],
                    value=str(i["id"]),
                    description=desc[:100],
                )
            )
        super().__init__(
            placeholder="Settings/Modpack auswählen ...",
            options=options,
            custom_id="playershop:itemselect",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        item_id = int(self.values[0])
        kind = self._kinds.get(item_id, "settings")

        settings = await _get_settings(self.bot, interaction.guild.id)
        trade_in_pct = float(settings.get("trade_in_percent") or 0)
        eligible = (
            await _eligible_trade_ins(self.bot, interaction.guild.id, interaction.user.id)
            if trade_in_pct > 0
            else []
        )
        if eligible:
            view = discord.ui.View(timeout=180)
            view.add_item(PlayerTradeInSelect(self.bot, item_id, kind, eligible, trade_in_pct))
            await interaction.response.edit_message(
                content=(
                    f"Du hast frühere bestätigte Käufe — möchtest du einen eintauschen "
                    f"(**{trade_in_pct:g}%** seines Preises als Rabatt)?"
                ),
                view=view,
            )
            return
        await interaction.response.send_modal(PlayerItemIGNModal(self.bot, item_id, kind))


class PlayerSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", players: list[dict]) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(
                label=p["name"][:100],
                value=str(p["id"]),
                emoji="🎖️" if int(p.get("verified") or 0) else None,
                description="Verifizierter Spieler" if int(p.get("verified") or 0) else None,
            )
            for p in players[:25]
        ]
        super().__init__(
            placeholder="Spieler auswählen ...",
            options=options,
            custom_id="playershop:playerselect",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        player_id = int(self.values[0])
        items = await list_player_items(self.bot, interaction.guild.id, player_id)
        if not items:
            await interaction.response.edit_message(
                content="Für diesen Spieler sind aktuell keine Settings/Modpacks hinterlegt.",
                view=None,
            )
            return
        view = discord.ui.View(timeout=180)
        view.add_item(PlayerItemSelect(self.bot, items))
        await interaction.response.edit_message(
            content="Welches Settings/Modpack möchtest du kaufen?", view=view
        )


class PlayerShopSinglePanelView(discord.ui.View):
    """Persistentes Panel: ein Button -> direkt Item-Auswahl für GENAU diesen Spieler."""

    def __init__(self, bot: "ShopBot", player_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.player_id = int(player_id)

        buy_btn = discord.ui.Button(
            label="Kaufen", style=discord.ButtonStyle.primary,
            custom_id=f"playershoppanel:buyplayer:{self.player_id}", emoji="🎮",
        )
        buy_btn.callback = self._on_buy  # type: ignore[method-assign]
        self.add_item(buy_btn)

    async def _on_buy(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        items = await list_player_items(self.bot, interaction.guild.id, self.player_id)
        if not items:
            await interaction.response.send_message(
                embed=warn_embed("Für diesen Spieler sind aktuell keine Settings/Modpacks hinterlegt."),
                ephemeral=True,
            )
            return
        view = discord.ui.View(timeout=180)
        view.add_item(PlayerItemSelect(self.bot, items))
        await interaction.response.send_message(
            content="Welches Settings/Modpack möchtest du kaufen?", view=view, ephemeral=True,
        )


def ensure_player_panel_view(bot: "ShopBot", player_id: int) -> None:
    registered: set[int] = getattr(bot, "_player_panel_registered", set())
    pid = int(player_id)
    if pid in registered:
        return
    bot.add_view(PlayerShopSinglePanelView(bot, pid))
    registered.add(pid)
    bot._player_panel_registered = registered


async def register_all_player_panel_views(bot: "ShopBot") -> int:
    """Registriert Views für alle Spieler (Panel-Buttons bleiben nach Neustart klickbar)."""
    rows = await bot.db.fetchall("SELECT id FROM shop_players")
    for row in rows:
        ensure_player_panel_view(bot, int(row["id"]))
    return len(rows)


async def _open_player_picker(bot: "ShopBot", interaction: discord.Interaction) -> None:
    assert interaction.guild is not None
    players = await list_players(bot, interaction.guild.id)
    if not players:
        await interaction.response.send_message(
            embed=warn_embed("Aktuell sind keine Spieler im Shop hinterlegt."),
            ephemeral=True,
        )
        return
    view = discord.ui.View(timeout=180)
    view.add_item(PlayerSelect(bot, players))
    await interaction.response.send_message(
        content="Von welchem Spieler möchtest du ein Settings/Modpack kaufen?",
        view=view,
        ephemeral=True,
    )


async def _create_player_item_ticket(
    bot: "ShopBot",
    interaction: discord.Interaction,
    *,
    player: dict,
    item: dict,
    ign: str,
    note: str = "",
    old_ticket_id: Optional[int] = None,
) -> None:
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    settings = await bot.db.ensure_guild(guild.id)
    category_id = settings.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None
    staff_role_id = await _get_staff_role_id(bot, guild.id) or settings.get("staff_role_id")
    staff_role = guild.get_role(int(staff_role_id)) if staff_role_id else None
    me = guild.me
    if me is None:
        await interaction.followup.send(embed=error_embed("Bot-Mitgliedschaft fehlt"), ephemeral=True)
        return

    bot_perms = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, embed_links=True, attach_files=True,
        read_message_history=True, manage_channels=True, manage_messages=True,
    )
    buyer_perms = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, attach_files=True,
        embed_links=True, read_message_history=True,
    )
    staff_perms = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, attach_files=True,
        embed_links=True, read_message_history=True, manage_messages=True,
    )
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: bot_perms,
    }
    if isinstance(interaction.user, discord.Member):
        overwrites[interaction.user] = buyer_perms
    if staff_role:
        overwrites[staff_role] = staff_perms

    price, on_sale = _effective_price(item)
    payout_percent = float(player.get("payout_percent") or 0)
    payout_recipient = (player.get("payout_recipient") or "").strip()

    discount_notes: list[str] = []
    group_pct, group_note = await _group_discount_info(bot, guild, interaction.user, item)
    if group_pct > 0:
        price = max(0.0, round(price * (1 - group_pct / 100), 2))
        discount_notes.append(group_note)

    old_ticket: Optional[dict] = None
    trade_in_pct = 0.0
    if old_ticket_id:
        row = await bot.db.fetchone(
            """
            SELECT * FROM player_shop_tickets
            WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'confirmed' AND traded_in = 0
            """,
            (old_ticket_id, guild.id, interaction.user.id),
        )
        old_ticket = dict(row) if row else None
    if old_ticket is not None:
        ps_settings = await _get_settings(bot, guild.id)
        trade_in_pct = float(ps_settings.get("trade_in_percent") or 0)
        if trade_in_pct > 0:
            credit = round(float(old_ticket["price"]) * trade_in_pct / 100, 2)
            price = max(0.0, round(price - credit, 2))
            discount_notes.append(
                f"Eintausch ({old_ticket['item_name']}, {trade_in_pct:g}%): -{format_price(credit)}"
            )
            await bot.db.db.execute(
                "UPDATE player_shop_tickets SET traded_in = 1 WHERE id = ?", (old_ticket["id"],)
            )
            await bot.db.db.commit()
        else:
            old_ticket = None

    discount_note = " · ".join(discount_notes)
    ticket_number = await _next_ticket_number(bot, guild.id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO player_shop_tickets
            (guild_id, ticket_number, user_id, item_id, player_name, item_name, kind,
             price, ign, note, pack_dm_text, pack_link, pack_file, preview_file,
             keybinds_text, payout_percent, payout_recipient, discount_note,
             traded_in_ticket_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            guild.id, ticket_number, interaction.user.id, item.get("id"), player["name"], item["name"],
            item["kind"], price, ign, note,
            item.get("pack_dm_text") or "", item.get("pack_link") or "",
            item.get("pack_file") or "", item.get("preview_file") or "",
            item.get("keybinds_text") or "", payout_percent, payout_recipient, discount_note,
            old_ticket["id"] if old_ticket else None,
        ),
    )
    await bot.db.db.commit()
    ticket_id = int(cur.lastrowid)  # type: ignore[arg-type]

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    name = f"settings-{ticket_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites,
            reason=f"Settings/Modpack-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return

    await bot.db.db.execute(
        "UPDATE player_shop_tickets SET ticket_channel_id = ? WHERE id = ?",
        (channel.id, ticket_id),
    )
    await bot.db.db.commit()

    sale_note = " 🔥 (Sale)" if on_sale else ""
    discount_line = f"\n🏷️ **Rabatt:** {discount_note}" if discount_note else ""
    pay_line = (
        f"**Zahlung 1 — Shop-Währung ({format_price(price)}{sale_note}):**{discount_line}\n"
        f"```\n{config.mc_pay_command(price)}\n```\n"
        f"**Zahlung 2 — PayPal ({config.PAYPAL_EMAIL}):** fester Preis "
        f"**{config.PLAYER_ITEM_PAYPAL_PRICE:.2f} €** (unabhängig vom Shop-Preis)\n"
        "_Bei PayPal bitte „Freunde/Familie“ wählen, danach hier im Ticket Bescheid geben._"
    )
    is_coaching = item["kind"] == "coaching"
    note_label = "Wunschtermin" if is_coaching else "Notiz"
    note_line = f"\n{note_label}: **{note}**" if note else ""
    keybinds_text = (item.get("keybinds_text") or "").strip()
    keybinds_line = f"\n\n**⌨️ Keybinds**\n{keybinds_text[:900]}" if keybinds_text else ""
    payout_line = (
        f"\n\n_Staff-Hinweis: {payout_percent:g}% (≈ {format_price(price * payout_percent / 100)}) "
        f"an **{payout_recipient}** weiterleiten (`{config.mc_pay_command(price * payout_percent / 100)}`)._"
        if payout_percent > 0 and payout_recipient
        else ""
    )
    closing_line = (
        "Sobald die Zahlung bestätigt ist, klickt Staff **✅ Bestätigen** — der Termin wird dann hier abgestimmt."
        if is_coaching
        else "Sobald die Zahlung bestätigt ist, klickt Staff **✅ Bestätigen** — das Pack wird dann automatisch per DM geliefert."
    )
    embed = base_embed(
        f"🎮 {_kind_label(item['kind'])} · {player['name']} · {item['name']} — #{ticket_number}",
        f"Hallo {interaction.user.mention}, hier ist deine Bestellung.\n\n"
        f"Spieler: **{player['name']}**\n"
        f"{_kind_label(item['kind'])}: **{item['name']}**\n"
        f"Ingame-Name: **{ign}**{note_line}\n\n"
        f"{pay_line}"
        f"{keybinds_line}\n\n"
        f"{closing_line}"
        f"{payout_line}",
    )
    mention = staff_role.mention if staff_role else "Staff"
    preview_path = resolve_preview_path(item.get("preview_file"))
    await channel.send(
        content=f"{interaction.user.mention} {mention}",
        embed=embed,
        view=PlayerItemTicketView(bot),
        file=discord.File(preview_path, filename=preview_path.name) if preview_path else discord.utils.MISSING,
    )
    await interaction.followup.send(
        embed=success_embed(
            "Ticket erstellt",
            f"Dein Ticket: {channel.mention}\n"
            f"**{item['name']}** ({player['name']}) · {format_price(price)}{sale_note} "
            f"oder {config.PLAYER_ITEM_PAYPAL_PRICE:.2f} € PayPal",
        ),
        ephemeral=True,
    )


async def _get_ticket_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM player_shop_tickets WHERE ticket_channel_id = ?", (channel_id,)
    )
    return dict(row) if row else None


async def _mark_ticket(bot: "ShopBot", ticket_id: int, status: str, staff_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE player_shop_tickets SET status = ?, created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
        (status, staff_id, ticket_id),
    )
    await bot.db.db.commit()


class PlayerItemTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Bestätigen", style=discord.ButtonStyle.success, custom_id="playeritemticket:confirm", emoji="✅",
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Settings/Modpack-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_ticket(self.bot, int(row["id"]), "confirmed", interaction.user.id)
        from utils.referrals import credit_referral

        await credit_referral(self.bot, interaction.guild, int(row["user_id"]), float(row["price"]))

        member: discord.Member | None = None
        try:
            member = await interaction.guild.fetch_member(int(row["user_id"]))
        except discord.HTTPException:
            member = None

        is_coaching = row["kind"] == "coaching"
        keybinds_text = (row.get("keybinds_text") or "").strip()
        dm_text = (row.get("pack_dm_text") or "").strip()
        if keybinds_text:
            dm_text = f"{dm_text}\n\n⌨️ **Keybinds**\n{keybinds_text}".strip()

        delivery_note = ""
        if member is not None and isinstance(interaction.channel, discord.TextChannel):
            snap = {
                "name_snapshot": f"{row['item_name']} ({row['player_name']})",
                "qty": 1,
                "pack_dm_text": dm_text,
                "pack_link": row.get("pack_link") or "",
                "pack_file": row.get("pack_file") or "",
            }
            if snap["pack_dm_text"] or snap["pack_link"] or snap["pack_file"]:
                await deliver_packs(member, interaction.channel, [snap], bot=self.bot)
            elif is_coaching:
                delivery_note = "\n_Termin bitte hier im Ticket mit dem Käufer abstimmen._"
            else:
                delivery_note = "\n_Kein Pack hinterlegt — bitte manuell liefern._"

        payout_percent = float(row.get("payout_percent") or 0)
        payout_recipient = (row.get("payout_recipient") or "").strip()
        payout_note = ""
        if payout_percent > 0 and payout_recipient:
            amount = float(row["price"]) * payout_percent / 100
            payout_note = (
                f"\n💸 Nicht vergessen: **{payout_percent:g}%** ({format_price(amount)}) an "
                f"**{payout_recipient}** weiterleiten: `{config.mc_pay_command(amount)}`"
            )

        await interaction.followup.send(
            embed=success_embed(
                "Bestätigt",
                f"Kauf bestätigt von {interaction.user.mention}.{delivery_note}{payout_note}",
            )
        )

    @discord.ui.button(
        label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="playeritemticket:reject", emoji="❌",
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Settings/Modpack-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_ticket(self.bot, int(row["id"]), "rejected", interaction.user.id)
        trade_in_note = ""
        if row.get("traded_in_ticket_id"):
            await self.bot.db.db.execute(
                "UPDATE player_shop_tickets SET traded_in = 0 WHERE id = ?", (row["traded_in_ticket_id"],)
            )
            await self.bot.db.db.commit()
            trade_in_note = "\n_Eingetauschter alter Kauf wurde wieder freigegeben._"
        await interaction.followup.send(
            embed=warn_embed("Abgelehnt", f"Abgelehnt von {interaction.user.mention}.{trade_in_note}")
        )

    @discord.ui.button(
        label="Schließen", style=discord.ButtonStyle.secondary, custom_id="playeritemticket:close", emoji="🔒",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Settings/Modpack-Ticket"), ephemeral=True)
            return
        staff = await is_staff(self.bot, interaction)
        is_owner = row.get("user_id") and interaction.user.id == int(row["user_id"])
        if not staff and not is_owner:
            await interaction.response.send_message(embed=error_embed("Keine Berechtigung"), ephemeral=True)
            return
        await interaction.response.defer()
        if row["status"] == "pending":
            await _mark_ticket(self.bot, int(row["id"]), "rejected", interaction.user.id)
            if row.get("traded_in_ticket_id"):
                await self.bot.db.db.execute(
                    "UPDATE player_shop_tickets SET traded_in = 0 WHERE id = ?", (row["traded_in_ticket_id"],)
                )
                await self.bot.db.db.commit()
        await interaction.followup.send(
            embed=warn_embed(
                "Ticket wird geschlossen",
                f"Geschlossen von {interaction.user.mention}. Channel wird in 5 Sekunden gelöscht.",
            )
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Settings/Modpack-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


class PlayerShopPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Kaufen", style=discord.ButtonStyle.primary, custom_id="playershoppanel:buy", emoji="🎮",
    )
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await _open_player_picker(self.bot, interaction)


# ── Slash-Commands ───────────────────────────────────────────────────────

class PlayerShopCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot
        self.sale_reminder_loop.start()

    def cog_unload(self) -> None:
        self.sale_reminder_loop.cancel()

    @tasks.loop(minutes=10)
    async def sale_reminder_loop(self) -> None:
        try:
            n = await _notify_expired_sales(self.bot)
            if n:
                print(f"[PlayerShop] {n} abgelaufene Sale(s) gemeldet")
        except Exception as e:
            print(f"[PlayerShop] Sale-Reminder fehlgeschlagen: {e!r}")

    @sale_reminder_loop.before_loop
    async def before_sale_reminder_loop(self) -> None:
        await self.bot.wait_until_ready()

    player_group = app_commands.Group(
        name="spieler", description="Spieler für Settings/Modpack-Shop verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )
    item_group = app_commands.Group(
        name="spieleritem", description="Settings/Modpacks eines Spielers verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _player_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not interaction.guild_id:
            return []
        players = await list_players(self.bot, interaction.guild_id)
        q = (current or "").lower().strip()
        if q:
            players = [p for p in players if q in p["name"].lower()]
        return [app_commands.Choice(name=p["name"][:100], value=p["name"]) for p in players[:25]]

    async def _item_id_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if not interaction.guild_id:
            return []
        rows = await self.bot.db.fetchall(
            """
            SELECT i.id, i.name, i.kind, p.name AS player_name
            FROM shop_player_items i
            JOIN shop_players p ON p.id = i.player_id
            WHERE i.guild_id = ?
            ORDER BY p.name, i.name
            """,
            (interaction.guild_id,),
        )
        items = [dict(r) for r in rows]
        q = (current or "").lower().strip()
        if q:
            items = [
                i for i in items
                if q in i["name"].lower() or q in i["player_name"].lower() or q == str(i["id"])
            ]
        return [
            app_commands.Choice(
                name=f"{i['player_name']} · {i['name']} (#{i['id']})"[:100], value=int(i["id"])
            )
            for i in items[:25]
        ]

    @player_group.command(name="hinzufuegen", description="Neuen Spieler anlegen")
    @app_commands.describe(name="Name des Spielers/Creators")
    async def player_add(self, interaction: discord.Interaction, name: str) -> None:
        assert interaction.guild is not None
        existing = await find_player_by_name(self.bot, interaction.guild.id, name)
        if existing:
            await interaction.response.send_message(
                embed=error_embed(f"**{name}** gibt es schon."), ephemeral=True
            )
            return
        await self.bot.db.db.execute(
            "INSERT INTO shop_players (guild_id, name) VALUES (?, ?)",
            (interaction.guild.id, name.strip()),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Spieler angelegt", f"**{name}** — jetzt Settings/Modpacks hinzufügen: `/spieleritem hinzufuegen`"),
            ephemeral=True,
        )

    @player_group.command(name="entfernen", description="Spieler löschen (inkl. seiner Settings/Modpacks)")
    @app_commands.describe(name="Name des Spielers")
    async def player_remove(self, interaction: discord.Interaction, name: str) -> None:
        assert interaction.guild is not None
        player = await find_player_by_name(self.bot, interaction.guild.id, name)
        if not player:
            await interaction.response.send_message(embed=error_embed("Nicht gefunden"), ephemeral=True)
            return
        await self.bot.db.db.execute(
            "DELETE FROM shop_player_items WHERE guild_id = ? AND player_id = ?",
            (interaction.guild.id, player["id"]),
        )
        await self.bot.db.db.execute(
            "DELETE FROM shop_players WHERE id = ? AND guild_id = ?",
            (player["id"], interaction.guild.id),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Entfernt", f"**{name}** und alle seine Settings/Modpacks wurden gelöscht."),
            ephemeral=True,
        )

    @player_remove.autocomplete("name")
    async def player_remove_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._player_ac(interaction, current)

    @player_group.command(name="liste", description="Alle Spieler + ihre Settings/Modpacks anzeigen")
    async def player_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        players = await list_players(self.bot, interaction.guild.id)
        if not players:
            await interaction.response.send_message(embed=error_embed("Keine Spieler."), ephemeral=True)
            return
        lines = []
        for p in players:
            items = await list_player_items(self.bot, interaction.guild.id, int(p["id"]))
            payout = (
                f" · 💸 {float(p['payout_percent']):g}% → `{p['payout_recipient']}`"
                if float(p.get("payout_percent") or 0) > 0 and p.get("payout_recipient")
                else ""
            )
            lines.append(f"**{p['name']}**{_player_badge(p)}{payout}")
            if items:
                lines.extend(f"  {_item_line(i)}" for i in items)
            else:
                lines.append("  _keine Items_")
        await interaction.response.send_message(
            embed=base_embed("Spieler & Settings/Modpacks", "\n".join(lines)[:4000]),
            ephemeral=True,
        )

    @player_group.command(name="rolle", description="Staff-Rolle für Settings/Modpack-Tickets setzen")
    @app_commands.describe(rolle="Rolle, die in diesen Tickets gepingt wird")
    async def player_role(self, interaction: discord.Interaction, rolle: discord.Role) -> None:
        assert interaction.guild is not None
        await _set_staff_role_id(self.bot, interaction.guild.id, rolle.id)
        await interaction.response.send_message(
            embed=success_embed("Gespeichert", f"Settings/Modpack-Support-Rolle ist jetzt {rolle.mention}."),
            ephemeral=True,
        )

    @player_group.command(name="verifizieren", description="Spieler als vom Team verifiziert markieren/entfernen")
    @app_commands.describe(name="Name des Spielers (tippen zum Suchen)", status="Verifiziert oder nicht")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Verifiziert (🎖️)", value=1),
            app_commands.Choice(name="Nicht verifiziert", value=0),
        ],
    )
    async def player_verify(
        self, interaction: discord.Interaction, name: str, status: app_commands.Choice[int],
    ) -> None:
        assert interaction.guild is not None
        player = await find_player_by_name(self.bot, interaction.guild.id, name)
        if not player:
            await interaction.response.send_message(embed=error_embed("Nicht gefunden"), ephemeral=True)
            return
        await self.bot.db.db.execute(
            "UPDATE shop_players SET verified = ? WHERE id = ?", (status.value, player["id"])
        )
        await self.bot.db.db.commit()
        msg = f"**{player['name']}** ist jetzt {'🎖️ verifiziert' if status.value else 'nicht mehr verifiziert'}."
        await interaction.response.send_message(embed=success_embed("Gespeichert", msg), ephemeral=True)

    @player_verify.autocomplete("name")
    async def player_verify_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._player_ac(interaction, current)

    @player_group.command(name="stats", description="Umsatz-Statistik anzeigen (Spieler oder gesamt)")
    @app_commands.describe(name="Nur diesen Spieler zeigen (optional, tippen zum Suchen)")
    async def player_stats(self, interaction: discord.Interaction, name: Optional[str] = None) -> None:
        assert interaction.guild is not None
        params: list = [interaction.guild.id]
        where = "guild_id = ? AND status = 'confirmed'"
        title = "Statistik — Alle Spieler"
        if name:
            player = await find_player_by_name(self.bot, interaction.guild.id, name)
            if not player:
                await interaction.response.send_message(embed=error_embed("Spieler nicht gefunden"), ephemeral=True)
                return
            where += " AND player_name = ?"
            params.append(player["name"])
            title = f"Statistik — {player['name']}"

        rows = await self.bot.db.fetchall(
            f"""
            SELECT player_name, item_name, price, payout_percent
            FROM player_shop_tickets WHERE {where}
            """,
            tuple(params),
        )
        rows = [dict(r) for r in rows]
        if not rows:
            await interaction.response.send_message(
                embed=base_embed(title, "_Noch keine bestätigten Käufe._"), ephemeral=True,
            )
            return

        total_revenue = sum(float(r["price"]) for r in rows)
        total_payout = sum(float(r["price"]) * float(r["payout_percent"]) / 100 for r in rows)
        per_item: dict[str, list[float]] = {}
        for r in rows:
            key = f"{r['player_name']} · {r['item_name']}"
            per_item.setdefault(key, []).append(float(r["price"]))
        top = sorted(per_item.items(), key=lambda kv: sum(kv[1]), reverse=True)[:10]
        top_lines = "\n".join(
            f"• **{k}** — {len(v)}× · {format_price(sum(v))}" for k, v in top
        )

        body = (
            f"**Bestätigte Käufe:** {len(rows)}\n"
            f"**Gesamtumsatz:** {format_price(total_revenue)}\n"
            f"**Davon Provision (fällig/ausgezahlt):** {format_price(total_payout)}\n\n"
            f"**Top-Items**\n{top_lines}"
        )
        await interaction.response.send_message(
            embed=base_embed(title, body[:4000]), ephemeral=True,
        )

    @player_stats.autocomplete("name")
    async def player_stats_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._player_ac(interaction, current)

    @player_group.command(
        name="auszahlung",
        description="Provisions-Anteil für einen Spieler/Creator festlegen (0 = kein Anteil)",
    )
    @app_commands.describe(
        name="Name des Spielers (tippen zum Suchen)",
        prozent="Anteil am Verkaufspreis in %, z.B. 30 (0 = deaktivieren)",
        ingame_name="Minecraft-Name, an den Staff die Provision per /pay schickt",
    )
    async def player_payout(
        self, interaction: discord.Interaction, name: str, prozent: app_commands.Range[float, 0, 100],
        ingame_name: str = "",
    ) -> None:
        assert interaction.guild is not None
        player = await find_player_by_name(self.bot, interaction.guild.id, name)
        if not player:
            await interaction.response.send_message(embed=error_embed("Nicht gefunden"), ephemeral=True)
            return
        if prozent > 0 and not ingame_name.strip():
            await interaction.response.send_message(
                embed=error_embed("Ingame-Name fehlt", "Bei einem Anteil > 0% bitte `ingame_name` mit angeben."),
                ephemeral=True,
            )
            return
        await self.bot.db.db.execute(
            "UPDATE shop_players SET payout_percent = ?, payout_recipient = ? WHERE id = ?",
            (float(prozent), ingame_name.strip()[:32], player["id"]),
        )
        await self.bot.db.db.commit()
        if prozent > 0:
            msg = f"**{player['name']}** bekommt jetzt **{prozent:g}%** an `{ingame_name.strip()}`."
        else:
            msg = f"**{player['name']}** hat jetzt keinen Provisions-Anteil mehr."
        await interaction.response.send_message(embed=success_embed("Gespeichert", msg), ephemeral=True)

    @player_payout.autocomplete("name")
    async def player_payout_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._player_ac(interaction, current)

    @player_group.command(
        name="tauschwert",
        description="Eintausch-Rabatt aktivieren: alter bestätigter Kauf = % Rabatt auf neuen Kauf (0 = aus)",
    )
    @app_commands.describe(prozent="Wie viel % des alten Kaufpreises als Rabatt angerechnet werden (0 = aus)")
    async def player_trade_in(
        self, interaction: discord.Interaction, prozent: app_commands.Range[float, 0, 100],
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "INSERT INTO player_shop_settings (guild_id, trade_in_percent) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET trade_in_percent = excluded.trade_in_percent",
            (interaction.guild.id, float(prozent)),
        )
        await self.bot.db.db.commit()
        msg = (
            f"Eintausch-Rabatt ist jetzt **{prozent:g}%**."
            if prozent > 0
            else "Eintausch-Rabatt ist jetzt deaktiviert."
        )
        await interaction.response.send_message(embed=success_embed("Gespeichert", msg), ephemeral=True)

    @player_group.command(
        name="gruppenrabatt",
        description="Rabatt für Rollen-Träger, sobald N Käufe desselben Items bestätigt sind (0% = aus)",
    )
    @app_commands.describe(
        rolle="Rolle, die den Gruppen-Rabatt bekommt",
        prozent="Rabatt in %, z.B. 15 (0 = deaktivieren)",
        ab_kaeufen="Ab wie vielen bestätigten Käufen des gleichen Items der Rabatt greift (Standard 3)",
    )
    async def player_group_discount(
        self,
        interaction: discord.Interaction,
        rolle: discord.Role,
        prozent: app_commands.Range[float, 0, 100],
        ab_kaeufen: app_commands.Range[int, 1, 100] = 3,
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            """
            INSERT INTO player_shop_settings (guild_id, group_role_id, group_discount_percent, group_threshold)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                group_role_id = excluded.group_role_id,
                group_discount_percent = excluded.group_discount_percent,
                group_threshold = excluded.group_threshold
            """,
            (interaction.guild.id, rolle.id, float(prozent), int(ab_kaeufen)),
        )
        await self.bot.db.db.commit()
        if prozent > 0:
            msg = f"Träger von {rolle.mention} bekommen **{prozent:g}%** Rabatt, sobald **{ab_kaeufen}** Käufe desselben Items bestätigt sind."
        else:
            msg = "Gruppen-Rabatt ist jetzt deaktiviert."
        await interaction.response.send_message(embed=success_embed("Gespeichert", msg), ephemeral=True)

    @player_group.command(
        name="salelog", description="Channel setzen, in dem abgelaufene Sales gemeldet werden",
    )
    @app_commands.describe(channel="Ziel-Channel (leer = deaktivieren)")
    async def player_sale_log(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "INSERT INTO player_shop_settings (guild_id, sale_log_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET sale_log_channel_id = excluded.sale_log_channel_id",
            (interaction.guild.id, channel.id if channel else None),
        )
        await self.bot.db.db.commit()
        msg = f"Sale-Ablauf-Meldungen gehen jetzt in {channel.mention}." if channel else "Sale-Ablauf-Meldungen sind jetzt deaktiviert."
        await interaction.response.send_message(embed=success_embed("Gespeichert", msg), ephemeral=True)

    @item_group.command(name="hinzufuegen", description="Settings/Modpack/Coaching für einen Spieler anlegen")
    @app_commands.describe(
        spieler="Name des Spielers (tippen zum Suchen)",
        art="Settings, Modpack oder Coaching (buchbarer Termin)",
        name="Name des Settings/Modpacks/Coachings",
        preis="Shop-Währungspreis, z.B. 500k, 1.5m",
        datei="Pack-Datei per Anhang (optional)",
        link="Pack-Link (optional, alternativ zur Datei)",
        beschreibung="Text, der dem Käufer per DM mitgeschickt wird (optional)",
        bild="Vorschau-Bild oder -Video (png/jpg/gif/webp/mp4/mov/webm, optional)",
        keybinds="Keybind-Liste, wird im Ticket gezeigt + per DM mitgeliefert (optional)",
    )
    @app_commands.choices(
        art=[
            app_commands.Choice(name="Settings", value="settings"),
            app_commands.Choice(name="Modpack", value="modpack"),
            app_commands.Choice(name="Coaching", value="coaching"),
        ],
    )
    async def item_add(
        self,
        interaction: discord.Interaction,
        spieler: str,
        art: app_commands.Choice[str],
        name: str,
        preis: str,
        datei: Optional[discord.Attachment] = None,
        link: str = "",
        beschreibung: str = "",
        bild: Optional[discord.Attachment] = None,
        keybinds: str = "",
    ) -> None:
        assert interaction.guild is not None
        player = await find_player_by_name(self.bot, interaction.guild.id, spieler)
        if not player:
            await interaction.response.send_message(
                embed=error_embed("Spieler nicht gefunden", f"Erst anlegen: `/spieler hinzufuegen name:{spieler}`"),
                ephemeral=True,
            )
            return
        try:
            price = parse_price(preis)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Preis", "Beispiele: `500k`, `1.5m`, `9,99`"), ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        cur = await self.bot.db.db.execute(
            """
            INSERT INTO shop_player_items
                (guild_id, player_id, kind, name, price, pack_dm_text, pack_link, keybinds_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interaction.guild.id, player["id"], art.value, name.strip(), price,
                beschreibung[:1500], link[:500], keybinds[:1500],
            ),
        )
        await self.bot.db.db.commit()
        item_id = int(cur.lastrowid)  # type: ignore[arg-type]

        pack_note = ""
        if datei is not None:
            try:
                rel = await save_pack_attachment(item_id, datei)
                await self.bot.db.db.execute(
                    "UPDATE shop_player_items SET pack_file = ? WHERE id = ?", (rel, item_id)
                )
                await self.bot.db.db.commit()
                pack_note = f"\nPack gespeichert: **{datei.filename}** (nicht öffentlich)"
            except ValueError as e:
                pack_note = f"\nPack-Upload fehlgeschlagen: {e}"

        preview_note = ""
        if bild is not None:
            try:
                rel = await save_preview_attachment(item_id, bild)
                await self.bot.db.db.execute(
                    "UPDATE shop_player_items SET preview_file = ? WHERE id = ?", (rel, item_id)
                )
                await self.bot.db.db.commit()
                preview_note = f"\nVorschau gespeichert: **{bild.filename}**"
            except ValueError as e:
                preview_note = f"\nVorschau-Upload fehlgeschlagen: {e}"

        await interaction.followup.send(
            embed=success_embed(
                "Settings/Modpack angelegt",
                f"ID `{item_id}` — **{name}** ({_kind_label(art.value)}) für **{player['name']}**\n"
                f"{format_price(price)} · PayPal fest {config.PLAYER_ITEM_PAYPAL_PRICE:.2f} €"
                f"{pack_note}{preview_note}",
            ),
            ephemeral=True,
        )

    @item_add.autocomplete("spieler")
    async def item_add_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._player_ac(interaction, current)

    @item_group.command(name="setzen", description="Settings/Modpack bearbeiten")
    @app_commands.describe(
        item="Settings/Modpack (tippen zum Suchen)",
        name="Neuer Name (leer = unverändert)",
        preis="Neuer Preis, z.B. 500k (leer = unverändert)",
        datei="Neue Pack-Datei per Anhang (optional)",
        link="Neuer Pack-Link (optional)",
        beschreibung="Neuer DM-Text (optional)",
        bild="Neues Vorschau-Bild oder -Video (optional)",
        keybinds="Neue Keybind-Liste (optional)",
    )
    async def item_set(
        self,
        interaction: discord.Interaction,
        item: int,
        name: Optional[str] = None,
        preis: Optional[str] = None,
        datei: Optional[discord.Attachment] = None,
        link: Optional[str] = None,
        beschreibung: Optional[str] = None,
        bild: Optional[discord.Attachment] = None,
        keybinds: Optional[str] = None,
    ) -> None:
        assert interaction.guild is not None
        row = await get_player_item(self.bot, interaction.guild.id, item)
        if not row:
            await interaction.response.send_message(embed=error_embed("Nicht gefunden"), ephemeral=True)
            return

        updates: dict = {}
        if name and name.strip():
            updates["name"] = name.strip()
        if preis and preis.strip():
            try:
                price = parse_price(preis)
            except ValueError:
                await interaction.response.send_message(
                    embed=error_embed("Ungültiger Preis", "Beispiele: `500k`, `1.5m`, `9,99`"), ephemeral=True,
                )
                return
            updates["price"] = price
        if link is not None:
            updates["pack_link"] = link[:500]
        if beschreibung is not None:
            updates["pack_dm_text"] = beschreibung[:1500]
        if keybinds is not None:
            updates["keybinds_text"] = keybinds[:1500]

        await interaction.response.defer(ephemeral=True)
        if updates:
            cols = ", ".join(f"{k} = ?" for k in updates)
            await self.bot.db.db.execute(
                f"UPDATE shop_player_items SET {cols} WHERE id = ?",
                (*updates.values(), item),
            )
            await self.bot.db.db.commit()

        pack_note = ""
        if datei is not None:
            try:
                rel = await save_pack_attachment(item, datei)
                await self.bot.db.db.execute(
                    "UPDATE shop_player_items SET pack_file = ? WHERE id = ?", (rel, item)
                )
                await self.bot.db.db.commit()
                pack_note = f"\nPack aktualisiert: **{datei.filename}**"
            except ValueError as e:
                pack_note = f"\nPack-Upload fehlgeschlagen: {e}"

        preview_note = ""
        if bild is not None:
            try:
                rel = await save_preview_attachment(item, bild)
                await self.bot.db.db.execute(
                    "UPDATE shop_player_items SET preview_file = ? WHERE id = ?", (rel, item)
                )
                await self.bot.db.db.commit()
                preview_note = f"\nVorschau aktualisiert: **{bild.filename}**"
            except ValueError as e:
                preview_note = f"\nVorschau-Upload fehlgeschlagen: {e}"

        fresh = await get_player_item(self.bot, interaction.guild.id, item) or row
        await interaction.followup.send(
            embed=success_embed(
                "Aktualisiert",
                f"ID `{item}` — **{fresh['name']}** · {format_price(float(fresh['price']))}"
                f"{pack_note}{preview_note}",
            ),
            ephemeral=True,
        )

    @item_set.autocomplete("item")
    async def item_set_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._item_id_ac(interaction, current)

    @item_group.command(name="sale", description="Zeitlich begrenzten Sale-Preis setzen (STOP = beenden)")
    @app_commands.describe(
        item="Settings/Modpack (tippen zum Suchen)",
        preis="Sale-Preis, z.B. 300k — oder STOP zum sofortigen Beenden",
        dauer="Wie lange der Sale läuft, z.B. 24h, 3d, 1w (nicht nötig bei STOP)",
    )
    async def item_sale(
        self, interaction: discord.Interaction, item: int, preis: str, dauer: Optional[str] = None,
    ) -> None:
        assert interaction.guild is not None
        row = await get_player_item(self.bot, interaction.guild.id, item)
        if not row:
            await interaction.response.send_message(embed=error_embed("Nicht gefunden"), ephemeral=True)
            return

        if preis.strip().upper() == "STOP":
            await self.bot.db.db.execute(
                "UPDATE shop_player_items SET sale_price = NULL, sale_until = NULL WHERE id = ?", (item,)
            )
            await self.bot.db.db.commit()
            await interaction.response.send_message(
                embed=success_embed("Sale beendet", f"**{row['name']}** kostet wieder {format_price(float(row['price']))}."),
                ephemeral=True,
            )
            return

        if not dauer or not dauer.strip():
            await interaction.response.send_message(
                embed=error_embed("Dauer fehlt", "Z.B. `dauer:24h` oder `dauer:3d`."), ephemeral=True,
            )
            return
        try:
            sale_price = parse_price(preis)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Preis", "Beispiele: `300k`, `9,99`"), ephemeral=True,
            )
            return
        try:
            seconds = parse_duration(dauer)
        except ValueError as e:
            await interaction.response.send_message(embed=error_embed("Ungültige Dauer", str(e)), ephemeral=True)
            return
        if sale_price >= float(row["price"]):
            await interaction.response.send_message(
                embed=error_embed("Sale-Preis muss unter dem Normalpreis liegen."), ephemeral=True,
            )
            return

        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await self.bot.db.db.execute(
            "UPDATE shop_player_items SET sale_price = ?, sale_until = ?, sale_notified = 0 WHERE id = ?",
            (sale_price, until.strftime(_SALE_TIME_FMT), item),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed(
                "Sale aktiv",
                f"**{row['name']}**: ~~{format_price(float(row['price']))}~~ **{format_price(sale_price)}** "
                f"bis <t:{int(until.timestamp())}:f>.",
            ),
            ephemeral=True,
        )

    @item_sale.autocomplete("item")
    async def item_sale_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._item_id_ac(interaction, current)

    @item_group.command(name="entfernen", description="Settings/Modpack löschen")
    @app_commands.describe(item="Settings/Modpack (tippen zum Suchen)")
    async def item_remove(self, interaction: discord.Interaction, item: int) -> None:
        assert interaction.guild is not None
        row = await get_player_item(self.bot, interaction.guild.id, item)
        if not row:
            await interaction.response.send_message(embed=error_embed("Nicht gefunden"), ephemeral=True)
            return
        await self.bot.db.db.execute(
            "DELETE FROM shop_player_items WHERE id = ? AND guild_id = ?",
            (item, interaction.guild.id),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Gelöscht", f"**{row['name']}** entfernt."), ephemeral=True,
        )

    @item_remove.autocomplete("item")
    async def item_remove_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._item_id_ac(interaction, current)

    @item_group.command(name="liste", description="Settings/Modpacks auflisten")
    @app_commands.describe(spieler="Nur diesen Spieler zeigen (optional)")
    async def item_list(self, interaction: discord.Interaction, spieler: Optional[str] = None) -> None:
        assert interaction.guild is not None
        if spieler:
            player = await find_player_by_name(self.bot, interaction.guild.id, spieler)
            if not player:
                await interaction.response.send_message(embed=error_embed("Spieler nicht gefunden"), ephemeral=True)
                return
            items = await list_player_items(self.bot, interaction.guild.id, int(player["id"]))
            title = f"Settings/Modpacks — {player['name']}"
        else:
            rows = await self.bot.db.fetchall(
                "SELECT * FROM shop_player_items WHERE guild_id = ? ORDER BY sort_order, name",
                (interaction.guild.id,),
            )
            items = [dict(r) for r in rows]
            title = "Alle Settings/Modpacks"
        body = "\n".join(_item_line(i) for i in items) or "_Keine Einträge._"
        await interaction.response.send_message(
            embed=base_embed(title, body[:4000]), ephemeral=True,
        )

    @item_list.autocomplete("spieler")
    async def item_list_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._player_ac(interaction, current)

    @app_commands.command(
        name="spielerpanel",
        description="Settings/Modpack-Shop-Panel posten (Staff) — mit spieler: nur für einen Spieler",
    )
    @app_commands.describe(
        channel="Ziel-Channel (Standard: aktuell)",
        spieler="Nur für diesen Spieler ein eigenes Panel posten (optional, tippen zum Suchen)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def spielerpanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        spieler: Optional[str] = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        if spieler:
            player = await find_player_by_name(self.bot, interaction.guild.id, spieler)
            if not player:
                await interaction.followup.send(embed=error_embed("Spieler nicht gefunden"), ephemeral=True)
                return
            items = await list_player_items(self.bot, interaction.guild.id, int(player["id"]))
            ensure_player_panel_view(self.bot, int(player["id"]))
            msg = await target.send(
                embed=_player_panel_embed(player, items),
                view=PlayerShopSinglePanelView(self.bot, int(player["id"])),
            )
            await interaction.followup.send(
                embed=success_embed("Panel gepostet", f"**{player['name']}** in {target.mention}: {msg.jump_url}"),
                ephemeral=True,
            )
            return

        players = await list_players(self.bot, interaction.guild.id)
        msg = await target.send(embed=_panel_embed(players), view=PlayerShopPanelView(self.bot))
        await interaction.followup.send(
            embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True,
        )

    @spielerpanel.autocomplete("spieler")
    async def spielerpanel_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._player_ac(interaction, current)


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(PlayerShopCog(bot))
