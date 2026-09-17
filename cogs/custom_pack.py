"""
custom_pack.py
================

Custom-Texturepack- und Custom-Sky-Bestellungen (Kunde gibt Details an,
Staff liefert das fertige Ergebnis als Datei direkt im Ticket).

Texturepack-Preisstaffel (Gesamtpreis pro Bestellung, nicht pro Textur):
    bis 10 Texturen   -> 0,20 €
    bis 20 Texturen   -> 0,30 €
    bis 30 Texturen   -> 0,40 €
    bis 50 Texturen   -> 0,50 €
    bis 70 Texturen   -> 1,00 €
    mehr als 70       -> Preis auf Anfrage (Staff nennt Betrag im Ticket)

Custom Sky: fester Preis (config.CUSTOM_SKY_PRICE), keine Mengenstaffel.

Jede Bestellung ist ZUSÄTZLICH zum Echtgeld-Preis immer auch für einen
festen Ingame-Betrag kaufbar (config.CUSTOM_PACK_INGAME_PRICE bzw.
CUSTOM_SKY_INGAME_PRICE) — Zahlung per /pay, wird wie beim Haupt-Shop
automatisch erkannt (utils/mc_order_match.py), Staff bestätigt danach wie
gewohnt per Button.

Ablauf:
  - Kunde klickt "📦 Pack anfragen" oder "🌌 Sky anfragen" -> Modal ->
    privates Ticket wird erstellt (fortlaufend nummeriert), zeigt beide
    Zahlungsoptionen.
  - Staff bestätigt im Ticket ("✅ Bestätigen") -> öffnet ein natives
    Datei-Upload-Feld (discord.py 2.7 Components-V2, wie beim File-Scanner)
    zum Hochladen der fertigen Datei -> wird automatisch per DM an den
    Kunden geschickt.

Eigene Tabelle (pack_orders) - keine Änderung an db/database.py nötig.
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
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot

# (max_qty, total_price) - erste passende Stufe (qty <= max_qty) gewinnt.
PRICE_BRACKETS: tuple[tuple[int, float], ...] = (
    (10, 0.20),
    (20, 0.30),
    (30, 0.40),
    (50, 0.50),
    (70, 1.00),
)


def price_for_qty(qty: int) -> Optional[float]:
    for max_qty, price in PRICE_BRACKETS:
        if qty <= max_qty:
            return price
    return None  # > 70 -> Preis auf Anfrage


def price_label(qty: int) -> str:
    price = price_for_qty(qty)
    return format_price(price) if price is not None else "Preis auf Anfrage"


def brackets_overview() -> str:
    lines = []
    prev = 0
    for max_qty, price in PRICE_BRACKETS:
        lo = prev + 1
        lines.append(f"**{lo}-{max_qty} Texturen** — {format_price(price)}")
        prev = max_qty
    lines.append(f"**Mehr als {prev} Texturen** — Preis auf Anfrage")
    return "\n".join(lines)


# ── DB Bootstrap & Helpers (eigene Tabelle, kein Eingriff in database.py) ──

async def _ensure_table(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS pack_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            order_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            qty INTEGER NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price REAL,
            kind TEXT NOT NULL DEFAULT 'texturepack',
            ingame_price REAL,
            status TEXT NOT NULL DEFAULT 'pending',
            ticket_channel_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pack_settings (
            guild_id INTEGER PRIMARY KEY,
            next_order_number INTEGER NOT NULL DEFAULT 1,
            support_role_id INTEGER
        );
        """
    )
    # Additive Spalten für bereits bestehende Installationen (idempotent).
    for stmt in (
        "ALTER TABLE pack_orders ADD COLUMN kind TEXT NOT NULL DEFAULT 'texturepack'",
        "ALTER TABLE pack_orders ADD COLUMN ingame_price REAL",
        "ALTER TABLE pack_settings ADD COLUMN support_role_id INTEGER",
    ):
        try:
            await bot.db.db.execute(stmt)
        except Exception:
            pass
    await bot.db.db.commit()


async def _next_order_number(bot: "ShopBot", guild_id: int) -> int:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO pack_settings (guild_id, next_order_number) VALUES (?, 1)",
        (guild_id,),
    )
    row = await bot.db.fetchone(
        "SELECT next_order_number FROM pack_settings WHERE guild_id = ?", (guild_id,)
    )
    n = int(row["next_order_number"]) if row else 1
    await bot.db.db.execute(
        "UPDATE pack_settings SET next_order_number = ? WHERE guild_id = ?",
        (n + 1, guild_id),
    )
    await bot.db.db.commit()
    return n


async def _get_pack_settings(bot: "ShopBot", guild_id: int) -> dict:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO pack_settings (guild_id, next_order_number) VALUES (?, 1)",
        (guild_id,),
    )
    await bot.db.db.commit()
    row = await bot.db.fetchone(
        "SELECT * FROM pack_settings WHERE guild_id = ?", (guild_id,)
    )
    return dict(row) if row else {"guild_id": guild_id, "support_role_id": None}


async def _update_pack_settings(bot: "ShopBot", guild_id: int, **fields) -> None:
    if not fields:
        return
    await _get_pack_settings(bot, guild_id)  # sicherstellen, dass die Zeile existiert
    cols = ", ".join(f"{k} = ?" for k in fields)
    await bot.db.db.execute(
        f"UPDATE pack_settings SET {cols} WHERE guild_id = ?",
        (*fields.values(), guild_id),
    )
    await bot.db.db.commit()


async def _resolve_support_role(
    bot: "ShopBot", guild: discord.Guild, pack_settings: Optional[dict] = None
) -> Optional[discord.Role]:
    """Eigene Custom-Pack/Sky-Support-Rolle, falls gesetzt (/custompack staff)
    - sonst Fallback auf die normale Shop-Staff-Rolle (/setup)."""
    if pack_settings is None:
        pack_settings = await _get_pack_settings(bot, guild.id)
    role_id = pack_settings.get("support_role_id")
    if role_id:
        role = guild.get_role(int(role_id))
        if role is not None:
            return role
    settings = await bot.db.ensure_guild(guild.id)
    staff_role_id = settings.get("staff_role_id")
    return guild.get_role(int(staff_role_id)) if staff_role_id else None


async def _is_pack_staff(bot: "ShopBot", interaction: discord.Interaction) -> bool:
    """Wie is_staff(), aber die eigene Custom-Pack/Sky-Support-Rolle zählt
    zusätzlich zur normalen Shop-Staff-Rolle."""
    user = interaction.user
    if isinstance(user, discord.Member) and user.guild_permissions.administrator:
        return True
    assert interaction.guild is not None
    pack_settings = await _get_pack_settings(bot, interaction.guild.id)
    role_id = pack_settings.get("support_role_id")
    if role_id and isinstance(user, discord.Member):
        if any(r.id == int(role_id) for r in user.roles):
            return True
    return await is_staff(bot, interaction)


async def _create_order(
    bot: "ShopBot", guild_id: int, user_id: int, qty: int, description: str, price: Optional[float],
    *, kind: str, ingame_price: float,
) -> tuple[int, int]:
    order_number = await _next_order_number(bot, guild_id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO pack_orders (guild_id, order_number, user_id, qty, description, price, kind, ingame_price, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (guild_id, order_number, user_id, qty, description, price, kind, ingame_price),
    )
    await bot.db.db.commit()
    return int(cur.lastrowid), order_number  # type: ignore[arg-type]


async def _get_order_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM pack_orders WHERE ticket_channel_id = ?", (channel_id,)
    )
    return dict(row) if row else None


async def _set_ticket_channel(bot: "ShopBot", order_id: int, channel_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE pack_orders SET ticket_channel_id = ? WHERE id = ?", (channel_id, order_id)
    )
    await bot.db.db.commit()


async def _mark_confirmed(bot: "ShopBot", order_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        """
        UPDATE pack_orders SET status = 'confirmed', created_by = ?, confirmed_at = datetime('now')
        WHERE id = ?
        """,
        (staff_id, order_id),
    )
    await bot.db.db.commit()


async def _mark_rejected(bot: "ShopBot", order_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        """
        UPDATE pack_orders SET status = 'rejected', created_by = ?, confirmed_at = datetime('now')
        WHERE id = ?
        """,
        (staff_id, order_id),
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


# ── UI: Panel, Anfrage-Modal, Liefer-Modal, Ticket-Buttons ──────────────

def _panel_embed() -> discord.Embed:
    return base_embed(
        "📦 Custom Texturepack / 🌌 Custom Sky anfragen",
        "**Custom Texturepack** — individuelle Texturen für dein Pack, "
        "Preis nach Anzahl:\n"
        f"{brackets_overview()}\n\n"
        f"**Custom Sky** — dein eigener Himmel, fester Preis **{format_price(config.CUSTOM_SKY_PRICE)}**.\n\n"
        "Beides zusätzlich auch für einen festen Ingame-Betrag kaufbar "
        f"(Pack: **{format_price(config.CUSTOM_PACK_INGAME_PRICE)}**, "
        f"Sky: **{format_price(config.CUSTOM_SKY_INGAME_PRICE)}**) — steht im Ticket.\n\n"
        "Klicke einen der Buttons — danach wird ein privates Ticket erstellt.",
    )


class CustomPackRequestModal(discord.ui.Modal, title="Custom Pack anfragen"):
    qty = discord.ui.TextInput(
        label="Anzahl Texturen",
        placeholder="z. B. 20",
        max_length=4,
        required=True,
    )
    description = discord.ui.TextInput(
        label="Was soll angepasst werden?",
        style=discord.TextStyle.paragraph,
        placeholder="z. B. Schwert, Rüstung, Items ... (optional)",
        max_length=1000,
        required=False,
    )

    def __init__(self, bot: "ShopBot") -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        raw = str(self.qty.value).strip()
        if not raw.isdigit() or int(raw) <= 0:
            await interaction.response.send_message(
                embed=error_embed("Ungültige Anzahl", "Bitte eine positive Zahl eingeben."),
                ephemeral=True,
            )
            return
        qty = int(raw)
        desc = str(self.description.value).strip()
        price = price_for_qty(qty)
        await _create_pack_ticket_channel(
            self.bot, interaction, qty=qty, description=desc, price=price,
            kind="texturepack", ingame_price=config.CUSTOM_PACK_INGAME_PRICE,
        )


class CustomSkyRequestModal(discord.ui.Modal, title="Custom Sky anfragen"):
    description = discord.ui.TextInput(
        label="Wie soll der Himmel aussehen?",
        style=discord.TextStyle.paragraph,
        placeholder="z. B. Farben, Sterne, Wolken, Referenzbild-Link ...",
        max_length=1000,
        required=True,
    )

    def __init__(self, bot: "ShopBot") -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        desc = str(self.description.value).strip()
        await _create_pack_ticket_channel(
            self.bot, interaction, qty=1, description=desc, price=config.CUSTOM_SKY_PRICE,
            kind="sky", ingame_price=config.CUSTOM_SKY_INGAME_PRICE,
        )


async def _create_pack_ticket_channel(
    bot: "ShopBot",
    interaction: discord.Interaction,
    *,
    qty: int,
    description: str,
    price: Optional[float],
    kind: str,
    ingame_price: float,
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
    staff_role = await _resolve_support_role(bot, guild)
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

    order_id, order_number = await _create_order(
        bot, guild.id, interaction.user.id, qty, description, price,
        kind=kind, ingame_price=ingame_price,
    )

    is_sky = kind == "sky"
    prefix = "sky" if is_sky else "pack"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    name = f"{prefix}-{order_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites,
            reason=f"Custom-{'Sky' if is_sky else 'Pack'}-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return

    await _set_ticket_channel(bot, order_id, channel.id)

    price_txt = format_price(price) if price is not None else "Preis auf Anfrage — Staff nennt dir den Betrag"
    money_line = (
        f"**Zahlung 1 — Echtgeld ({price_txt}):**\n"
        f"Zahlung an **{payee_name(settings)}**:\n{payee_details_text(settings) or '_Keine Details hinterlegt_'}"
    )
    ingame_line = (
        f"**Zahlung 2 — Ingame (fester Preis {format_price(ingame_price)}):**\n"
        "_Wird automatisch erkannt — Staff bestätigt danach trotzdem manuell._\n"
        f"```\n{config.mc_pay_command(ingame_price)}\n```"
    )
    title = f"🌌 Sky-Ticket #{order_number}" if is_sky else f"📦 Pack-Ticket #{order_number}"
    qty_line = "" if is_sky else f"Anzahl Texturen: **{qty}**\n"
    embed = base_embed(
        title,
        f"Käufer: {interaction.user.mention}\n"
        f"{qty_line}"
        f"Preis: **{price_txt}**\n"
        + (f"Beschreibung: {description}\n" if description else "")
        + f"\n**{config.PAYMENT_NOTICE}**\n"
        f"{money_line}\n\n"
        "Sobald die Zahlung eingegangen ist, klickt Staff **✅ Bestätigen** und "
        "lädt die fertige Datei hoch — der Kunde bekommt sie automatisch per DM.\n\n"
        f"{ingame_line}",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(
        content=f"{interaction.user.mention} {mention}",
        embed=embed,
        view=CustomPackTicketView(bot),
    )
    await interaction.followup.send(
        embed=success_embed("Ticket erstellt", f"Dein Ticket: {channel.mention}"), ephemeral=True,
    )


class PackDeliverModal(discord.ui.Modal, title="Pack liefern"):
    """Natives Datei-Upload-Feld (discord.py 2.7 Components-V2) - Staff lädt
    das fertige Pack direkt hier hoch, kein Umweg über eine Chat-Nachricht."""

    def __init__(self, bot: "ShopBot", order: dict) -> None:
        super().__init__()
        self.bot = bot
        self.order = order
        is_sky = order.get("kind") == "sky"
        self.file_upload = discord.ui.FileUpload(
            custom_id="pack_file", max_values=1, min_values=1, required=True,
        )
        self.add_item(
            discord.ui.Label(
                text="Fertige Sky-Datei" if is_sky else "Fertiges Pack",
                description="ZIP/RAR mit dem fertigen Ergebnis · maximal 25 MB",
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

        await _mark_confirmed(self.bot, int(self.order["id"]), interaction.user.id)

        is_sky = self.order.get("kind") == "sky"
        product_label = "Custom Sky" if is_sky else "Custom Texturepack"
        tier_label = "Einzelanfertigung" if is_sky else f"{self.order['qty']} Texturen"

        buyer = await _resolve_member(interaction.guild, self.order.get("user_id"))
        dm_ok = True
        if buyer is not None:
            try:
                file_to_send = await att.to_file()
                await buyer.send(
                    embed=success_embed(
                        f"{'🌌' if is_sky else '📦'} Dein {product_label} (#{self.order['order_number']})",
                        (f"Anzahl Texturen: **{self.order['qty']}**\n" if not is_sky else "")
                        + f"Viel Spaß mit deinem {product_label}!",
                    ),
                    file=file_to_send,
                )
            except discord.HTTPException:
                dm_ok = False
            else:
                from utils import tweak_vouch

                await tweak_vouch.request_vouch(
                    self.bot, interaction.guild, buyer,
                    product=product_label,
                    tier_label=tier_label,
                )

        body = f"Bestätigt und geliefert von {interaction.user.mention}."
        if not dm_ok:
            body += "\n⚠️ DM an Käufer fehlgeschlagen (DMs geschlossen) — Datei oben manuell weitergeben."
        await interaction.followup.send(
            embed=success_embed("Geliefert", body),
            file=await att.to_file(),
        )


class CustomPackTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Bestätigen & liefern",
        style=discord.ButtonStyle.success,
        custom_id="packorder:confirm",
        emoji="✅",
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await _is_pack_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_order_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Pack-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.send_modal(PackDeliverModal(self.bot, row))

    @discord.ui.button(
        label="Ablehnen",
        style=discord.ButtonStyle.danger,
        custom_id="packorder:reject",
        emoji="❌",
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await _is_pack_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_order_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Pack-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_rejected(self.bot, int(row["id"]), interaction.user.id)

        buyer = await _resolve_member(interaction.guild, row.get("user_id"))
        if buyer is not None:
            try:
                await buyer.send(
                    embed=warn_embed(
                        "Bestellung abgelehnt",
                        "Deine Custom-Pack-Bestellung wurde abgelehnt (z.B. keine Zahlung "
                        "erkannt). Melde dich im Ticket für Rückfragen.",
                    )
                )
            except discord.HTTPException:
                pass

        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        await interaction.followup.send(embed=warn_embed("Abgelehnt", f"Abgelehnt von {interaction.user.mention}."))

    @discord.ui.button(
        label="Schließen",
        style=discord.ButtonStyle.secondary,
        custom_id="packorder:close",
        emoji="🔒",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        row = await _get_order_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Pack-Ticket"), ephemeral=True)
            return
        staff = await _is_pack_staff(self.bot, interaction)
        is_owner = row.get("user_id") and interaction.user.id == int(row["user_id"])
        if not staff and not is_owner:
            await interaction.response.send_message(embed=error_embed("Keine Berechtigung"), ephemeral=True)
            return
        await interaction.response.defer()
        if row["status"] == "pending":
            await _mark_rejected(self.bot, int(row["id"]), interaction.user.id)
        await interaction.followup.send(
            embed=warn_embed(
                "Ticket wird geschlossen",
                f"Geschlossen von {interaction.user.mention}. Channel wird in 5 Sekunden gelöscht.",
            )
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Pack-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


class CustomPackPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Pack anfragen",
        style=discord.ButtonStyle.success,
        custom_id="custompack:request",
        emoji="📦",
    )
    async def request(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await interaction.response.send_modal(CustomPackRequestModal(self.bot))

    @discord.ui.button(
        label="Sky anfragen",
        style=discord.ButtonStyle.primary,
        custom_id="custompack:request_sky",
        emoji="🌌",
    )
    async def request_sky(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await interaction.response.send_modal(CustomSkyRequestModal(self.bot))


# ── Slash-Commands ───────────────────────────────────────────────────────

class CustomPackCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    custompack_group = app_commands.Group(
        name="custompack",
        description="Custom Pack/Sky verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @custompack_group.command(
        name="panel",
        description="Custom-Pack/Sky-Anfrage-Panel posten (Staff)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    async def panel(
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
        msg = await target.send(embed=_panel_embed(), view=CustomPackPanelView(self.bot))
        await interaction.followup.send(
            embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True,
        )

    @custompack_group.command(
        name="staff",
        description="Eigene Support-Rolle für Custom Pack/Sky setzen (Staff)",
    )
    @app_commands.describe(
        role="Eigene Support-Rolle für Custom-Pack/Sky-Tickets (sieht Tickets, darf bestätigen/ablehnen). Leer = unverändert.",
        clear="Eigene Support-Rolle entfernen (Fallback: normale Shop-Staff-Rolle)",
    )
    async def staff(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
        clear: bool = False,
    ) -> None:
        assert interaction.guild is not None
        if clear:
            await _update_pack_settings(self.bot, interaction.guild.id, support_role_id=None)
        elif role is not None:
            await _update_pack_settings(self.bot, interaction.guild.id, support_role_id=role.id)
        settings = await _get_pack_settings(self.bot, interaction.guild.id)
        role_id = settings.get("support_role_id")
        current = interaction.guild.get_role(int(role_id)) if role_id else None
        role_line = (
            f"Support-Rolle: {current.mention}"
            if current
            else "Support-Rolle: **nicht gesetzt** (Fallback: normale Shop-Staff-Rolle aus `/setup`)"
        )
        await interaction.response.send_message(
            embed=success_embed("Custom-Pack/Sky-Einstellungen", role_line), ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_table(bot)
    await bot.add_cog(CustomPackCog(bot))
