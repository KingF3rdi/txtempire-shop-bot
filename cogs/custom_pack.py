"""
custom_pack.py
================

Custom-Texturepack-Bestellungen (Kunde gibt eine Menge an, zahlt nach
Mengenstaffel, Staff liefert das fertige Pack als Datei direkt im Ticket).

Preisstaffel (Gesamtpreis pro Bestellung, nicht pro Textur):
    bis 10 Texturen   -> 0,20 €
    bis 20 Texturen   -> 0,30 €
    bis 30 Texturen   -> 0,40 €
    bis 50 Texturen   -> 0,50 €
    bis 70 Texturen   -> 1,00 €
    mehr als 70       -> Preis auf Anfrage (Staff nennt Betrag im Ticket)

Ablauf:
  - Kunde klickt "📦 Pack anfragen" -> Modal (Anzahl + Beschreibung) ->
    privates Ticket wird erstellt (fortlaufend nummeriert).
  - Staff bestätigt im Ticket ("✅ Bestätigen") -> öffnet ein natives
    Datei-Upload-Feld (discord.py 2.7 Components-V2, wie beim File-Scanner)
    zum Hochladen des fertigen Packs -> wird automatisch per DM an den
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
            status TEXT NOT NULL DEFAULT 'pending',
            ticket_channel_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pack_settings (
            guild_id INTEGER PRIMARY KEY,
            next_order_number INTEGER NOT NULL DEFAULT 1
        );
        """
    )
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


async def _create_order(
    bot: "ShopBot", guild_id: int, user_id: int, qty: int, description: str, price: Optional[float]
) -> tuple[int, int]:
    order_number = await _next_order_number(bot, guild_id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO pack_orders (guild_id, order_number, user_id, qty, description, price, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """,
        (guild_id, order_number, user_id, qty, description, price),
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
        "📦 Custom Texturepack anfragen",
        "Du willst individuelle Texturen für dein Pack? Gib die Anzahl an, "
        "wir liefern dir das fertige Pack direkt im Ticket.\n\n"
        f"{brackets_overview()}\n\n"
        "Klicke **Pack anfragen** — danach wird ein privates Ticket erstellt.",
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
        await _create_pack_ticket_channel(self.bot, interaction, qty=qty, description=desc, price=price)


async def _create_pack_ticket_channel(
    bot: "ShopBot",
    interaction: discord.Interaction,
    *,
    qty: int,
    description: str,
    price: Optional[float],
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

    order_id, order_number = await _create_order(bot, guild.id, interaction.user.id, qty, description, price)

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    name = f"pack-{order_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites,
            reason=f"Custom-Pack-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return

    await _set_ticket_channel(bot, order_id, channel.id)

    price_txt = format_price(price) if price is not None else "Preis auf Anfrage — Staff nennt dir den Betrag"
    embed = base_embed(
        f"📦 Pack-Ticket #{order_number}",
        f"Käufer: {interaction.user.mention}\n"
        f"Anzahl Texturen: **{qty}**\n"
        f"Preis: **{price_txt}**\n"
        + (f"Beschreibung: {description}\n" if description else "")
        + f"\n**{config.PAYMENT_NOTICE}**\n"
        f"Zahlung an **{payee_name(settings)}**:\n{payee_details_text(settings) or '_Keine Details hinterlegt_'}\n\n"
        "Sobald die Zahlung eingegangen ist, klickt Staff **✅ Bestätigen** und "
        "lädt das fertige Pack hoch — der Kunde bekommt es automatisch per DM.",
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
        self.file_upload = discord.ui.FileUpload(
            custom_id="pack_file", max_values=1, min_values=1, required=True,
        )
        self.add_item(
            discord.ui.Label(
                text="Fertiges Pack",
                description="ZIP/RAR mit den fertigen Texturen · maximal 25 MB",
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

        buyer = await _resolve_member(interaction.guild, self.order.get("user_id"))
        dm_ok = True
        if buyer is not None:
            try:
                file_to_send = await att.to_file()
                await buyer.send(
                    embed=success_embed(
                        f"📦 Dein Custom Pack (#{self.order['order_number']})",
                        f"Anzahl Texturen: **{self.order['qty']}**\nViel Spaß mit deinem Pack!",
                    ),
                    file=file_to_send,
                )
            except discord.HTTPException:
                dm_ok = False

        body = f"Bestätigt und geliefert von {interaction.user.mention}."
        if not dm_ok:
            body += "\n⚠️ DM an Käufer fehlgeschlagen (DMs geschlossen) — Datei oben manuell weitergeben."
        await interaction.followup.send(
            embed=success_embed("Pack geliefert", body),
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
        if not await is_staff(self.bot, interaction):
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
        if not await is_staff(self.bot, interaction):
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
        staff = await is_staff(self.bot, interaction)
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


# ── Slash-Commands ───────────────────────────────────────────────────────

class CustomPackCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="custompackpanel",
        description="Custom-Texturepack-Anfrage-Panel posten (Staff)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def custompackpanel(
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


async def setup(bot: "ShopBot") -> None:
    await _ensure_table(bot)
    await bot.add_cog(CustomPackCog(bot))
