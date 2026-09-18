"""
afk_service.py
================

Spawner-AFK-Service: Kunden geben ihre (Skeleton-)Spawner in unsere Obhut,
wir AFKen sie und zahlen einen Anteil vom Bone-Erlös aus.

Abrechnung (alles in config.py per .env änderbar):
  - config.AFK_BONES_PER_MIN  (3.6)  Bones pro Minute und Spawner
  - config.AFK_UPTIME_HOURS   (22)   garantierte Uptime pro Tag
  - config.AFK_CUSTOMER_PERCENT (75) Anteil des Kunden am Erlös
  -> ein Spawner = 3.6 * 60 * 22 = 4.752 Bones pro Tag
  - Bone-Order-Preis wird von Staff gesetzt (/spawner afkpreis).
  - Eigene Support-Rolle (/spawner afkrolle): sieht/pingt die AFK-Tickets und
    darf Spawner eintragen, auszahlen und schließen; sonst gilt die normale
    Shop-Staff-Rolle.

Ablauf:
  - Panel (/spawner afkpanel): Infotext + "Formular ausfüllen" -> Modal
    (IGN, Anzahl, Infos) -> privates Ticket = ein AFK-Konto.
  - Im Ticket trägt Staff Spawner mit ➕ ein / mit ➖ aus, sobald sie ingame
    übergeben bzw. zurückgegeben wurden (Kunden können das nicht selbst,
    sonst wären die Zahlen nicht überprüfbar).
  - 📊 Status (Ticket + Panel): Spawner im Service, Einnahmen pro Tag/Woche
    und der seit der letzten Auszahlung aufgelaufene Betrag.
  - 💸 Auszahlen (Staff): schreibt die aufgelaufenen Bones ab, postet die
    Auszahlungs-Nachricht samt /pay-Befehl und setzt die Aufzeichnung zurück.

Aufgelaufene Bones werden bei jeder Änderung der Spawner-Anzahl "eingefroren"
(accrued_bones + accrual_ts), damit Änderungen nie rückwirkend rechnen.

Die Slash-Commands (/spawner afkpanel, /spawner afkpreis) hängen in
cogs/spawner_shop.py an der bestehenden /spawner-Gruppe (Discord erlaubt
nur 100 Top-Level-Commands). Dieses Modul registriert nur Tabellen + Views.
Eigene Tabellen (afk_settings, afk_accounts, afk_payouts).
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Optional

import discord

import config
from utils.embeds import base_embed, error_embed, format_price, success_embed, warn_embed
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot

SECONDS_PER_DAY = 86400.0
MAX_QTY = 100_000


# ── Reine Rechenfunktionen ───────────────────────────────────────────────

def bones_per_spawner_per_day() -> float:
    return config.AFK_BONES_PER_MIN * 60.0 * config.AFK_UPTIME_HOURS


def pending_bones(account: dict, now: float) -> float:
    """Seit der letzten Auszahlung aufgelaufene Bones (eingefrorener Stand +
    aktuelle Spawner-Anzahl * Zeit seit der letzten Änderung)."""
    elapsed = max(0.0, now - float(account["accrual_ts"]))
    return float(account["accrued_bones"]) + int(account["spawner_count"]) * bones_per_spawner_per_day() * elapsed / SECONDS_PER_DAY


def split_revenue(bones: float, bone_price: float) -> tuple[float, float, float]:
    """(Gesamt-Erlös, Anteil Kunde, Anteil Shop)."""
    revenue = bones * bone_price
    customer = revenue * config.AFK_CUSTOMER_PERCENT / 100.0
    return revenue, customer, revenue - customer


def _fmt_int(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", ".")


# ── DB Bootstrap & Helpers (eigene Tabellen, kein Eingriff in database.py) ──

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS afk_settings (
            guild_id INTEGER PRIMARY KEY,
            bone_price REAL,
            panel_channel_id INTEGER,
            panel_message_id INTEGER,
            next_ticket_number INTEGER NOT NULL DEFAULT 1,
            support_role_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS afk_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            ign TEXT NOT NULL,
            requested_qty INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            spawner_count INTEGER NOT NULL DEFAULT 0,
            accrued_bones REAL NOT NULL DEFAULT 0,
            accrual_ts REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            ticket_channel_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS afk_payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            bones REAL NOT NULL,
            bone_price REAL NOT NULL,
            revenue REAL NOT NULL,
            customer_percent REAL NOT NULL,
            customer_amount REAL NOT NULL,
            staff_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    try:  # bestehende Installationen (idempotent)
        await bot.db.db.execute("ALTER TABLE afk_settings ADD COLUMN support_role_id INTEGER")
    except Exception:
        pass
    await bot.db.db.commit()


async def _get_settings(bot: "ShopBot", guild_id: int) -> dict:
    await bot.db.db.execute("INSERT OR IGNORE INTO afk_settings (guild_id) VALUES (?)", (guild_id,))
    await bot.db.db.commit()
    row = await bot.db.fetchone("SELECT * FROM afk_settings WHERE guild_id = ?", (guild_id,))
    return dict(row) if row else {"guild_id": guild_id, "bone_price": None}


async def _update_settings(bot: "ShopBot", guild_id: int, **fields) -> None:
    await _get_settings(bot, guild_id)
    cols = ", ".join(f"{k} = ?" for k in fields)
    await bot.db.db.execute(f"UPDATE afk_settings SET {cols} WHERE guild_id = ?", (*fields.values(), guild_id))
    await bot.db.db.commit()


async def get_bone_price(bot: "ShopBot", guild_id: int) -> Optional[float]:
    value = (await _get_settings(bot, guild_id)).get("bone_price")
    return float(value) if value else None


async def set_support_role(bot: "ShopBot", guild_id: int, role_id: Optional[int]) -> None:
    await _update_settings(bot, guild_id, support_role_id=role_id)


async def get_support_role_id(bot: "ShopBot", guild_id: int) -> Optional[int]:
    value = (await _get_settings(bot, guild_id)).get("support_role_id")
    return int(value) if value else None


async def _resolve_support_role(bot: "ShopBot", guild: discord.Guild) -> Optional[discord.Role]:
    """Eigene AFK-Support-Rolle, falls gesetzt — sonst die normale Shop-Staff-Rolle."""
    role_id = await get_support_role_id(bot, guild.id)
    if role_id:
        role = guild.get_role(role_id)
        if role is not None:
            return role
    settings = await bot.db.ensure_guild(guild.id)
    staff_role_id = settings.get("staff_role_id")
    return guild.get_role(int(staff_role_id)) if staff_role_id else None


async def _is_afk_staff(bot: "ShopBot", interaction: discord.Interaction) -> bool:
    """Wie is_staff(), aber die eigene AFK-Support-Rolle zählt zusätzlich."""
    user = interaction.user
    if isinstance(user, discord.Member) and user.guild_permissions.administrator:
        return True
    if isinstance(user, discord.Member) and interaction.guild is not None:
        role_id = await get_support_role_id(bot, interaction.guild.id)
        if role_id and any(r.id == role_id for r in user.roles):
            return True
    return await is_staff(bot, interaction)


async def _next_ticket_number(bot: "ShopBot", guild_id: int) -> int:
    settings = await _get_settings(bot, guild_id)
    n = int(settings.get("next_ticket_number") or 1)
    await _update_settings(bot, guild_id, next_ticket_number=n + 1)
    return n


async def _get_account(bot: "ShopBot", account_id: int) -> Optional[dict]:
    row = await bot.db.fetchone("SELECT * FROM afk_accounts WHERE id = ?", (account_id,))
    return dict(row) if row else None


async def _get_account_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM afk_accounts WHERE ticket_channel_id = ? AND status = 'open'", (channel_id,)
    )
    return dict(row) if row else None


async def _list_open_accounts(bot: "ShopBot", guild_id: int, user_id: Optional[int] = None) -> list[dict]:
    if user_id is None:
        rows = await bot.db.fetchall(
            "SELECT * FROM afk_accounts WHERE guild_id = ? AND status = 'open' ORDER BY id ASC", (guild_id,)
        )
    else:
        rows = await bot.db.fetchall(
            "SELECT * FROM afk_accounts WHERE guild_id = ? AND user_id = ? AND status = 'open' ORDER BY id ASC",
            (guild_id, user_id),
        )
    return [dict(r) for r in rows]


async def _apply_delta(bot: "ShopBot", account: dict, delta: int) -> dict:
    """Ändert die Spawner-Anzahl und friert dabei den bisher aufgelaufenen Stand ein."""
    now = time.time()
    pending = pending_bones(account, now)
    new_count = int(account["spawner_count"]) + delta
    await bot.db.db.execute(
        "UPDATE afk_accounts SET spawner_count = ?, accrued_bones = ?, accrual_ts = ? WHERE id = ?",
        (new_count, pending, now, account["id"]),
    )
    await bot.db.db.commit()
    return await _get_account(bot, int(account["id"])) or account


# ── Embeds ───────────────────────────────────────────────────────────────

def _panel_embed(bone_price: Optional[float]) -> discord.Embed:
    pct = config.AFK_CUSTOMER_PERCENT
    uptime = config.AFK_UPTIME_HOURS
    price_txt = f": **{format_price(bone_price)}** pro Bone" if bone_price else ""
    return base_embed(
        "Spawner AFK Service",
        f"Wir, die Owner von **TxtEmpire** (Ferdi & Team), AFKn deine Spawner für dich. "
        f"Du erhältst **{pct:g}% vom Gewinn** bei einer garantierten Auslastung von **{uptime:g}h pro Tag**.\n\n"
        "**Wie wird der Gewinn berechnet?**\n"
        "Wir nutzen keinen externen Rechner, sondern den aktuellen Bone-Order-Preis "
        f"(aktueller Bone Order Preis{price_txt}). Pro Skeleton-Spawner entstehen **{config.AFK_BONES_PER_MIN:g} Bones pro Minute**, "
        f"gerechnet auf **{uptime:g}h Uptime** pro Tag — das sind **{_fmt_int(bones_per_spawner_per_day())} Bones "
        f"pro Spawner und Tag**. Bones × Bone-Preis = Erlös, davon bekommst du {pct:g}%.\n\n"
        "**Wie sicher sind die Spawner?**\n"
        "Nur wir (Ferdi und das TxtEmpire-Team) haben Zugriff auf die Spawner. Jedoch können wir nicht zu "
        "100% garantieren, dass die Spawner sicher sind, wodurch ein geringes Risiko auf Spawnerverlust "
        "bestehen bleibt.\n\n"
        "**Wie funktioniert die TxtEmpire Spawner-Versicherung?**\n"
        "Die Versicherung ist ein optionales zusätzliches Angebot von uns, ohne Gebühren. Du legst einen "
        "kleinen Teil deines Gewinns zur Seite, wir legen denselben Betrag nochmal oben drauf (bis 5%). "
        "Aus 1000$ von dir werden so 2000$ Schutz.\n\n"
        "Das Geld wird jeden Tag ausgezahlt, optimalerweise zur gleichen Uhrzeit.\n\n"
        "**Wie viele Spawner muss ich haben?**\n"
        "Wir bevorzugen Anfragen mit größeren Mengen an Spawnern, z.B. 100+, können aber auch vereinzelt "
        "kleinere Mengen annehmen.",
    )


def _status_embed(account: dict, bone_price: Optional[float]) -> discord.Embed:
    now = time.time()
    count = int(account["spawner_count"])
    pct = config.AFK_CUSTOMER_PERCENT
    bones_day = count * bones_per_spawner_per_day()
    pending = pending_bones(account, now)

    embed = base_embed(
        f"📊 AFK-Status — Ticket #{account['ticket_number']}",
        f"Ingame-Name: **{account['ign']}**\n"
        f"Spawner im Service: **{count}**"
        + (f" _(angefragt: {account['requested_qty']})_" if not count and account.get("requested_qty") else ""),
    )
    if not bone_price:
        embed.add_field(
            name="Bone-Preis",
            value="⚠️ Noch nicht festgelegt — Staff setzt ihn mit `/spawner afkpreis`.",
            inline=False,
        )
        embed.add_field(name="Bones pro Tag", value=_fmt_int(bones_day), inline=True)
        embed.add_field(name="Aufgelaufene Bones", value=_fmt_int(pending), inline=True)
        return embed

    rev_day, cust_day, _ = split_revenue(bones_day, bone_price)
    rev_pending, cust_pending, _ = split_revenue(pending, bone_price)
    embed.add_field(name="Bone-Preis", value=format_price(bone_price), inline=True)
    embed.add_field(name="Bones pro Tag", value=_fmt_int(bones_day), inline=True)
    embed.add_field(name="Erlös pro Tag", value=format_price(rev_day), inline=True)
    embed.add_field(name=f"Dein Anteil ({pct:g}%) pro Tag", value=f"**{format_price(cust_day)}**", inline=True)
    embed.add_field(name="… pro Woche", value=f"**{format_price(cust_day * 7)}**", inline=True)
    embed.add_field(name="​", value="​", inline=True)
    since = int(float(account["accrual_ts"])) if account.get("accrual_ts") else None
    embed.add_field(
        name="Aufgelaufen seit der letzten Auszahlung",
        value=(
            f"{_fmt_int(pending)} Bones → Erlös {format_price(rev_pending)}\n"
            f"**Dein Anteil: {format_price(cust_pending)}**"
            + (f"\n_letzte Änderung <t:{since}:R>_" if since else "")
        ),
        inline=False,
    )
    return embed


async def _overview_embed(bot: "ShopBot", guild: discord.Guild) -> discord.Embed:
    """Staff-Gesamtübersicht über alle offenen AFK-Konten."""
    accounts = await _list_open_accounts(bot, guild.id)
    bone_price = await get_bone_price(bot, guild.id)
    now = time.time()
    total_spawners = sum(int(a["spawner_count"]) for a in accounts)
    bones_day = total_spawners * bones_per_spawner_per_day()
    body = [
        f"Offene Konten: **{len(accounts)}** · Spawner insgesamt: **{total_spawners}**",
        f"Bones pro Tag: **{_fmt_int(bones_day)}**",
    ]
    if bone_price:
        rev, cust, shop = split_revenue(bones_day, bone_price)
        body.append(
            f"Erlös pro Tag: **{format_price(rev)}** (Kunden {config.AFK_CUSTOMER_PERCENT:g}%: "
            f"**{format_price(cust)}** · Shop: **{format_price(shop)}**)"
        )
    else:
        body.append("⚠️ Bone-Preis noch nicht gesetzt (`/spawner afkpreis`).")
    lines = []
    for a in accounts[:15]:
        pend = pending_bones(a, now)
        money = f" · offen {format_price(split_revenue(pend, bone_price)[1])}" if bone_price else ""
        lines.append(f"`#{a['ticket_number']}` <@{a['user_id']}> (`{a['ign']}`) — **{a['spawner_count']}** Spawner{money}")
    if lines:
        body.append("\n" + "\n".join(lines))
    if len(accounts) > 15:
        body.append(f"… und {len(accounts) - 15} weitere")
    return base_embed("📊 AFK-Service — Übersicht (Staff)", "\n".join(body))


# ── Öffentliche Funktionen (von cogs/spawner_shop.py genutzt) ────────────

async def post_afk_panel(bot: "ShopBot", channel: discord.TextChannel) -> discord.Message:
    bone_price = await get_bone_price(bot, channel.guild.id)
    msg = await channel.send(embed=_panel_embed(bone_price), view=AfkPanelView(bot))
    await _update_settings(bot, channel.guild.id, panel_channel_id=channel.id, panel_message_id=msg.id)
    return msg


async def _refresh_panel(bot: "ShopBot", guild: discord.Guild) -> None:
    settings = await _get_settings(bot, guild.id)
    channel_id, message_id = settings.get("panel_channel_id"), settings.get("panel_message_id")
    if not channel_id or not message_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        msg = await channel.fetch_message(int(message_id))
        await msg.edit(embed=_panel_embed(settings.get("bone_price")))
    except discord.HTTPException:
        pass


async def set_bone_price(bot: "ShopBot", guild: discord.Guild, price: float) -> str:
    await _update_settings(bot, guild.id, bone_price=price)
    await _refresh_panel(bot, guild)
    revenue, customer, _ = split_revenue(bones_per_spawner_per_day(), price)
    return (
        f"Bone-Preis: **{format_price(price)}**\n"
        f"1 Spawner = **{_fmt_int(bones_per_spawner_per_day())} Bones/Tag** → Erlös **{format_price(revenue)}**/Tag, "
        f"Kunde ({config.AFK_CUSTOMER_PERCENT:g}%) **{format_price(customer)}**/Tag."
    )


# ── UI: Formular, Ticket, Auszahlung ─────────────────────────────────────

class AfkFormModal(discord.ui.Modal, title="Spawner AFK Service"):
    ign = discord.ui.TextInput(
        label="Minecraft-Name (IGN)", placeholder="Dein Ingame-Name", max_length=32, required=True,
    )
    qty = discord.ui.TextInput(
        label="Anzahl Skeleton-Spawner", placeholder="z.B. 16", max_length=6, required=True,
    )
    note = discord.ui.TextInput(
        label="Weitere Infos", style=discord.TextStyle.paragraph,
        placeholder="Optional: Standort, Zeitpunkt, Besonderheiten", max_length=800, required=False,
    )

    def __init__(self, bot: "ShopBot") -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.qty.value).strip()
        if not raw.isdigit() or not (1 <= int(raw) <= MAX_QTY):
            await interaction.response.send_message(
                embed=error_embed("Ungültige Anzahl", f"Bitte eine Zahl zwischen 1 und {MAX_QTY} eingeben."),
                ephemeral=True,
            )
            return
        await _create_afk_ticket(
            self.bot, interaction, ign=str(self.ign.value).strip(), qty=int(raw), note=str(self.note.value).strip(),
        )


async def _create_afk_ticket(
    bot: "ShopBot", interaction: discord.Interaction, *, ign: str, qty: int, note: str,
) -> None:
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    for existing in await _list_open_accounts(bot, guild.id, interaction.user.id):
        channel = guild.get_channel(int(existing["ticket_channel_id"] or 0))
        if channel is not None:
            await interaction.followup.send(
                embed=warn_embed("Du hast schon ein AFK-Ticket", f"Bitte melde dich dort: {channel.mention}"),
                ephemeral=True,
            )
            return

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

    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True, attach_files=True,
            read_message_history=True, manage_channels=True, manage_messages=True,
        ),
    }
    if isinstance(interaction.user, discord.Member):
        overwrites[interaction.user] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True,
        )
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True, embed_links=True,
            read_message_history=True, manage_messages=True,
        )

    ticket_number = await _next_ticket_number(bot, guild.id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO afk_accounts (guild_id, ticket_number, user_id, ign, requested_qty, note, accrual_ts, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (guild.id, ticket_number, interaction.user.id, ign, qty, note, time.time()),
    )
    await bot.db.db.commit()
    account_id = int(cur.lastrowid)  # type: ignore[arg-type]

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    try:
        channel = await guild.create_text_channel(
            name=f"afk-{ticket_number:04d}-{safe}"[:100], category=category, overwrites=overwrites,
            reason=f"AFK-Service-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await bot.db.db.execute("DELETE FROM afk_accounts WHERE id = ?", (account_id,))
        await bot.db.db.commit()
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return
    await bot.db.db.execute("UPDATE afk_accounts SET ticket_channel_id = ? WHERE id = ?", (channel.id, account_id))
    await bot.db.db.commit()

    bone_price = await get_bone_price(bot, guild.id)
    price_txt = format_price(bone_price) if bone_price else "wird von Staff festgelegt"
    embed = base_embed(
        f"🤖 Spawner AFK Service #{ticket_number}",
        f"Hallo {interaction.user.mention}, hier ist dein AFK-Service-Ticket.\n\n"
        f"Ingame-Name: **{ign}**\n"
        f"Angefragte Spawner: **{qty}** Skeleton-Spawner\n"
        + (f"Infos: {note}\n" if note else "")
        + f"\n**Konditionen:** {config.AFK_BONES_PER_MIN:g} Bones/Min pro Spawner · {config.AFK_UPTIME_HOURS:g}h/Tag "
        f"= {_fmt_int(bones_per_spawner_per_day())} Bones pro Spawner und Tag · Bone-Preis: {price_txt} · "
        f"Dein Anteil: **{config.AFK_CUSTOMER_PERCENT:g}%**\n\n"
        "Staff meldet sich hier, um die Übergabe der Spawner ingame abzustimmen. Sobald sie geprüft sind, "
        "trägt Staff sie mit **➕ Spawner hinzufügen** ein — ab dann läuft die Abrechnung. Deinen aktuellen "
        "Stand siehst du jederzeit mit **📊 Status**.",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(content=f"{interaction.user.mention} {mention}", embed=embed, view=AfkTicketView(bot))
    await interaction.followup.send(
        embed=success_embed("Ticket erstellt", f"Dein Ticket: {channel.mention}"), ephemeral=True,
    )


class AfkAdjustModal(discord.ui.Modal):
    def __init__(self, bot: "ShopBot", account: dict, mode: str) -> None:
        super().__init__(title="Spawner hinzufügen" if mode == "add" else "Spawner entfernen")
        self.bot = bot
        self.account_id = int(account["id"])
        self.mode = mode
        default = ""
        if mode == "add" and not int(account["spawner_count"]) and account.get("requested_qty"):
            default = str(account["requested_qty"])
        self.amount = discord.ui.TextInput(
            label="Anzahl", placeholder="z.B. 16", default=default or None, max_length=6, required=True,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.amount.value).strip()
        if not raw.isdigit() or not (1 <= int(raw) <= MAX_QTY):
            await interaction.response.send_message(
                embed=error_embed("Ungültige Anzahl", f"Bitte eine Zahl zwischen 1 und {MAX_QTY} eingeben."),
                ephemeral=True,
            )
            return
        amount = int(raw)
        account = await _get_account(self.bot, self.account_id)
        if not account or account["status"] != "open":
            await interaction.response.send_message(embed=error_embed("Konto nicht mehr offen"), ephemeral=True)
            return
        if self.mode == "remove" and amount > int(account["spawner_count"]):
            await interaction.response.send_message(
                embed=error_embed("Zu viele", f"Es sind nur **{account['spawner_count']}** Spawner eingetragen."),
                ephemeral=True,
            )
            return
        updated = await _apply_delta(self.bot, account, amount if self.mode == "add" else -amount)
        bone_price = await get_bone_price(self.bot, int(updated["guild_id"]))
        sign, verb = ("➕", "eingetragen") if self.mode == "add" else ("➖", "ausgetragen")
        await interaction.response.send_message(
            embed=success_embed(
                f"{sign} {amount} Spawner {verb}",
                f"Jetzt im Service: **{updated['spawner_count']}** Spawner "
                f"({_fmt_int(int(updated['spawner_count']) * bones_per_spawner_per_day())} Bones/Tag)\n"
                f"Von {interaction.user.mention}.",
            )
        )
        await interaction.followup.send(embed=_status_embed(updated, bone_price), ephemeral=True)


class AfkPayoutConfirmView(discord.ui.View):
    def __init__(self, bot: "ShopBot", account_id: int) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.account_id = account_id

    @discord.ui.button(label="Auszahlung bestätigen", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        button.disabled = True
        account = await _get_account(self.bot, self.account_id)
        bone_price = await get_bone_price(self.bot, interaction.guild_id or 0)
        if not account or account["status"] != "open" or not bone_price:
            await interaction.response.edit_message(content="Auszahlung nicht möglich (Konto/Bone-Preis).", embed=None, view=None)
            return
        bones = pending_bones(account, time.time())
        if bones < 1:
            await interaction.response.edit_message(content="Noch nichts aufgelaufen.", embed=None, view=None)
            return
        revenue, customer, shop = split_revenue(bones, bone_price)
        now = time.time()
        cur = await self.bot.db.db.execute(
            """
            INSERT INTO afk_payouts (guild_id, account_id, bones, bone_price, revenue, customer_percent, customer_amount, staff_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account["guild_id"], account["id"], bones, bone_price, revenue, config.AFK_CUSTOMER_PERCENT, customer, interaction.user.id),
        )
        await self.bot.db.db.execute(
            "UPDATE afk_accounts SET accrued_bones = 0, accrual_ts = ? WHERE id = ?", (now, account["id"])
        )
        await self.bot.db.db.commit()
        payout_id = int(cur.lastrowid)  # type: ignore[arg-type]

        embed = base_embed(
            f"💰 Geld-Auszahlung #{payout_id}",
            f"<@{account['user_id']}> — deine Spawner haben geliefert! 🎉\n"
            "_TxtEmpire behält den Rest und zahlt dir deinen Anteil aus._",
        )
        embed.color = discord.Color.green()
        embed.add_field(name="Gesamt-Erlös", value=f"**{format_price(revenue)}**", inline=False)
        embed.add_field(name=f"Dein Anteil ({config.AFK_CUSTOMER_PERCENT:g}%)", value=f"**{format_price(customer)}**", inline=True)
        embed.add_field(name=f"TxtEmpire ({100 - config.AFK_CUSTOMER_PERCENT:g}%)", value=format_price(shop), inline=True)
        embed.add_field(
            name="📊 So wird der Betrag berechnet",
            value=f"{_fmt_int(bones)} Bones × {format_price(bone_price)} = {format_price(revenue)}",
            inline=False,
        )
        embed.set_footer(text=f"erfasst von {interaction.user.display_name} · Ticket #{account['ticket_number']}")
        embed.add_field(
            name="💸 /pay-Befehl (Staff)",
            value=f"```\n/pay {account['ign']} {int(round(customer))}\n```",
            inline=False,
        )
        if interaction.channel is not None and hasattr(interaction.channel, "send"):
            await interaction.channel.send(embed=embed)  # type: ignore[union-attr]
        await interaction.response.edit_message(content=f"✅ Auszahlung #{payout_id} gepostet.", embed=None, view=None)


class AfkTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _account_or_error(self, interaction: discord.Interaction) -> Optional[dict]:
        account = await _get_account_by_channel(self.bot, interaction.channel_id or 0)
        if not account:
            await interaction.response.send_message(embed=error_embed("Kein offenes AFK-Konto in diesem Channel"), ephemeral=True)
        return account

    async def _staff_or_error(self, interaction: discord.Interaction, hint: str) -> bool:
        if interaction.guild is not None and await _is_afk_staff(self.bot, interaction):
            return True
        await interaction.response.send_message(embed=error_embed("Nur Staff", hint), ephemeral=True)
        return False

    @discord.ui.button(label="Spawner hinzufügen", style=discord.ButtonStyle.success, custom_id="afk:add", emoji="➕")
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._staff_or_error(interaction, "Schreib hier im Ticket, wie viele Spawner du übergibst — Staff trägt sie ein."):
            return
        if account := await self._account_or_error(interaction):
            await interaction.response.send_modal(AfkAdjustModal(self.bot, account, "add"))

    @discord.ui.button(label="Spawner entfernen", style=discord.ButtonStyle.danger, custom_id="afk:remove", emoji="➖")
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._staff_or_error(interaction, "Schreib hier im Ticket, wie viele Spawner du zurück möchtest — Staff trägt sie aus."):
            return
        if account := await self._account_or_error(interaction):
            await interaction.response.send_modal(AfkAdjustModal(self.bot, account, "remove"))

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, custom_id="afk:status", emoji="📊")
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if account := await self._account_or_error(interaction):
            bone_price = await get_bone_price(self.bot, int(account["guild_id"]))
            await interaction.response.send_message(embed=_status_embed(account, bone_price), ephemeral=True)

    @discord.ui.button(label="Auszahlen", style=discord.ButtonStyle.primary, custom_id="afk:payout", emoji="💸")
    async def payout(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._staff_or_error(interaction, "Nur Staff kann Auszahlungen erfassen."):
            return
        account = await self._account_or_error(interaction)
        if not account:
            return
        bone_price = await get_bone_price(self.bot, int(account["guild_id"]))
        if not bone_price:
            await interaction.response.send_message(
                embed=error_embed("Bone-Preis fehlt", "Erst mit `/spawner afkpreis` setzen."), ephemeral=True,
            )
            return
        bones = pending_bones(account, time.time())
        if bones < 1:
            await interaction.response.send_message(embed=warn_embed("Noch nichts aufgelaufen"), ephemeral=True)
            return
        revenue, customer, shop = split_revenue(bones, bone_price)
        await interaction.response.send_message(
            embed=base_embed(
                "Auszahlung erfassen?",
                f"{_fmt_int(bones)} Bones × {format_price(bone_price)} = **{format_price(revenue)}**\n"
                f"Kunde ({config.AFK_CUSTOMER_PERCENT:g}%): **{format_price(customer)}** · Shop: {format_price(shop)}\n\n"
                "Danach wird der aufgelaufene Stand zurückgesetzt.",
            ),
            view=AfkPayoutConfirmView(self.bot, int(account["id"])),
            ephemeral=True,
        )

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.secondary, custom_id="afk:close", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        account = await self._account_or_error(interaction)
        if not account:
            return
        staff = await _is_afk_staff(self.bot, interaction)
        if not staff and interaction.user.id != int(account["user_id"]):
            await interaction.response.send_message(embed=error_embed("Keine Berechtigung"), ephemeral=True)
            return
        if int(account["spawner_count"]) > 0 or pending_bones(account, time.time()) >= 1:
            await interaction.response.send_message(
                embed=error_embed(
                    "Noch nicht schließbar",
                    "Es sind noch Spawner eingetragen oder Einnahmen offen — erst alle Spawner austragen (➖) "
                    "und auszahlen (💸).",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        await self.bot.db.db.execute("UPDATE afk_accounts SET status = 'closed' WHERE id = ?", (account["id"],))
        await self.bot.db.db.commit()
        await interaction.followup.send(
            embed=warn_embed(
                "Ticket wird geschlossen",
                f"Geschlossen von {interaction.user.mention}. Channel wird in 5 Sekunden gelöscht.",
            )
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"AFK-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


class AfkPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Formular ausfüllen", style=discord.ButtonStyle.success, custom_id="afk:form", emoji="📝")
    async def form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await interaction.response.send_modal(AfkFormModal(self.bot))

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, custom_id="afk:panelstatus", emoji="📊")
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        if await _is_afk_staff(self.bot, interaction):
            await interaction.response.send_message(embed=await _overview_embed(self.bot, interaction.guild), ephemeral=True)
            return
        accounts = await _list_open_accounts(self.bot, interaction.guild.id, interaction.user.id)
        if not accounts:
            await interaction.response.send_message(
                embed=warn_embed("Kein AFK-Service", "Du hast aktuell keinen AFK-Service. Klick auf **Formular ausfüllen**."),
                ephemeral=True,
            )
            return
        bone_price = await get_bone_price(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embeds=[_status_embed(a, bone_price) for a in accounts[:5]], ephemeral=True,
        )


def _self_check() -> None:
    """ponytail: Nachweis der Abrechnung (3.6 Bones/Min * 22h * 75%) und des Einfrierens bei Änderungen."""
    assert abs(bones_per_spawner_per_day() - 4752.0) < 1e-6
    revenue, customer, shop = split_revenue(100 * 4752, 54)
    assert abs(revenue - 25_660_800) < 1e-3 and abs(customer - 19_245_600) < 1e-3 and abs(shop - 6_415_200) < 1e-3
    day = {"spawner_count": 10, "accrued_bones": 0.0, "accrual_ts": 1000.0}
    assert abs(pending_bones(day, 1000.0 + SECONDS_PER_DAY) - 47_520) < 1e-6
    frozen = {"spawner_count": 20, "accrued_bones": pending_bones(day, 1000.0 + SECONDS_PER_DAY), "accrual_ts": 1000.0 + SECONDS_PER_DAY}
    assert abs(pending_bones(frozen, 1000.0 + 2 * SECONDS_PER_DAY) - (47_520 + 95_040)) < 1e-6
    assert pending_bones(day, 500.0) == 0.0  # keine negative Zeit
    assert _fmt_int(1234567) == "1.234.567"


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    bot.add_view(AfkPanelView(bot))
    bot.add_view(AfkTicketView(bot))


if __name__ == "__main__":
    _self_check()
    print("OK")
