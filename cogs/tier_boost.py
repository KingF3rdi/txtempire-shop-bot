"""
tier_boost.py
==============

Tier-Boosting (LT3, HT3, HT4). Kunde wählt eine Stufe, gibt seinen
Minecraft-Namen an, zahlt, Staff bestätigt und führt den Boost durch —
kein Datei-Versand, das ist eine Dienstleistung.

Eigene Support-Rolle + Einzelmitglieder (siehe utils/boost_support.py),
geteilt mit cogs/account_shop.py — NICHT die generische Ticket-Staff-Rolle.

Eigene Tabellen (boost_tier_prices, boost_settings, boost_tickets) - keine
Änderung an db/database.py nötig.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import boost_support
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

if TYPE_CHECKING:
    from bot import ShopBot

TIERS: tuple[str, ...] = ("LT3", "HT3", "HT4")
DEFAULT_PRICES: dict[str, float] = {"LT3": 4.99, "HT3": 9.99, "HT4": 14.99}


# ── DB Bootstrap & Helpers ───────────────────────────────────────────────

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS boost_tier_prices (
            guild_id INTEGER NOT NULL,
            tier_key TEXT NOT NULL,
            price REAL NOT NULL,
            PRIMARY KEY (guild_id, tier_key)
        );
        CREATE TABLE IF NOT EXISTS boost_settings (
            guild_id INTEGER PRIMARY KEY,
            next_ticket_number INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS boost_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            tier_key TEXT NOT NULL,
            price REAL NOT NULL,
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


async def get_tier_price(bot: "ShopBot", guild_id: int, tier_key: str) -> float:
    row = await bot.db.fetchone(
        "SELECT price FROM boost_tier_prices WHERE guild_id = ? AND tier_key = ?", (guild_id, tier_key),
    )
    if row:
        return float(row["price"])
    return DEFAULT_PRICES.get(tier_key, 0.0)


async def set_tier_price(bot: "ShopBot", guild_id: int, tier_key: str, price: float) -> None:
    await bot.db.db.execute(
        """
        INSERT INTO boost_tier_prices (guild_id, tier_key, price) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, tier_key) DO UPDATE SET price = excluded.price
        """,
        (guild_id, tier_key, price),
    )
    await bot.db.db.commit()


async def _next_ticket_number(bot: "ShopBot", guild_id: int) -> int:
    await bot.db.db.execute("INSERT OR IGNORE INTO boost_settings (guild_id) VALUES (?)", (guild_id,))
    row = await bot.db.fetchone("SELECT next_ticket_number FROM boost_settings WHERE guild_id = ?", (guild_id,))
    n = int(row["next_ticket_number"]) if row else 1
    await bot.db.db.execute("UPDATE boost_settings SET next_ticket_number = ? WHERE guild_id = ?", (n + 1, guild_id))
    await bot.db.db.commit()
    return n


async def _get_ticket_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone("SELECT * FROM boost_tickets WHERE ticket_channel_id = ?", (channel_id,))
    return dict(row) if row else None


async def _set_ticket_channel(bot: "ShopBot", ticket_id: int, channel_id: int) -> None:
    await bot.db.db.execute("UPDATE boost_tickets SET ticket_channel_id = ? WHERE id = ?", (channel_id, ticket_id))
    await bot.db.db.commit()


async def _mark_confirmed(bot: "ShopBot", ticket_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE boost_tickets SET status = 'confirmed', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
        (staff_id, ticket_id),
    )
    await bot.db.db.commit()


async def _mark_rejected(bot: "ShopBot", ticket_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE boost_tickets SET status = 'rejected', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
        (staff_id, ticket_id),
    )
    await bot.db.db.commit()


# ── UI: Panel, Auswahl, IGN-Modal, Ticket-Buttons ────────────────────────

async def _panel_embed(bot: "ShopBot", guild_id: int) -> discord.Embed:
    lines = [f"⚔️ **{t}** — {format_price(await get_tier_price(bot, guild_id, t))}" for t in TIERS]
    return base_embed(
        "⚔️ Tier-Boosting",
        "\n".join(lines) + "\n\nKlicke unten, wähle eine Stufe — danach wird ein privates Ticket erstellt.",
    )


class TierSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", prices: dict[str, float]) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(label=t, value=t, description=format_price(prices[t])) for t in TIERS
        ]
        super().__init__(placeholder="Stufe auswählen ...", options=options, custom_id="tierboost:select")

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TierIgnModal(self.bot, self.values[0]))


async def _open_tier_picker(bot: "ShopBot", interaction: discord.Interaction) -> None:
    assert interaction.guild is not None
    prices = {t: await get_tier_price(bot, interaction.guild.id, t) for t in TIERS}
    view = discord.ui.View(timeout=180)
    view.add_item(TierSelect(bot, prices))
    await interaction.response.send_message(content="Welche Stufe möchtest du buchen?", view=view, ephemeral=True)


class TierIgnModal(discord.ui.Modal, title="Boost anfragen"):
    ign = discord.ui.TextInput(label="Dein Minecraft-Name", max_length=16, min_length=3, required=True)

    def __init__(self, bot: "ShopBot", tier_key: str) -> None:
        super().__init__()
        self.bot = bot
        self.tier_key = tier_key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        price = await get_tier_price(self.bot, interaction.guild.id, self.tier_key)
        await _create_boost_ticket(self.bot, interaction, tier_key=self.tier_key, price=price, ign=str(self.ign.value).strip())


async def _create_boost_ticket(bot: "ShopBot", interaction: discord.Interaction, *, tier_key: str, price: float, ign: str) -> None:
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    settings = await bot.db.ensure_guild(guild.id)
    category_id = settings.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None
    staff_role = await boost_support.boost_staff_role(bot, guild)
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
    overwrites: dict = {guild.default_role: discord.PermissionOverwrite(view_channel=False), me: bot_perms}
    if isinstance(interaction.user, discord.Member):
        overwrites[interaction.user] = buyer_perms
    if staff_role:
        overwrites[staff_role] = staff_perms
    for uid in await boost_support.get_extra_staff_ids(bot, guild.id):
        member = guild.get_member(uid)
        if member is not None:
            overwrites[member] = staff_perms

    ticket_number = await _next_ticket_number(bot, guild.id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO boost_tickets (guild_id, ticket_number, user_id, tier_key, price, ign, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """,
        (guild.id, ticket_number, interaction.user.id, tier_key, price, ign),
    )
    await bot.db.db.commit()
    ticket_id = int(cur.lastrowid)  # type: ignore[arg-type]

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    name = f"boost-{ticket_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites, reason=f"Boost-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return

    await _set_ticket_channel(bot, ticket_id, channel.id)

    embed = base_embed(
        f"⚔️ Boost-Ticket #{ticket_number}",
        f"Käufer: {interaction.user.mention}\n"
        f"Stufe: **{tier_key}**\n"
        f"Minecraft-Name: **{ign}**\n"
        f"Preis: **{format_price(price)}**\n\n"
        f"**{config.PAYMENT_NOTICE}**\n"
        f"Zahlung an **{payee_name(settings)}**:\n{payee_details_text(settings) or '_Keine Details hinterlegt_'}\n\n"
        "Sobald die Zahlung eingegangen ist, klickt Staff **✅ Bestätigen** und führt den Boost durch.",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(content=f"{interaction.user.mention} {mention}", embed=embed, view=BoostTicketView(bot))
    await interaction.followup.send(embed=success_embed("Ticket erstellt", f"Dein Ticket: {channel.mention}"), ephemeral=True)


class BoostTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.success, custom_id="boostticket:confirm", emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await boost_support.is_boost_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Support"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Boost-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_confirmed(self.bot, int(row["id"]), interaction.user.id)
        await interaction.followup.send(
            embed=success_embed("Bestätigt", f"Von {interaction.user.mention} bestätigt — Boost kann jetzt gestartet werden.")
        )

    @discord.ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="boostticket:reject", emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await boost_support.is_boost_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Support"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Boost-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_rejected(self.bot, int(row["id"]), interaction.user.id)
        await interaction.followup.send(embed=warn_embed("Abgelehnt", f"Abgelehnt von {interaction.user.mention}."))

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.secondary, custom_id="boostticket:close", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Boost-Ticket"), ephemeral=True)
            return
        staff = await boost_support.is_boost_staff(self.bot, interaction)
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
            await interaction.channel.delete(reason=f"Boost-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


class TierBoostPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Boost kaufen", style=discord.ButtonStyle.primary, custom_id="tierboostpanel:buy", emoji="⚔️")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await _open_tier_picker(self.bot, interaction)


# ── Slash-Commands ───────────────────────────────────────────────────────

class TierBoostCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    tier_group = app_commands.Group(
        name="tier", description="Tier-Boosting-Preise verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @app_commands.command(name="tierpanel", description="Tier-Boosting-Panel posten (Staff)")
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def tierpanel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        embed = await _panel_embed(self.bot, interaction.guild.id)
        msg = await target.send(embed=embed, view=TierBoostPanelView(self.bot))
        await interaction.followup.send(embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True)

    @tier_group.command(name="preis", description="Preis einer Boost-Stufe setzen")
    @app_commands.describe(stufe="LT3, HT3 oder HT4", preis="Neuer Preis, z. B. 9.99")
    @app_commands.choices(stufe=[app_commands.Choice(name=t, value=t) for t in TIERS])
    async def preis(self, interaction: discord.Interaction, stufe: app_commands.Choice[str], preis: str) -> None:
        assert interaction.guild is not None
        try:
            price = parse_price(preis)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Preis", "Beispiele: `9.99`, `500k`."), ephemeral=True,
            )
            return
        await set_tier_price(self.bot, interaction.guild.id, stufe.value, price)
        await interaction.response.send_message(
            embed=success_embed("Gespeichert", f"**{stufe.value}** kostet jetzt {format_price(price)}."), ephemeral=True,
        )

    @tier_group.command(name="liste", description="Aktuelle Preise anzeigen")
    async def liste(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        lines = [f"**{t}** — {format_price(await get_tier_price(self.bot, interaction.guild.id, t))}" for t in TIERS]
        await interaction.response.send_message(embed=base_embed("Tier-Preise", "\n".join(lines)), ephemeral=True)


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(TierBoostCog(bot))
