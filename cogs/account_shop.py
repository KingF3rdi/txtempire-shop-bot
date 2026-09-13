"""
account_shop.py
================

Verkauf einzelner Minecraft-Accounts. Jedes Angebot hat einen Titel, einen
Preis und einen freien Info-Text (Staff schreibt ihn per Textfenster) — z.B.
Rang, Stats, Alter des Accounts. Ein Angebot ist ein Unikat: sobald es
verkauft ist, verschwindet es aus der Auswahl.

Eigene Support-Rolle + Einzelmitglieder (siehe utils/boost_support.py) —
getrennt von cogs/tier_boost.py (hat seit /tiersupport seine eigene) und
der generischen Ticket-Staff-Rolle.

Eigene Tabellen (accounts, account_settings, account_tickets) - keine
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


# ── DB Bootstrap & Helpers ───────────────────────────────────────────────

async def _ensure_column(bot: "ShopBot", table: str, column: str, ddl: str) -> None:
    try:
        await bot.db.db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        await bot.db.db.commit()
    except Exception as e:
        if "duplicate column" not in str(e).lower():
            raise


async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            info_text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'available',
            stock INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 99
        );
        CREATE TABLE IF NOT EXISTS account_settings (
            guild_id INTEGER PRIMARY KEY,
            next_ticket_number INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS account_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            account_id INTEGER,
            account_name TEXT NOT NULL,
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
    # Für Installationen, bei denen "accounts" schon vor "stock" existierte.
    await _ensure_column(bot, "accounts", "stock", "stock INTEGER NOT NULL DEFAULT 1")


async def _next_ticket_number(bot: "ShopBot", guild_id: int) -> int:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO account_settings (guild_id) VALUES (?)", (guild_id,)
    )
    row = await bot.db.fetchone(
        "SELECT next_ticket_number FROM account_settings WHERE guild_id = ?", (guild_id,)
    )
    n = int(row["next_ticket_number"]) if row else 1
    await bot.db.db.execute(
        "UPDATE account_settings SET next_ticket_number = ? WHERE guild_id = ?", (n + 1, guild_id),
    )
    await bot.db.db.commit()
    return n


async def list_available_accounts(bot: "ShopBot", guild_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        "SELECT * FROM accounts WHERE guild_id = ? AND status = 'available' AND stock > 0 ORDER BY sort_order ASC, name ASC",
        (guild_id,),
    )
    return [dict(r) for r in rows]


async def list_all_accounts(bot: "ShopBot", guild_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        "SELECT * FROM accounts WHERE guild_id = ? ORDER BY sort_order ASC, name ASC", (guild_id,),
    )
    return [dict(r) for r in rows]


async def get_account(bot: "ShopBot", account_id: int) -> Optional[dict]:
    row = await bot.db.fetchone("SELECT * FROM accounts WHERE id = ?", (account_id,))
    return dict(row) if row else None


async def set_stock(bot: "ShopBot", account_id: int, stock: int) -> None:
    stock = max(0, stock)
    await bot.db.db.execute(
        "UPDATE accounts SET stock = ?, status = ? WHERE id = ?",
        (stock, "available" if stock > 0 else "sold", account_id),
    )
    await bot.db.db.commit()


async def _consume_one_stock(bot: "ShopBot", account_id: int) -> None:
    """Zieht 1 vom Lagerbestand ab; bei 0 gilt der Account als ausverkauft."""
    account = await get_account(bot, account_id)
    if not account:
        return
    remaining = max(0, int(account["stock"]) - 1)
    await bot.db.db.execute(
        "UPDATE accounts SET stock = ?, status = ? WHERE id = ?",
        (remaining, "available" if remaining > 0 else "sold", account_id),
    )
    await bot.db.db.commit()


async def _get_ticket_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone("SELECT * FROM account_tickets WHERE ticket_channel_id = ?", (channel_id,))
    return dict(row) if row else None


async def _set_ticket_channel(bot: "ShopBot", ticket_id: int, channel_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE account_tickets SET ticket_channel_id = ? WHERE id = ?", (channel_id, ticket_id)
    )
    await bot.db.db.commit()


async def _mark_confirmed(bot: "ShopBot", ticket_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE account_tickets SET status = 'confirmed', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
        (staff_id, ticket_id),
    )
    await bot.db.db.commit()


async def _mark_rejected(bot: "ShopBot", ticket_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE account_tickets SET status = 'rejected', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
        (staff_id, ticket_id),
    )
    await bot.db.db.commit()


# ── UI: Panel, Auswahl, Ticket-Buttons ───────────────────────────────────

def _stock_suffix(account: dict) -> str:
    stock = int(account.get("stock") or 0)
    return f" · {stock}x auf Lager" if stock > 1 else ""


def _panel_embed(accounts: list[dict]) -> discord.Embed:
    body = "\n".join(
        f"👤 **{a['name']}** — {format_price(a['price'])}{_stock_suffix(a)}" for a in accounts
    ) or "_Aktuell keine Accounts im Angebot._"
    return base_embed(
        "👤 Account-Shop",
        f"{body}\n\nKlicke unten und wähle einen Account — danach wird ein privates Ticket erstellt.",
    )


class AccountSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", accounts: list[dict]) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(label=a["name"], value=str(a["id"]), description=format_price(a["price"])[:100])
            for a in accounts[:25]
        ]
        super().__init__(placeholder="Account auswählen ...", options=options, custom_id="account:select")

    async def callback(self, interaction: discord.Interaction) -> None:
        account = await get_account(self.bot, int(self.values[0]))
        if not account or account["status"] != "available" or int(account.get("stock") or 0) <= 0:
            await interaction.response.send_message(embed=error_embed("Nicht mehr verfügbar"), ephemeral=True)
            return
        await _create_account_ticket(self.bot, interaction, account=account)


async def _open_account_picker(bot: "ShopBot", interaction: discord.Interaction) -> None:
    assert interaction.guild is not None
    accounts = await list_available_accounts(bot, interaction.guild.id)
    if not accounts:
        await interaction.response.send_message(embed=warn_embed("Aktuell keine Accounts im Angebot."), ephemeral=True)
        return
    view = discord.ui.View(timeout=180)
    view.add_item(AccountSelect(bot, accounts))
    await interaction.response.send_message(content="Welchen Account möchtest du kaufen?", view=view, ephemeral=True)


async def _create_account_ticket(bot: "ShopBot", interaction: discord.Interaction, *, account: dict) -> None:
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
        INSERT INTO account_tickets (guild_id, ticket_number, user_id, account_id, account_name, price, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """,
        (guild.id, ticket_number, interaction.user.id, account["id"], account["name"], account["price"]),
    )
    await bot.db.db.commit()
    ticket_id = int(cur.lastrowid)  # type: ignore[arg-type]

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    name = f"account-{ticket_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites, reason=f"Account-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return

    await _set_ticket_channel(bot, ticket_id, channel.id)

    embed = base_embed(
        f"👤 Account-Ticket #{ticket_number}",
        f"Käufer: {interaction.user.mention}\n"
        f"Account: **{account['name']}**\n"
        f"Preis: **{format_price(account['price'])}**\n\n"
        f"**Info:**\n{account['info_text'] or '_Keine weiteren Infos_'}\n\n"
        f"**{config.PAYMENT_NOTICE}**\n"
        f"Zahlung an **{payee_name(settings)}**:\n{payee_details_text(settings) or '_Keine Details hinterlegt_'}\n\n"
        "Sobald die Zahlung eingegangen ist, klickt Staff **✅ Bestätigen** und übergibt die "
        "Zugangsdaten direkt hier im Ticket.",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(content=f"{interaction.user.mention} {mention}", embed=embed, view=AccountTicketView(bot))
    await interaction.followup.send(embed=success_embed("Ticket erstellt", f"Dein Ticket: {channel.mention}"), ephemeral=True)


class AccountTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.success, custom_id="accountticket:confirm", emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await boost_support.is_boost_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Support"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Account-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_confirmed(self.bot, int(row["id"]), interaction.user.id)
        if row.get("account_id"):
            await _consume_one_stock(self.bot, int(row["account_id"]))
        await interaction.followup.send(
            embed=success_embed(
                "Bestätigt",
                f"Von {interaction.user.mention} bestätigt. Bitte Zugangsdaten jetzt direkt hier im Ticket übergeben.",
            )
        )

    @discord.ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="accountticket:reject", emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await boost_support.is_boost_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Support"), ephemeral=True)
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Account-Ticket"), ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"), ephemeral=True,
            )
            return
        await interaction.response.defer()
        await _mark_rejected(self.bot, int(row["id"]), interaction.user.id)
        await interaction.followup.send(embed=warn_embed("Abgelehnt", f"Abgelehnt von {interaction.user.mention}."))

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.secondary, custom_id="accountticket:close", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        row = await _get_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein Account-Ticket"), ephemeral=True)
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
            await interaction.channel.delete(reason=f"Account-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


class AccountPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Account kaufen", style=discord.ButtonStyle.primary, custom_id="accountpanel:buy", emoji="👤")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await _open_account_picker(self.bot, interaction)


def _single_account_embed(account: dict) -> discord.Embed:
    stock = int(account.get("stock") or 0)
    stock_line = f"**Auf Lager:** {stock}\n" if stock > 1 else ""
    return base_embed(
        f"👤 {account['name']}",
        f"**Preis:** {format_price(account['price'])}\n{stock_line}\n{account['info_text'] or '_Keine weiteren Infos_'}",
    )


class AccountSinglePanelView(discord.ui.View):
    """Persistentes Panel: ein Button -> direkt Ticket für GENAU diesen Account."""

    def __init__(self, bot: "ShopBot", account_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.account_id = int(account_id)

        buy_btn = discord.ui.Button(
            label="Kaufen", style=discord.ButtonStyle.success,
            custom_id=f"account:buyone:{self.account_id}", emoji="💰",
        )
        buy_btn.callback = self._on_buy  # type: ignore[method-assign]
        self.add_item(buy_btn)

    async def _on_buy(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        account = await get_account(self.bot, self.account_id)
        if not account or account["status"] != "available" or int(account.get("stock") or 0) <= 0:
            await interaction.response.send_message(embed=error_embed("Nicht mehr verfügbar"), ephemeral=True)
            return
        await _create_account_ticket(self.bot, interaction, account=account)


def ensure_account_panel_view(bot: "ShopBot", account_id: int) -> None:
    registered: set[int] = getattr(bot, "_account_panel_registered", set())
    aid = int(account_id)
    if aid in registered:
        return
    bot.add_view(AccountSinglePanelView(bot, aid))
    registered.add(aid)
    bot._account_panel_registered = registered


async def register_all_account_panel_views(bot: "ShopBot") -> int:
    """Registriert Views für alle verfügbaren Accounts (Panel-Buttons bleiben nach Neustart klickbar)."""
    rows = await bot.db.fetchall("SELECT id FROM accounts WHERE status = 'available'")
    for row in rows:
        ensure_account_panel_view(bot, int(row["id"]))
    return len(rows)


class AccountInfoModal(discord.ui.Modal, title="Account-Info"):
    info = discord.ui.TextInput(
        label="Info-Text über den Account", style=discord.TextStyle.paragraph,
        placeholder="z. B. Rang, Stats, Alter, besondere Items ...", max_length=1800, required=True,
    )

    def __init__(self, bot: "ShopBot", name: str, price: float) -> None:
        super().__init__()
        self.bot = bot
        self.name = name
        self.price = price

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "INSERT INTO accounts (guild_id, name, price, info_text) VALUES (?, ?, ?, ?)",
            (interaction.guild.id, self.name, self.price, str(self.info.value).strip()),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Angelegt", f"**{self.name}** — {format_price(self.price)}"), ephemeral=True,
        )


# ── Slash-Commands ───────────────────────────────────────────────────────

class AccountShopCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    account_group = app_commands.Group(
        name="account", description="Account-Angebote verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @app_commands.command(name="accountpanel", description="Account-Shop-Panel posten (Staff)")
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def accountpanel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        accounts = await list_available_accounts(self.bot, interaction.guild.id)
        msg = await target.send(embed=_panel_embed(accounts), view=AccountPanelView(self.bot))
        await interaction.followup.send(embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True)

    @account_group.command(name="posten", description="Einen einzelnen Account als eigenen Post veröffentlichen")
    @app_commands.describe(name="Titel des Angebots", channel="Ziel-Channel (Standard: aktuell)")
    async def posten(
        self, interaction: discord.Interaction, name: str, channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        rows = await list_all_accounts(self.bot, interaction.guild.id)
        account = next((a for a in rows if a["name"].lower() == name.strip().lower()), None)
        if not account:
            await interaction.response.send_message(embed=error_embed("Nicht gefunden", f"**{name}** existiert nicht."), ephemeral=True)
            return
        if account["status"] != "available":
            await interaction.response.send_message(embed=error_embed("Bereits verkauft"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ensure_account_panel_view(self.bot, int(account["id"]))
        msg = await target.send(embed=_single_account_embed(account), view=AccountSinglePanelView(self.bot, int(account["id"])))
        await interaction.followup.send(
            embed=success_embed("Gepostet", f"**{account['name']}** in {target.mention}: {msg.jump_url}"), ephemeral=True,
        )

    @account_group.command(name="hinzufuegen", description="Neues Account-Angebot anlegen (öffnet Textfenster für die Info)")
    @app_commands.describe(name="Titel des Angebots", preis="Preis, z. B. 49.99")
    async def hinzufuegen(self, interaction: discord.Interaction, name: str, preis: str) -> None:
        try:
            price = parse_price(preis)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Preis", "Beispiele: `49.99`, `500k`."), ephemeral=True,
            )
            return
        await interaction.response.send_modal(AccountInfoModal(self.bot, name.strip(), price))

    @account_group.command(name="entfernen", description="Account-Angebot löschen")
    @app_commands.describe(name="Titel des Angebots")
    async def entfernen(self, interaction: discord.Interaction, name: str) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "DELETE FROM accounts WHERE guild_id = ? AND lower(name) = lower(?)",
            (interaction.guild.id, name.strip()),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(embed=success_embed("Entfernt", f"**{name}** wurde gelöscht."), ephemeral=True)

    @account_group.command(name="liste", description="Alle Account-Angebote anzeigen (auch verkaufte)")
    async def liste(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        rows = await list_all_accounts(self.bot, interaction.guild.id)
        body = "\n".join(
            f"`{a['id']}` **{a['name']}** — {format_price(a['price'])} · "
            f"{'✅ verfügbar' if a['status'] == 'available' else '❌ ausverkauft'} · Lager: {int(a.get('stock') or 0)}"
            for a in rows
        ) or "_Keine Angebote._"
        await interaction.response.send_message(embed=base_embed("Account-Angebote", body), ephemeral=True)

    @account_group.command(name="stock", description="Lagerbestand eines Accounts anzeigen oder setzen")
    @app_commands.describe(name="Titel des Angebots", menge="Neuer Lagerbestand (leer lassen, um nur anzuzeigen)")
    async def stock(self, interaction: discord.Interaction, name: str, menge: Optional[int] = None) -> None:
        assert interaction.guild is not None
        rows = await list_all_accounts(self.bot, interaction.guild.id)
        account = next((a for a in rows if a["name"].lower() == name.strip().lower()), None)
        if not account:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden", f"**{name}** existiert nicht."), ephemeral=True,
            )
            return
        if menge is None:
            await interaction.response.send_message(
                embed=base_embed(
                    f"Lagerbestand · {account['name']}",
                    f"**{int(account.get('stock') or 0)}** auf Lager "
                    f"({'✅ verfügbar' if account['status'] == 'available' else '❌ ausverkauft'}).",
                ),
                ephemeral=True,
            )
            return
        if menge < 0:
            await interaction.response.send_message(
                embed=error_embed("Ungültige Menge", "Menge darf nicht negativ sein."), ephemeral=True,
            )
            return
        await set_stock(self.bot, int(account["id"]), menge)
        if menge > 0:
            ensure_account_panel_view(self.bot, int(account["id"]))
        await interaction.response.send_message(
            embed=success_embed("Gespeichert", f"**{account['name']}** hat jetzt **{menge}** auf Lager."),
            ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(AccountShopCog(bot))
