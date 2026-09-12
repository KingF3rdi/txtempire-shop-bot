"""
schematic_shop.py
==================

Schematic-Verkauf — wie custom_pack.py (Ticket + Datei-Upload-Lieferung),
aber mit eigenem Panel und einem von Staff gepflegten Katalog (wie
spawner_shop.py), statt einer freien Mengenangabe: Staff legt benannte
Schematics mit Preis an, Kunde wählt eine per Dropdown aus.

Ablauf: Panel mit "Schematic kaufen"-Button -> Auswahl-Menü der aktiven
Schematics -> privates Ticket (Zahlungsinfo wie bei Packs) -> Staff
bestätigt & lädt die fertige Datei hoch -> automatischer DM-Versand.

Eigene Tabellen (schematics, schematic_settings, schematic_tickets) - keine
Änderung an db/database.py nötig.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.embeds import (
    base_embed,
    error_embed,
    format_price,
    payee_details_text,
    payee_name,
    success_embed,
    warn_embed,
)
from utils.price import parse_price
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


# ── DB Bootstrap & Helpers ───────────────────────────────────────────────

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS schematics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 99,
            UNIQUE(guild_id, name)
        );
        CREATE TABLE IF NOT EXISTS schematic_settings (
            guild_id INTEGER PRIMARY KEY,
            next_ticket_number INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS schematic_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            schematic_id INTEGER,
            schematic_name TEXT NOT NULL,
            price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            ticket_channel_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        """
    )
    await bot.db.db.commit()


async def _next_ticket_number(bot: "ShopBot", guild_id: int) -> int:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO schematic_settings (guild_id) VALUES (?)", (guild_id,)
    )
    row = await bot.db.fetchone(
        "SELECT next_ticket_number FROM schematic_settings WHERE guild_id = ?", (guild_id,)
    )
    n = int(row["next_ticket_number"]) if row else 1
    await bot.db.db.execute(
        "UPDATE schematic_settings SET next_ticket_number = ? WHERE guild_id = ?",
        (n + 1, guild_id),
    )
    await bot.db.db.commit()
    return n


async def list_schematics(bot: "ShopBot", guild_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        "SELECT * FROM schematics WHERE guild_id = ? ORDER BY sort_order ASC, name ASC",
        (guild_id,),
    )
    return [dict(r) for r in rows]


async def get_schematic(bot: "ShopBot", schematic_id: int) -> Optional[dict]:
    row = await bot.db.fetchone("SELECT * FROM schematics WHERE id = ?", (schematic_id,))
    return dict(row) if row else None


async def _get_ticket_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM schematic_tickets WHERE ticket_channel_id = ?", (channel_id,)
    )
    return dict(row) if row else None


async def _set_ticket_channel(bot: "ShopBot", ticket_id: int, channel_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE schematic_tickets SET ticket_channel_id = ? WHERE id = ?", (channel_id, ticket_id)
    )
    await bot.db.db.commit()


async def _mark_confirmed(bot: "ShopBot", ticket_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE schematic_tickets SET status = 'confirmed', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
        (staff_id, ticket_id),
    )
    await bot.db.db.commit()


async def _mark_rejected(bot: "ShopBot", ticket_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE schematic_tickets SET status = 'rejected', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
        (staff_id, ticket_id),
    )
    await bot.db.db.commit()


async def _resolve_member(guild: discord.Guild, user_id: Optional[int]) -> Optional[discord.Member]:
    if not user_id:
        return None
    member = guild.get_member(int(user_id))
    if member is not None:
        return member
    try:
        return await guild.fetch_member(int(user_id))
    except discord.HTTPException:
        return None


def _price_line(s: dict) -> str:
    return f"🗺️ **{s['name']}** — {format_price(s['price'])}"


# ── UI: Panel, Auswahl, Ticket-Buttons ───────────────────────────────────

def _panel_embed(schematics: list[dict]) -> discord.Embed:
    body = "\n".join(_price_line(s) for s in schematics) or "_Noch keine Schematics im Angebot._"
    return base_embed(
        "🗺️ Schematic-Shop",
        f"{body}\n\nKlicke unten und wähle eine Schematic — danach wird ein privates Ticket erstellt.",
    )


class SchematicSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", schematics: list[dict]) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(
                label=s["name"], value=str(s["id"]), description=format_price(s["price"])[:100],
            )
            for s in schematics[:25]
        ]
        super().__init__(placeholder="Schematic auswählen ...", options=options, custom_id="schematic:select")

    async def callback(self, interaction: discord.Interaction) -> None:
        schematic = await get_schematic(self.bot, int(self.values[0]))
        if not schematic:
            await interaction.response.send_message(embed=error_embed("Nicht mehr verfügbar"), ephemeral=True)
            return
        await _create_schematic_ticket(self.bot, interaction, schematic=schematic)


async def _open_schematic_picker(bot: "ShopBot", interaction: discord.Interaction) -> None:
    assert interaction.guild is not None
    schematics = await list_schematics(bot, interaction.guild.id)
    if not schematics:
        await interaction.response.send_message(
            embed=warn_embed("Aktuell keine Schematics im Angebot."), ephemeral=True,
        )
        return
    view = discord.ui.View(timeout=180)
    view.add_item(SchematicSelect(bot, schematics))
    await interaction.response.send_message(content="Welche Schematic möchtest du kaufen?", view=view, ephemeral=True)


async def _create_schematic_ticket(bot: "ShopBot", interaction: discord.Interaction, *, schematic: dict) -> None:
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    settings = await bot.db.ensure_guild(guild.id)
    category_id = settings.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None
    staff_role_id = settings.get("staff_role_id")
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
        view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True,
    )
    staff_perms = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, attach_files=True, embed_links=True,
        read_message_history=True, manage_messages=True,
    )
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: bot_perms,
    }
    if isinstance(interaction.user, discord.Member):
        overwrites[interaction.user] = buyer_perms
    if staff_role:
        overwrites[staff_role] = staff_perms

    ticket_number = await _next_ticket_number(bot, guild.id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO schematic_tickets (guild_id, ticket_number, user_id, schematic_id, schematic_name, price, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """,
        (guild.id, ticket_number, interaction.user.id, schematic["id"], schematic["name"], schematic["price"]),
    )
    await bot.db.db.commit()
    ticket_id = int(cur.lastrowid)  # type: ignore[arg-type]

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    name = f"schematic-{ticket_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites,
            reason=f"Schematic-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return

    await _set_ticket_channel(bot, ticket_id, channel.id)

    embed = base_embed(
        f"🗺️ Schematic-Ticket #{ticket_number}",
        f"Käufer: {interaction.user.mention}\n"
        f"Schematic: **{schematic['name']}**\n"
        f"Preis: **{format_price(schematic['price'])}**\n\n"
        f"**{config.PAYMENT_NOTICE}**\n"
        f"Zahlung an **{payee_name(settings)}**:\n{payee_details_text(settings) or '_Keine Details hinterlegt_'}\n\n"
        "Sobald die Zahlung eingegangen ist, klickt Staff **✅ Bestätigen** und "
        "lädt die Schematic-Datei hoch — der Kunde bekommt sie automatisch per DM.",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(
        content=f"{interaction.user.mention} {mention}", embed=embed, view=SchematicTicketView(bot),
    )
    await interaction.followup.send(
        embed=success_embed("Ticket erstellt", f"Dein Ticket: {channel.mention}"), ephemeral=True,
    )


class SchematicDeliverModal(discord.ui.Modal, title="Schematic liefern"):
    def __init__(self, bot: "ShopBot", ticket: dict) -> None:
        super().__init__()
        self.bot = bot
        self.ticket = ticket
        self.file_upload = discord.ui.FileUpload(
            custom_id="schematic_file", max_values=1, min_values=1, required=True,
        )
        self.add_item(
            discord.ui.Label(
                text="Schematic-Datei",
                description="z. B. .litematic/.schem — maximal 25 MB",
                component=self.file_upload,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer()
        atts = self.file_upload.values
        if not atts:
            await interaction.followup.send(embed=error_embed("Keine Datei hochgeladen"), ephemeral=True)
            return
        att = atts[0]

        await _mark_confirmed(self.bot, int(self.ticket["id"]), interaction.user.id)

        buyer = await _resolve_member(interaction.guild, self.ticket.get("user_id"))
        dm_ok = True
        if buyer is not None:
            try:
                file_to_send = await att.to_file()
                await buyer.send(
                    embed=success_embed(
                        f"🗺️ Deine Schematic (#{self.ticket['ticket_number']})",
                        f"**{self.ticket['schematic_name']}** — viel Spaß beim Bauen!",
                    ),
                    file=file_to_send,
                )
            except discord.HTTPException:
                dm_ok = False
            else:
                from utils import tweak_vouch

                await tweak_vouch.request_vouch(
                    self.bot, interaction.guild, buyer,
                    product="Schematic", tier_label=str(self.ticket["schematic_name"]),
                )

        body = f"Bestätigt und geliefert von {interaction.user.mention}."
        if not dm_ok:
            body += "\n⚠️ DM an Käufer fehlgeschlagen (DMs geschlossen) — Datei oben manuell weitergeben."
        await interaction.followup.send(embed=success_embed("Schematic geliefert", body), file=await att.to_file())


class SchematicTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Bestätigen & liefern", style=discord.ButtonStyle.success, custom_id="schematicticket:confirm", emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Schematic-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.send_modal(SchematicDeliverModal(self.bot, row))

    @discord.ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="schematicticket:reject", emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Schematic-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_rejected(self.bot, int(row["id"]), interaction.user.id)
        await interaction.followup.send(embed=warn_embed("Abgelehnt", f"Abgelehnt von {interaction.user.mention}."))

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.secondary, custom_id="schematicticket:close", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Schematic-Ticket"), ephemeral=True)
            return
        staff = await is_staff(self.bot, interaction)
        is_owner = row.get("user_id") and interaction.user.id == int(row["user_id"])
        if not staff and not is_owner:
            await interaction.response.send_message(embed=error_embed("Keine Berechtigung"), ephemeral=True)
            return
        await interaction.response.defer()
        if row["status"] == "pending":
            await _mark_rejected(self.bot, int(row["id"]), interaction.user.id)
        await interaction.followup.send(
            embed=warn_embed("Ticket wird geschlossen", f"Geschlossen von {interaction.user.mention}. Channel wird in 5 Sekunden gelöscht.")
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Schematic-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


class SchematicPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Schematic kaufen", style=discord.ButtonStyle.primary, custom_id="schematicpanel:buy", emoji="🗺️")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await _open_schematic_picker(self.bot, interaction)


# ── Slash-Commands ───────────────────────────────────────────────────────

class SchematicShopCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    schematic_group = app_commands.Group(
        name="schematic", description="Schematics verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @app_commands.command(name="schematicpanel", description="Schematic-Shop-Panel posten (Staff)")
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def schematicpanel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        schematics = await list_schematics(self.bot, interaction.guild.id)
        msg = await target.send(embed=_panel_embed(schematics), view=SchematicPanelView(self.bot))
        await interaction.followup.send(
            embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True,
        )

    @schematic_group.command(name="liste", description="Alle Schematics anzeigen")
    async def liste(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        rows = await list_schematics(self.bot, interaction.guild.id)
        body = "\n".join(f"`{s['id']}` {_price_line(s)}" for s in rows) or "_Keine Schematics._"
        await interaction.response.send_message(embed=base_embed("Schematics", body), ephemeral=True)

    @schematic_group.command(name="hinzufuegen", description="Neue Schematic anlegen")
    @app_commands.describe(name="Name der Schematic", preis="Preis, z. B. 4.99", beschreibung="Optional")
    async def hinzufuegen(
        self, interaction: discord.Interaction, name: str, preis: str, beschreibung: Optional[str] = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            price = parse_price(preis)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Preis", "Beispiele: `4.99`, `500k`."), ephemeral=True,
            )
            return
        existing = await self.bot.db.fetchone(
            "SELECT id FROM schematics WHERE guild_id = ? AND lower(name) = lower(?)",
            (interaction.guild.id, name.strip()),
        )
        if existing:
            await interaction.response.send_message(
                embed=error_embed("Gibt es schon", f"**{name}** existiert bereits. Nutze `/schematic entfernen`."),
                ephemeral=True,
            )
            return
        await self.bot.db.db.execute(
            "INSERT INTO schematics (guild_id, name, price, description) VALUES (?, ?, ?, ?)",
            (interaction.guild.id, name.strip(), price, (beschreibung or "").strip()),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Angelegt", f"**{name}** — {format_price(price)}"), ephemeral=True,
        )

    @schematic_group.command(name="entfernen", description="Schematic löschen")
    @app_commands.describe(name="Name der Schematic")
    async def entfernen(self, interaction: discord.Interaction, name: str) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "DELETE FROM schematics WHERE guild_id = ? AND lower(name) = lower(?)",
            (interaction.guild.id, name.strip()),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Entfernt", f"**{name}** wurde gelöscht."), ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(SchematicShopCog(bot))
