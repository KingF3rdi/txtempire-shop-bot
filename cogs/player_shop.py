"""
player_shop.py
================

Settings/Modpacks von bestimmten Spielern (Content-Creatorn) — eine eigene,
vom normalen Kategorie/Item-Shop getrennte Struktur.

Jedes Settings-/Modpack-Item hat zwei Preise:
  - price:        normaler Shop-Währungspreis (per /pay Ingame-Befehl).
  - PayPal-Preis:  FEST config.PLAYER_ITEM_PAYPAL_PRICE (Standard 1,00 €),
                    unabhängig vom Shop-Preis — jedes Item ist immer auch
                    für diesen festen Betrag per PayPal kaufbar.

Ablauf: Panel mit "Kaufen"-Button -> Spieler wählen -> Settings/Modpack
wählen -> Modal (Ingame-Name) -> privates Ticket mit beiden Zahlungswegen
wird erstellt, Team bestätigt -> Pack wird per DM geliefert (deliver_packs).

Eigene Tabellen (shop_players, shop_player_items, player_shop_settings,
player_shop_tickets) — keine Änderung an db/database.py nötig.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.delivery import deliver_packs
from utils.embeds import base_embed, error_embed, format_price, success_embed, warn_embed
from utils.packs import save_pack_attachment
from utils.price import parse_price
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot

KIND_LABELS = {"settings": "Settings", "modpack": "Modpack"}


# ── DB Bootstrap & Helpers (eigene Tabellen, kein Eingriff in database.py) ──

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS shop_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
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
            pack_dm_text TEXT NOT NULL DEFAULT '',
            pack_link TEXT NOT NULL DEFAULT '',
            pack_file TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 99,
            FOREIGN KEY (player_id) REFERENCES shop_players(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS player_shop_settings (
            guild_id INTEGER PRIMARY KEY,
            staff_role_id INTEGER,
            next_ticket_number INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS player_shop_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            item_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            price REAL NOT NULL,
            ign TEXT NOT NULL,
            pack_dm_text TEXT NOT NULL DEFAULT '',
            pack_link TEXT NOT NULL DEFAULT '',
            pack_file TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            ticket_channel_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        """
    )
    await bot.db.db.commit()


async def _get_staff_role_id(bot: "ShopBot", guild_id: int) -> Optional[int]:
    row = await bot.db.fetchone(
        "SELECT staff_role_id FROM player_shop_settings WHERE guild_id = ?", (guild_id,)
    )
    return int(row["staff_role_id"]) if row and row["staff_role_id"] else None


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


async def get_player_item(bot: "ShopBot", guild_id: int, item_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM shop_player_items WHERE id = ? AND guild_id = ?", (item_id, guild_id)
    )
    return dict(row) if row else None


def _kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind.capitalize())


def _item_line(item: dict) -> str:
    return (
        f"`{item['id']}` **{item['name']}** ({_kind_label(item['kind'])}) · "
        f"{format_price(float(item['price']))} · PayPal "
        f"{config.PLAYER_ITEM_PAYPAL_PRICE:.2f} €"
    )


# ── UI: Panel, Spieler-/Item-Auswahl, IGN-Modal, Ticket-Buttons ─────────

def _panel_embed(players: list[dict]) -> discord.Embed:
    body = "\n".join(f"• **{p['name']}**" for p in players) or "_Noch keine Spieler konfiguriert._"
    return base_embed(
        "🎮 Settings & Modpacks",
        "Settings und Modpacks von unseren Spielern — wähle einen Spieler, dann das "
        "gewünschte Settings/Modpack.\n\n"
        f"{body}\n\n"
        f"Jedes Settings/Modpack kostet zusätzlich zum Shop-Preis immer auch nur "
        f"**{config.PLAYER_ITEM_PAYPAL_PRICE:.2f} € per PayPal**.",
    )


class PlayerItemIGNModal(discord.ui.Modal, title="Minecraft-Name"):
    ign = discord.ui.TextInput(
        label="Dein Minecraft-Name (für /pay)", max_length=32, required=True,
    )

    def __init__(self, bot: "ShopBot", item_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.item_id = item_id

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
        )


class PlayerItemSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", items: list[dict]) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(
                label=f"{i['name']} ({_kind_label(i['kind'])})"[:100],
                value=str(i["id"]),
                description=(
                    f"{format_price(float(i['price']))} · PayPal "
                    f"{config.PLAYER_ITEM_PAYPAL_PRICE:.2f} €"
                )[:100],
            )
            for i in items[:25]
        ]
        super().__init__(
            placeholder="Settings/Modpack auswählen ...",
            options=options,
            custom_id="playershop:itemselect",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        item_id = int(self.values[0])
        await interaction.response.send_modal(PlayerItemIGNModal(self.bot, item_id))


class PlayerSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", players: list[dict]) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(label=p["name"][:100], value=str(p["id"]))
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

    price = float(item["price"])
    ticket_number = await _next_ticket_number(bot, guild.id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO player_shop_tickets
            (guild_id, ticket_number, user_id, player_name, item_name, kind,
             price, ign, pack_dm_text, pack_link, pack_file, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            guild.id, ticket_number, interaction.user.id, player["name"], item["name"],
            item["kind"], price, ign,
            item.get("pack_dm_text") or "", item.get("pack_link") or "",
            item.get("pack_file") or "",
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

    pay_line = (
        f"**Zahlung 1 — Shop-Währung ({format_price(price)}):**\n"
        f"```\n{config.mc_pay_command(price)}\n```\n"
        f"**Zahlung 2 — PayPal ({config.PAYPAL_EMAIL}):** fester Preis "
        f"**{config.PLAYER_ITEM_PAYPAL_PRICE:.2f} €** (unabhängig vom Shop-Preis)\n"
        "_Bei PayPal bitte „Freunde/Familie“ wählen, danach hier im Ticket Bescheid geben._"
    )
    embed = base_embed(
        f"🎮 {_kind_label(item['kind'])} · {player['name']} · {item['name']} — #{ticket_number}",
        f"Hallo {interaction.user.mention}, hier ist deine Bestellung.\n\n"
        f"Spieler: **{player['name']}**\n"
        f"{_kind_label(item['kind'])}: **{item['name']}**\n"
        f"Ingame-Name: **{ign}**\n\n"
        f"{pay_line}\n\n"
        "Sobald die Zahlung bestätigt ist, klickt Staff **✅ Bestätigen** — "
        "das Pack wird dann automatisch per DM geliefert.",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(
        content=f"{interaction.user.mention} {mention}",
        embed=embed,
        view=PlayerItemTicketView(bot),
    )
    await interaction.followup.send(
        embed=success_embed(
            "Ticket erstellt",
            f"Dein Ticket: {channel.mention}\n"
            f"**{item['name']}** ({player['name']}) · {format_price(price)} "
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

        member: discord.Member | None = None
        try:
            member = await interaction.guild.fetch_member(int(row["user_id"]))
        except discord.HTTPException:
            member = None

        delivery_note = ""
        if member is not None and isinstance(interaction.channel, discord.TextChannel):
            snap = {
                "name_snapshot": f"{row['item_name']} ({row['player_name']})",
                "qty": 1,
                "pack_dm_text": row.get("pack_dm_text") or "",
                "pack_link": row.get("pack_link") or "",
                "pack_file": row.get("pack_file") or "",
            }
            if snap["pack_dm_text"] or snap["pack_link"] or snap["pack_file"]:
                await deliver_packs(member, interaction.channel, [snap], bot=self.bot)
            else:
                delivery_note = "\n_Kein Pack hinterlegt — bitte manuell liefern._"

        await interaction.followup.send(
            embed=success_embed(
                "Bestätigt",
                f"Kauf bestätigt von {interaction.user.mention}.{delivery_note}",
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
        await interaction.followup.send(embed=warn_embed("Abgelehnt", f"Abgelehnt von {interaction.user.mention}."))

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
            lines.append(f"**{p['name']}**")
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

    @item_group.command(name="hinzufuegen", description="Settings/Modpack für einen Spieler anlegen")
    @app_commands.describe(
        spieler="Name des Spielers (tippen zum Suchen)",
        art="Settings oder Modpack",
        name="Name des Settings/Modpacks",
        preis="Shop-Währungspreis, z.B. 500k, 1.5m",
        datei="Pack-Datei per Anhang (optional)",
        link="Pack-Link (optional, alternativ zur Datei)",
        beschreibung="Text, der dem Käufer per DM mitgeschickt wird (optional)",
    )
    @app_commands.choices(
        art=[
            app_commands.Choice(name="Settings", value="settings"),
            app_commands.Choice(name="Modpack", value="modpack"),
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
                (guild_id, player_id, kind, name, price, pack_dm_text, pack_link)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (interaction.guild.id, player["id"], art.value, name.strip(), price, beschreibung[:1500], link[:500]),
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

        await interaction.followup.send(
            embed=success_embed(
                "Settings/Modpack angelegt",
                f"ID `{item_id}` — **{name}** ({_kind_label(art.value)}) für **{player['name']}**\n"
                f"{format_price(price)} · PayPal fest {config.PLAYER_ITEM_PAYPAL_PRICE:.2f} €"
                f"{pack_note}",
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

        fresh = await get_player_item(self.bot, interaction.guild.id, item) or row
        await interaction.followup.send(
            embed=success_embed(
                "Aktualisiert",
                f"ID `{item}` — **{fresh['name']}** · {format_price(float(fresh['price']))}{pack_note}",
            ),
            ephemeral=True,
        )

    @item_set.autocomplete("item")
    async def item_set_ac(
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

    @app_commands.command(name="spielerpanel", description="Settings/Modpack-Shop-Panel posten (Staff)")
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def spielerpanel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        players = await list_players(self.bot, interaction.guild.id)
        msg = await target.send(embed=_panel_embed(players), view=PlayerShopPanelView(self.bot))
        await interaction.followup.send(
            embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(PlayerShopCog(bot))
