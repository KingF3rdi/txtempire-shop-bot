"""
spawner_shop.py
================

Spawner-Ankauf/Verkauf, portiert aus dem TXTBot-Projekt (dort spawners.ts).

Jeder Spawner hat zwei unabhängige Preise:
  - buy_price:  was DER SHOP zahlt, wenn ein Kunde einen Spawner AN den
                Shop verkauft (Richtung "sell" aus Kundensicht).
  - sell_price: was DER SHOP verlangt, wenn ein Kunde einen Spawner VOM
                Shop kauft (Richtung "buy" aus Kundensicht).
Ein Preis kann NULL sein ("STOP") - dann ist diese Richtung für den
jeweiligen Spawner deaktiviert.

Ablauf: Panel mit "Kaufen"/"Verkaufen"-Buttons -> Auswahl-Menü der
verfügbaren Spawner für die Richtung -> Modal (Menge + Ingame-Name) ->
privates Ticket wird erstellt, genau wie bei custom_pack.py/gputweaks_keys.py.

Eigene Tabellen (spawners, spawner_settings, spawner_tickets) - keine
Änderung an db/database.py nötig.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.embeds import base_embed, error_embed, format_price, success_embed, warn_embed
from utils.price import parse_price
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


def parse_price_or_stop(raw: str) -> Optional[float]:
    """Wie parse_price, aber "stop"/"-" ergibt None (Richtung deaktiviert)."""
    if raw.strip().lower() in ("stop", "-", "aus", "off"):
        return None
    return parse_price(raw)


# ── DB Bootstrap & Helpers (eigene Tabellen, kein Eingriff in database.py) ──

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS spawners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            buy_price REAL,
            sell_price REAL,
            emoji TEXT NOT NULL DEFAULT '🧱',
            sort_order INTEGER NOT NULL DEFAULT 99
        );
        CREATE TABLE IF NOT EXISTS spawner_settings (
            guild_id INTEGER PRIMARY KEY,
            staff_role_id INTEGER,
            next_ticket_number INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS spawner_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            spawner_name TEXT NOT NULL,
            spawner_emoji TEXT NOT NULL DEFAULT '🧱',
            qty INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            total REAL NOT NULL,
            ign TEXT NOT NULL,
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
        "SELECT staff_role_id FROM spawner_settings WHERE guild_id = ?", (guild_id,)
    )
    return int(row["staff_role_id"]) if row and row["staff_role_id"] else None


async def _set_staff_role_id(bot: "ShopBot", guild_id: int, role_id: int) -> None:
    await bot.db.db.execute(
        """
        INSERT INTO spawner_settings (guild_id, staff_role_id) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET staff_role_id = excluded.staff_role_id
        """,
        (guild_id, role_id),
    )
    await bot.db.db.commit()


async def _next_ticket_number(bot: "ShopBot", guild_id: int) -> int:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO spawner_settings (guild_id) VALUES (?)", (guild_id,)
    )
    row = await bot.db.fetchone(
        "SELECT next_ticket_number FROM spawner_settings WHERE guild_id = ?", (guild_id,)
    )
    n = int(row["next_ticket_number"]) if row else 1
    await bot.db.db.execute(
        "UPDATE spawner_settings SET next_ticket_number = ? WHERE guild_id = ?",
        (n + 1, guild_id),
    )
    await bot.db.db.commit()
    return n


async def list_spawners(bot: "ShopBot", guild_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        "SELECT * FROM spawners WHERE guild_id = ? ORDER BY sort_order ASC, name ASC",
        (guild_id,),
    )
    return [dict(r) for r in rows]


async def get_spawner(bot: "ShopBot", spawner_id: int) -> Optional[dict]:
    row = await bot.db.fetchone("SELECT * FROM spawners WHERE id = ?", (spawner_id,))
    return dict(row) if row else None


async def upsert_spawner(
    bot: "ShopBot",
    guild_id: int,
    name: str,
    buy: Optional[float],
    sell: Optional[float],
    emoji: Optional[str],
    *,
    mode: str,
) -> str:
    existing = await bot.db.fetchone(
        "SELECT id, emoji FROM spawners WHERE guild_id = ? AND lower(name) = lower(?)",
        (guild_id, name),
    )
    if mode == "create" and existing:
        raise ValueError(f"**{name}** gibt es schon. Nutze `/spawner setzen` zum Bearbeiten.")
    if mode == "update" and not existing:
        raise ValueError(f"**{name}** nicht gefunden. Lege ihn mit `/spawner hinzufuegen` an.")

    mark = (emoji or "").strip() or (existing["emoji"] if existing else None) or "🧱"
    if existing:
        await bot.db.db.execute(
            "UPDATE spawners SET buy_price = ?, sell_price = ?, emoji = ? WHERE id = ?",
            (buy, sell, mark, existing["id"]),
        )
        await bot.db.db.commit()
        return "updated"
    await bot.db.db.execute(
        "INSERT INTO spawners (guild_id, name, buy_price, sell_price, emoji) VALUES (?, ?, ?, ?, ?)",
        (guild_id, name, buy, sell, mark),
    )
    await bot.db.db.commit()
    return "created"


async def _get_ticket_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM spawner_tickets WHERE ticket_channel_id = ?", (channel_id,)
    )
    return dict(row) if row else None


async def _set_ticket_channel(bot: "ShopBot", ticket_id: int, channel_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE spawner_tickets SET ticket_channel_id = ? WHERE id = ?", (channel_id, ticket_id)
    )
    await bot.db.db.commit()


async def _mark_ticket(bot: "ShopBot", ticket_id: int, status: str, staff_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE spawner_tickets SET status = ?, created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
        (status, staff_id, ticket_id),
    )
    await bot.db.db.commit()


def _price_line(s: dict) -> str:
    buy = format_price(s["buy_price"]) if s["buy_price"] is not None else "STOP"
    sell = format_price(s["sell_price"]) if s["sell_price"] is not None else "STOP"
    return f"{s.get('emoji') or '🧱'} **{s['name']}** · 📥 Ankauf `{buy}` · 📤 Verkauf `{sell}`"


# ── UI: Panel, Auswahl, Menge/IGN-Modal, Ticket-Buttons ─────────────────

def _panel_embed(spawners: list[dict]) -> discord.Embed:
    body = "\n".join(_price_line(s) for s in spawners) or "_Noch keine Spawner konfiguriert._"
    return base_embed(
        "🧱 Spawner-Shop",
        "Kaufe oder verkaufe Spawner direkt beim Team — nur für Trusted Traders.\n\n"
        f"{body}\n\n"
        "📥 Ankauf = wir kaufen dir ab · 📤 Verkauf = du kaufst von uns.",
    )


class SpawnerQtyModal(discord.ui.Modal, title="Spawner-Menge"):
    qty = discord.ui.TextInput(
        label="Menge", placeholder="z. B. 1", max_length=3, required=True, default="1",
    )
    ign = discord.ui.TextInput(
        label="Dein Minecraft-Name (für /pay)", max_length=32, required=True,
    )

    def __init__(self, bot: "ShopBot", spawner_id: int, direction: str) -> None:
        super().__init__()
        self.bot = bot
        self.spawner_id = spawner_id
        self.direction = direction

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.qty.value).strip()
        if not raw.isdigit() or not (1 <= int(raw) <= 999):
            await interaction.response.send_message(
                embed=error_embed("Ungültige Menge", "Bitte eine Zahl zwischen 1 und 999 eingeben."),
                ephemeral=True,
            )
            return
        ign = str(self.ign.value).strip()
        if not ign:
            await interaction.response.send_message(
                embed=error_embed("Minecraft-Name fehlt"), ephemeral=True
            )
            return
        spawner = await get_spawner(self.bot, self.spawner_id)
        if not spawner:
            await interaction.response.send_message(embed=error_embed("Spawner nicht gefunden"), ephemeral=True)
            return
        unit = spawner["sell_price"] if self.direction == "buy" else spawner["buy_price"]
        if unit is None:
            await interaction.response.send_message(
                embed=error_embed("Nicht verfügbar", f"**{spawner['name']}** ist für diese Richtung auf STOP."),
                ephemeral=True,
            )
            return
        await _create_spawner_ticket(
            self.bot, interaction, spawner=spawner, direction=self.direction,
            qty=int(raw), unit=float(unit), ign=ign,
        )


class SpawnerSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", direction: str, spawners: list[dict]) -> None:
        self.bot = bot
        self.direction = direction
        options = [
            discord.SelectOption(
                label=s["name"],
                value=str(s["id"]),
                description=(
                    f"Verkauf {format_price(s['sell_price'])}" if direction == "buy"
                    else f"Ankauf {format_price(s['buy_price'])}"
                )[:100],
            )
            for s in spawners[:25]
        ]
        super().__init__(
            placeholder="Spawner auswählen ...",
            options=options,
            custom_id=f"spawner:select:{direction}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        spawner_id = int(self.values[0])
        await interaction.response.send_modal(SpawnerQtyModal(self.bot, spawner_id, self.direction))


async def _open_spawner_picker(bot: "ShopBot", interaction: discord.Interaction, direction: str) -> None:
    assert interaction.guild is not None
    spawners = [
        s for s in await list_spawners(bot, interaction.guild.id)
        if (s["sell_price"] is not None if direction == "buy" else s["buy_price"] is not None)
    ]
    if not spawners:
        await interaction.response.send_message(
            embed=warn_embed(
                "Aktuell keine Spawner im Verkauf (alles auf STOP)." if direction == "buy"
                else "Aktuell kaufen wir keine Spawner an."
            ),
            ephemeral=True,
        )
        return
    label = "kaufen" if direction == "buy" else "verkaufen"
    view = discord.ui.View(timeout=180)
    view.add_item(SpawnerSelect(bot, direction, spawners))
    await interaction.response.send_message(
        content=f"Welchen Spawner möchtest du **{label}**?", view=view, ephemeral=True,
    )


async def _create_spawner_ticket(
    bot: "ShopBot",
    interaction: discord.Interaction,
    *,
    spawner: dict,
    direction: str,
    qty: int,
    unit: float,
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

    total = round(unit * qty, 2)
    ticket_number = await _next_ticket_number(bot, guild.id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO spawner_tickets
            (guild_id, ticket_number, user_id, direction, spawner_name, spawner_emoji,
             qty, unit_price, total, ign, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            guild.id, ticket_number, interaction.user.id, direction, spawner["name"],
            spawner.get("emoji") or "🧱", qty, unit, total, ign,
        ),
    )
    await bot.db.db.commit()
    ticket_id = int(cur.lastrowid)  # type: ignore[arg-type]

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    prefix = "spawner-kauf" if direction == "buy" else "spawner-ankauf"
    name = f"{prefix}-{ticket_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites,
            reason=f"Spawner-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return

    await _set_ticket_channel(bot, ticket_id, channel.id)

    if direction == "buy":
        heading = f"Hallo {interaction.user.mention}, hier ist deine **Kauf**-Anfrage."
        pay_line = (
            f"Du zahlst **{format_price(total)}** an den Shop:\n"
            f"```\n{config.mc_pay_command(total)}\n```"
        )
    else:
        heading = f"Hallo {interaction.user.mention}, hier ist deine **Ankauf**-Anfrage (du verkaufst an uns)."
        pay_line = (
            f"Das Team zahlt **{format_price(total)}** an dich (`{ign}`):\n"
            f"```\n/pay {ign} {total:g}\n```\n"
            "_(Staff-Hinweis: Betrag ingame an den Kunden schicken.)_"
        )

    embed = base_embed(
        f"{spawner.get('emoji') or '🧱'} Spawner-{'Kauf' if direction == 'buy' else 'Ankauf'} #{ticket_number}",
        f"{heading}\n\n"
        f"Produkt: **{spawner['name']}**-Spawner × **{qty}**\n"
        f"Einzelpreis: **{format_price(unit)}** · Gesamt: **{format_price(total)}**\n"
        f"Ingame-Name: **{ign}**\n\n"
        f"{pay_line}\n\n"
        "Sobald die Zahlung bestätigt ist, klickt Staff **✅ Bestätigen**.",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(
        content=f"{interaction.user.mention} {mention}",
        embed=embed,
        view=SpawnerTicketView(bot),
    )
    await interaction.followup.send(
        embed=success_embed(
            "Ticket erstellt",
            f"Dein Ticket: {channel.mention}\n**{qty}× {spawner['name']}** · Gesamt **{format_price(total)}**",
        ),
        ephemeral=True,
    )


class SpawnerTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Bestätigen", style=discord.ButtonStyle.success, custom_id="spawnerticket:confirm", emoji="✅",
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Spawner-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_ticket(self.bot, int(row["id"]), "confirmed", interaction.user.id)
        await interaction.followup.send(
            embed=success_embed(
                "Bestätigt",
                f"Handel bestätigt von {interaction.user.mention}. Ticket kann jetzt geschlossen werden.",
            )
        )

    @discord.ui.button(
        label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="spawnerticket:reject", emoji="❌",
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Spawner-Ticket"), ephemeral=True)
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
        label="Schließen", style=discord.ButtonStyle.secondary, custom_id="spawnerticket:close", emoji="🔒",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Spawner-Ticket"), ephemeral=True)
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
            await interaction.channel.delete(reason=f"Spawner-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


class SpawnerPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Kaufen", style=discord.ButtonStyle.primary, custom_id="spawnerpanel:buy", emoji="📤",
    )
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await _open_spawner_picker(self.bot, interaction, "buy")

    @discord.ui.button(
        label="Verkaufen", style=discord.ButtonStyle.secondary, custom_id="spawnerpanel:sell", emoji="📥",
    )
    async def sell(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await _open_spawner_picker(self.bot, interaction, "sell")


# ── Slash-Commands ───────────────────────────────────────────────────────

class SpawnerShopCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    spawner_group = app_commands.Group(
        name="spawner", description="Spawner-Preise verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @app_commands.command(
        name="spawnerpanel", description="Spawner-Shop-Panel posten (Staff)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def spawnerpanel(
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
        spawners = await list_spawners(self.bot, interaction.guild.id)
        msg = await target.send(embed=_panel_embed(spawners), view=SpawnerPanelView(self.bot))
        await interaction.followup.send(
            embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True,
        )

    @spawner_group.command(name="liste", description="Alle Spawner-Preise anzeigen")
    async def spawner_liste(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        rows = await list_spawners(self.bot, interaction.guild.id)
        body = "\n".join(f"`{s['id']}` {_price_line(s)}" for s in rows) or "_Keine Spawner._"
        await interaction.response.send_message(
            embed=base_embed("Spawner-Preise", body), ephemeral=True,
        )

    @spawner_group.command(name="hinzufuegen", description="Neuen Spawner anlegen")
    @app_commands.describe(
        name="Spawner-Name", ankauf="Preis wenn wir kaufen (oder STOP)",
        verkauf="Preis wenn wir verkaufen (oder STOP)", emoji="Emoji (optional)",
    )
    async def spawner_add(
        self, interaction: discord.Interaction, name: str, ankauf: str, verkauf: str,
        emoji: Optional[str] = None,
    ) -> None:
        await self._upsert(interaction, name, ankauf, verkauf, emoji, mode="create")

    @spawner_group.command(name="setzen", description="Spawner-Preise bearbeiten")
    @app_commands.describe(
        name="Spawner-Name", ankauf="Preis wenn wir kaufen (oder STOP)",
        verkauf="Preis wenn wir verkaufen (oder STOP)", emoji="Emoji (optional)",
    )
    async def spawner_set(
        self, interaction: discord.Interaction, name: str, ankauf: str, verkauf: str,
        emoji: Optional[str] = None,
    ) -> None:
        await self._upsert(interaction, name, ankauf, verkauf, emoji, mode="update")

    async def _upsert(
        self, interaction: discord.Interaction, name: str, ankauf: str, verkauf: str,
        emoji: Optional[str], *, mode: str,
    ) -> None:
        assert interaction.guild is not None
        try:
            buy = parse_price_or_stop(ankauf)
            sell = parse_price_or_stop(verkauf)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Preis", "Beispiele: `500k`, `1.5m`, `STOP`."), ephemeral=True,
            )
            return
        try:
            result = await upsert_spawner(self.bot, interaction.guild.id, name.strip(), buy, sell, emoji, mode=mode)
        except ValueError as e:
            await interaction.response.send_message(embed=error_embed("Fehler", str(e)), ephemeral=True)
            return
        buy_txt = format_price(buy) if buy is not None else "STOP"
        sell_txt = format_price(sell) if sell is not None else "STOP"
        await interaction.response.send_message(
            embed=success_embed(
                "Spawner gespeichert" if result == "created" else "Spawner aktualisiert",
                f"**{name}**: Ankauf `{buy_txt}` · Verkauf `{sell_txt}`",
            ),
            ephemeral=True,
        )

    @spawner_group.command(name="entfernen", description="Spawner löschen")
    @app_commands.describe(name="Spawner-Name")
    async def spawner_remove(self, interaction: discord.Interaction, name: str) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "DELETE FROM spawners WHERE guild_id = ? AND lower(name) = lower(?)",
            (interaction.guild.id, name.strip()),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Entfernt", f"Spawner **{name}** wurde gelöscht."), ephemeral=True,
        )

    @spawner_group.command(name="emoji", description="Emoji eines Spawners ändern")
    @app_commands.describe(name="Spawner-Name", emoji="Neues Emoji")
    async def spawner_emoji(self, interaction: discord.Interaction, name: str, emoji: str) -> None:
        assert interaction.guild is not None
        existing = await self.bot.db.fetchone(
            "SELECT id FROM spawners WHERE guild_id = ? AND lower(name) = lower(?)",
            (interaction.guild.id, name.strip()),
        )
        if not existing:
            await interaction.response.send_message(embed=error_embed(f"**{name}** nicht gefunden."), ephemeral=True)
            return
        await self.bot.db.db.execute(
            "UPDATE spawners SET emoji = ? WHERE id = ?", (emoji.strip(), existing["id"])
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Emoji aktualisiert", f"**{name}** ist jetzt {emoji.strip()}."), ephemeral=True,
        )

    @spawner_group.command(name="rolle", description="Staff-Rolle für Spawner-Tickets setzen")
    @app_commands.describe(rolle="Rolle, die in Spawner-Tickets gepingt wird")
    async def spawner_role(self, interaction: discord.Interaction, rolle: discord.Role) -> None:
        assert interaction.guild is not None
        await _set_staff_role_id(self.bot, interaction.guild.id, rolle.id)
        await interaction.response.send_message(
            embed=success_embed(
                "Gespeichert",
                f"Spawner-Support-Rolle ist jetzt {rolle.mention}. "
                "Nur diese Rolle (plus normale Ticket-Staff) wird in Spawner-Tickets gepingt.",
            ),
            ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(SpawnerShopCog(bot))
