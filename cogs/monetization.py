"""
monetization.py
================

Drei eigenständige Monetarisierungs-Features, getrennt vom normalen Shop:

1) VIP-Abo — /vip einrichten legt Rolle, Preis (Shop-Währung + fester
   PayPal-Preis) und Laufzeit fest. /vipkaufen öffnet ein Ticket wie beim
   Shop; nach Bestätigung wird die Rolle vergeben/verlängert. Ein
   10-Minuten-Sweep entfernt die Rolle automatisch nach Ablauf (mit DM +
   optionalem Log-Channel).

2) Empfehlungs-Provision — /empfehlung code gibt einen persönlichen Code,
   /empfehlung einloesen verknüpft einen neuen Käufer mit seinem Werber.
   Bei jedem bestätigten Kauf des Geworbenen (Hauptshop, Spieler-Shop)
   bekommt der Werber automatisch einen %-Anteil als Credits gutgeschrieben
   (siehe utils/referrals.py — dort eingehängt, hier nur Konfiguration).

3) Mystery Box — /box erstellen + /box preis für eine gewichtete
   Gewinn-Tabelle (Credits, Rolle, Rabattcode, Niete). /box kaufen zieht
   sofort Credits ab und würfelt den Gewinn aus — kein Ticket, kein Staff
   nötig (Impulskauf-Mechanik).

Eigene Tabellen (monetization_settings, vip_subscriptions, vip_tickets,
referral_codes, referral_links, mystery_boxes, mystery_box_prizes) — keine
Änderung an db/database.py nötig.
"""
from __future__ import annotations

import asyncio
import random
import string
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.credits import format_credits
from utils.embeds import base_embed, error_embed, format_price, success_embed, warn_embed
from utils.price import parse_price
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot

_TIME_FMT = "%Y-%m-%d %H:%M:%S"


# ── DB Bootstrap ─────────────────────────────────────────────────────────

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS monetization_settings (
            guild_id INTEGER PRIMARY KEY,
            staff_role_id INTEGER,
            log_channel_id INTEGER,
            vip_role_id INTEGER,
            vip_price REAL,
            vip_paypal_price REAL,
            vip_days INTEGER NOT NULL DEFAULT 30,
            referral_percent REAL NOT NULL DEFAULT 0,
            next_ticket_number INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS vip_subscriptions (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS vip_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            ticket_channel_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS referral_codes (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id),
            UNIQUE (guild_id, code)
        );
        CREATE TABLE IF NOT EXISTS referral_links (
            guild_id INTEGER NOT NULL,
            invited_user_id INTEGER NOT NULL,
            referrer_user_id INTEGER NOT NULL,
            credited_total REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (guild_id, invited_user_id)
        );
        CREATE TABLE IF NOT EXISTS mystery_boxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            UNIQUE (guild_id, name)
        );
        CREATE TABLE IF NOT EXISTS mystery_box_prizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            box_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            weight INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (box_id) REFERENCES mystery_boxes(id) ON DELETE CASCADE
        );
        """
    )
    await bot.db.db.commit()


async def _get_settings(bot: "ShopBot", guild_id: int) -> dict:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO monetization_settings (guild_id) VALUES (?)", (guild_id,)
    )
    await bot.db.db.commit()
    row = await bot.db.fetchone(
        "SELECT * FROM monetization_settings WHERE guild_id = ?", (guild_id,)
    )
    return dict(row) if row else {}


async def _next_ticket_number(bot: "ShopBot", guild_id: int) -> int:
    settings = await _get_settings(bot, guild_id)
    n = int(settings.get("next_ticket_number") or 1)
    await bot.db.db.execute(
        "UPDATE monetization_settings SET next_ticket_number = ? WHERE guild_id = ?",
        (n + 1, guild_id),
    )
    await bot.db.db.commit()
    return n


def _gen_referral_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=6))


# ── VIP: Ticket-Erstellung & Buttons ────────────────────────────────────

async def _get_vip_ticket_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM vip_tickets WHERE ticket_channel_id = ?", (channel_id,)
    )
    return dict(row) if row else None


async def _create_vip_ticket(bot: "ShopBot", interaction: discord.Interaction) -> None:
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    settings = await _get_settings(bot, guild.id)
    vip_role_id = settings.get("vip_role_id")
    price = settings.get("vip_price")
    if not vip_role_id or price is None:
        await interaction.followup.send(
            embed=error_embed("VIP nicht eingerichtet", "Staff muss erst `/vip einrichten` ausführen."),
            ephemeral=True,
        )
        return
    price = float(price)
    paypal_price = settings.get("vip_paypal_price")
    days = int(settings.get("vip_days") or 30)

    guild_settings = await bot.db.ensure_guild(guild.id)
    category_id = guild_settings.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None
    staff_role_id = settings.get("staff_role_id") or guild_settings.get("staff_role_id")
    staff_role = guild.get_role(int(staff_role_id)) if staff_role_id else None
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
            view_channel=True, send_messages=True, attach_files=True,
            embed_links=True, read_message_history=True,
        )
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True,
            embed_links=True, read_message_history=True, manage_messages=True,
        )

    ticket_number = await _next_ticket_number(bot, guild.id)
    cur = await bot.db.db.execute(
        "INSERT INTO vip_tickets (guild_id, ticket_number, user_id, price, status) VALUES (?, ?, ?, ?, 'pending')",
        (guild.id, ticket_number, interaction.user.id, price),
    )
    await bot.db.db.commit()
    ticket_id = int(cur.lastrowid)  # type: ignore[arg-type]

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower())[:18]
    name = f"vip-{ticket_number:04d}-{safe}"[:100]
    try:
        channel = await guild.create_text_channel(
            name=name, category=category, overwrites=overwrites,
            reason=f"VIP-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await interaction.followup.send(embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True)
        return

    await bot.db.db.execute(
        "UPDATE vip_tickets SET ticket_channel_id = ? WHERE id = ?", (channel.id, ticket_id)
    )
    await bot.db.db.commit()

    paypal_line = (
        f"**Zahlung 2 — PayPal ({config.PAYPAL_EMAIL}):** fester Preis **{float(paypal_price):.2f} €**\n"
        "_Bitte „Freunde/Familie“ wählen, danach hier im Ticket Bescheid geben._"
        if paypal_price is not None
        else ""
    )
    role = guild.get_role(int(vip_role_id))
    embed = base_embed(
        f"⭐ VIP-Abo — #{ticket_number}",
        f"Hallo {interaction.user.mention}, hier ist deine VIP-Bestellung.\n\n"
        f"Rolle: {role.mention if role else '_Rolle nicht gefunden_'}\n"
        f"Laufzeit: **{days} Tage** (bei bestehendem VIP wird verlängert)\n\n"
        f"**Zahlung 1 — Shop-Währung:**\n```\n{config.mc_pay_command(price)}\n```\n"
        f"{paypal_line}\n\n"
        "Sobald die Zahlung bestätigt ist, klickt Staff **✅ Bestätigen**.",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(content=f"{interaction.user.mention} {mention}", embed=embed, view=VipTicketView(bot))
    await interaction.followup.send(
        embed=success_embed("Ticket erstellt", f"Dein Ticket: {channel.mention}"), ephemeral=True,
    )


async def _grant_vip(bot: "ShopBot", guild: discord.Guild, user_id: int, days: int, role_id: int) -> datetime:
    row = await bot.db.fetchone(
        "SELECT expires_at FROM vip_subscriptions WHERE guild_id = ? AND user_id = ?", (guild.id, user_id)
    )
    now = datetime.now(timezone.utc)
    base = now
    if row:
        try:
            existing = datetime.strptime(row["expires_at"], _TIME_FMT).replace(tzinfo=timezone.utc)
            if existing > now:
                base = existing
        except (ValueError, TypeError):
            pass
    new_expiry = base + timedelta(days=days)
    await bot.db.db.execute(
        """
        INSERT INTO vip_subscriptions (guild_id, user_id, expires_at) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET expires_at = excluded.expires_at
        """,
        (guild.id, user_id, new_expiry.strftime(_TIME_FMT)),
    )
    await bot.db.db.commit()
    member = guild.get_member(user_id)
    if member is not None:
        role = guild.get_role(role_id)
        if role is not None and role not in member.roles:
            try:
                await member.add_roles(role, reason="VIP-Abo bestätigt")
            except discord.HTTPException:
                pass
    return new_expiry


class VipTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.success, custom_id="vipticket:confirm", emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_vip_ticket_by_channel(self.bot, interaction.channel_id)
        if not row or row["status"] != "pending":
            await interaction.response.send_message(embed=error_embed("Kein offenes VIP-Ticket"), ephemeral=True)
            return
        await interaction.response.defer()
        await self.bot.db.db.execute(
            "UPDATE vip_tickets SET status = 'confirmed', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
            (interaction.user.id, int(row["id"])),
        )
        await self.bot.db.db.commit()

        from utils.referrals import credit_referral

        await credit_referral(self.bot, interaction.guild, int(row["user_id"]), float(row["price"]))

        settings = await _get_settings(self.bot, interaction.guild.id)
        role_id = int(settings.get("vip_role_id") or 0)
        days = int(settings.get("vip_days") or 30)
        if role_id:
            expiry = await _grant_vip(self.bot, interaction.guild, int(row["user_id"]), days, role_id)
            await interaction.followup.send(
                embed=success_embed(
                    "VIP aktiv",
                    f"Bestätigt von {interaction.user.mention}. VIP läuft jetzt bis "
                    f"<t:{int(expiry.timestamp())}:f>.",
                )
            )
        else:
            await interaction.followup.send(embed=error_embed("Keine VIP-Rolle konfiguriert."))

    @discord.ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="vipticket:reject", emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff"), ephemeral=True)
            return
        row = await _get_vip_ticket_by_channel(self.bot, interaction.channel_id)
        if not row or row["status"] != "pending":
            await interaction.response.send_message(embed=error_embed("Kein offenes VIP-Ticket"), ephemeral=True)
            return
        await interaction.response.defer()
        await self.bot.db.db.execute(
            "UPDATE vip_tickets SET status = 'rejected', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
            (interaction.user.id, int(row["id"])),
        )
        await self.bot.db.db.commit()
        await interaction.followup.send(embed=warn_embed("Abgelehnt", f"Abgelehnt von {interaction.user.mention}."))

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.secondary, custom_id="vipticket:close", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        row = await _get_vip_ticket_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(embed=error_embed("Kein VIP-Ticket"), ephemeral=True)
            return
        staff = await is_staff(self.bot, interaction)
        is_owner = row.get("user_id") and interaction.user.id == int(row["user_id"])
        if not staff and not is_owner:
            await interaction.response.send_message(embed=error_embed("Keine Berechtigung"), ephemeral=True)
            return
        await interaction.response.defer()
        if row["status"] == "pending":
            await self.bot.db.db.execute(
                "UPDATE vip_tickets SET status = 'rejected', created_by = ?, confirmed_at = datetime('now') WHERE id = ?",
                (interaction.user.id, int(row["id"])),
            )
            await self.bot.db.db.commit()
        await interaction.followup.send(
            embed=warn_embed("Ticket wird geschlossen", f"Geschlossen von {interaction.user.mention}. Channel wird in 5 Sekunden gelöscht.")
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"VIP-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


async def _sweep_expired_vip(bot: "ShopBot") -> int:
    now = datetime.now(timezone.utc)
    rows = await bot.db.fetchall("SELECT * FROM vip_subscriptions")
    removed = 0
    for r in rows:
        row = dict(r)
        try:
            expiry = datetime.strptime(row["expires_at"], _TIME_FMT).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if now < expiry:
            continue
        guild = bot.get_guild(int(row["guild_id"]))
        if guild is None:
            continue
        await bot.db.db.execute(
            "DELETE FROM vip_subscriptions WHERE guild_id = ? AND user_id = ?",
            (row["guild_id"], row["user_id"]),
        )
        await bot.db.db.commit()
        removed += 1
        settings = await _get_settings(bot, guild.id)
        role_id = settings.get("vip_role_id")
        member = guild.get_member(int(row["user_id"]))
        if member is not None and role_id:
            role = guild.get_role(int(role_id))
            if role is not None and role in member.roles:
                try:
                    await member.remove_roles(role, reason="VIP-Abo abgelaufen")
                except discord.HTTPException:
                    pass
            try:
                await member.send("⭐ Dein VIP-Abo ist abgelaufen. Mit `/vipkaufen` kannst du es erneuern.")
            except discord.HTTPException:
                pass
        log_channel_id = settings.get("log_channel_id")
        if log_channel_id:
            channel = guild.get_channel(int(log_channel_id))
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(f"⭐ VIP von <@{row['user_id']}> ist abgelaufen.")
                except discord.HTTPException:
                    pass
    return removed


# ── Mystery Box ──────────────────────────────────────────────────────────

KIND_LABELS = {"credits": "Credits", "role": "Rolle", "code": "Rabattcode", "nichts": "Niete"}


async def _list_boxes(bot: "ShopBot", guild_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        "SELECT * FROM mystery_boxes WHERE guild_id = ? ORDER BY name", (guild_id,)
    )
    return [dict(r) for r in rows]


async def _get_box_by_name(bot: "ShopBot", guild_id: int, name: str) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM mystery_boxes WHERE guild_id = ? AND lower(name) = lower(?)", (guild_id, name.strip())
    )
    return dict(row) if row else None


async def _list_prizes(bot: "ShopBot", box_id: int) -> list[dict]:
    rows = await bot.db.fetchall(
        "SELECT * FROM mystery_box_prizes WHERE box_id = ? ORDER BY id", (box_id,)
    )
    return [dict(r) for r in rows]


async def _roll_prize(bot: "ShopBot", box_id: int) -> Optional[dict]:
    prizes = await _list_prizes(bot, box_id)
    prizes = [p for p in prizes if int(p.get("weight") or 0) > 0]
    if not prizes:
        return None
    weights = [int(p["weight"]) for p in prizes]
    return random.choices(prizes, weights=weights, k=1)[0]


async def _apply_prize(
    bot: "ShopBot", interaction: discord.Interaction, prize: dict
) -> str:
    """Wendet den Gewinn an und gibt eine Anzeige-Zeile zurück."""
    guild = interaction.guild
    assert guild is not None
    kind = prize["kind"]
    value = prize.get("value") or ""
    if kind == "credits":
        amount = float(value or 0)
        balance = await bot.db.add_credits(guild.id, interaction.user.id, amount)
        return f"🪙 **{format_credits(amount)} Credits** (Guthaben jetzt: {format_credits(balance)})"
    if kind == "role":
        role = guild.get_role(int(value)) if value.isdigit() else None
        if role is None or not isinstance(interaction.user, discord.Member):
            return f"🎭 Rolle **{prize['label']}** (konnte nicht automatisch vergeben werden — bitte Staff kontaktieren)"
        try:
            await interaction.user.add_roles(role, reason="Mystery-Box-Gewinn")
            return f"🎭 Rolle {role.mention}"
        except discord.HTTPException:
            return f"🎭 Rolle **{role.name}** (Vergabe fehlgeschlagen — bitte Staff kontaktieren)"
    if kind == "code":
        try:
            dtype, dval = value.split(":", 1)
        except ValueError:
            dtype, dval = "percent", "10"
        code = "BOX-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        try:
            await bot.db.create_discount_code(
                guild.id, code, discount_type=dtype, discount_value=float(dval),
                max_uses=1, max_per_user=1, label=f"Mystery-Box · {prize['label']}"[:100],
                created_by=interaction.user.id, kind="rabatt",
            )
            from utils.discount_codes import format_code_discount
            return f"🏷️ Rabattcode `{code}` — {format_code_discount(dtype, float(dval))}"
        except Exception:
            return f"🏷️ Rabattcode konnte nicht erstellt werden — bitte Staff kontaktieren ({prize['label']})"
    return "😢 Niete — nächstes Mal mehr Glück!"


class BoxSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", boxes: list[dict]) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(
                label=b["name"][:100], value=str(b["id"]), description=format_price(float(b["price"]))[:100],
            )
            for b in boxes[:25]
        ]
        super().__init__(placeholder="Box auswählen ...", options=options, custom_id="monetization:boxselect")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        box_id = int(self.values[0])
        box_row = await self.bot.db.fetchone(
            "SELECT * FROM mystery_boxes WHERE id = ? AND guild_id = ?", (box_id, interaction.guild.id)
        )
        if not box_row or not int(box_row["active"]):
            await interaction.response.send_message(embed=error_embed("Box nicht mehr verfügbar"), ephemeral=True)
            return
        box = dict(box_row)
        price = float(box["price"])
        ok = await self.bot.db.try_deduct_credits(interaction.guild.id, interaction.user.id, price)
        if not ok:
            balance = await self.bot.db.get_credits(interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed(
                    "Zu wenig Credits",
                    f"Benötigt: **{format_credits(price)}** · Guthaben: **{format_credits(balance)}**",
                ),
                ephemeral=True,
            )
            return
        prize = await _roll_prize(self.bot, box_id)
        if prize is None:
            await self.bot.db.add_credits(interaction.guild.id, interaction.user.id, price)
            await interaction.response.send_message(
                embed=error_embed("Box hat keine Preise", "Credits wurden zurückerstattet."), ephemeral=True,
            )
            return
        result_line = await _apply_prize(self.bot, interaction, prize)
        await interaction.response.send_message(
            embed=success_embed(f"🎁 {box['name']} geöffnet!", f"Du hast gewonnen:\n{result_line}"),
            ephemeral=True,
        )


class BoxPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot", boxes: list[dict]) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        if boxes:
            self.add_item(BoxSelect(bot, boxes))


# ── Slash-Commands ───────────────────────────────────────────────────────

class MonetizationCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot
        self.vip_expire_loop.start()

    def cog_unload(self) -> None:
        self.vip_expire_loop.cancel()

    @tasks.loop(minutes=10)
    async def vip_expire_loop(self) -> None:
        try:
            n = await _sweep_expired_vip(self.bot)
            if n:
                print(f"[Monetization] {n} abgelaufene VIP-Abo(s) entfernt")
        except Exception as e:
            print(f"[Monetization] VIP-Sweep fehlgeschlagen: {e!r}")

    @vip_expire_loop.before_loop
    async def before_vip_expire_loop(self) -> None:
        await self.bot.wait_until_ready()

    vip_group = app_commands.Group(
        name="vip", description="VIP-Abo verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )
    box_group = app_commands.Group(
        name="box", description="Mystery-Boxen verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )
    referral_group = app_commands.Group(name="empfehlung", description="Empfehlungs-Programm")

    # ── VIP ──────────────────────────────────────────────────────────

    @vip_group.command(name="einrichten", description="VIP-Abo konfigurieren")
    @app_commands.describe(
        rolle="Rolle, die VIP-Mitglieder bekommen",
        preis="Preis in Shop-Währung pro Laufzeit, z.B. 1m",
        tage="Laufzeit in Tagen (Standard 30)",
        paypal_preis="Fester PayPal-Preis in Euro (optional, z.B. 4.99)",
    )
    async def vip_setup(
        self,
        interaction: discord.Interaction,
        rolle: discord.Role,
        preis: str,
        tage: app_commands.Range[int, 1, 365] = 30,
        paypal_preis: Optional[float] = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            price = parse_price(preis)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Preis", "Beispiele: `500k`, `1.5m`"), ephemeral=True,
            )
            return
        await self.bot.db.db.execute(
            """
            INSERT INTO monetization_settings (guild_id, vip_role_id, vip_price, vip_paypal_price, vip_days)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                vip_role_id = excluded.vip_role_id,
                vip_price = excluded.vip_price,
                vip_paypal_price = excluded.vip_paypal_price,
                vip_days = excluded.vip_days
            """,
            (interaction.guild.id, rolle.id, price, paypal_preis, int(tage)),
        )
        await self.bot.db.db.commit()
        paypal_note = f" oder {paypal_preis:.2f} € PayPal" if paypal_preis is not None else ""
        await interaction.response.send_message(
            embed=success_embed(
                "VIP eingerichtet",
                f"{rolle.mention} · {format_price(price)}{paypal_note} · {tage} Tage.\n"
                "Mitglieder kaufen mit `/vipkaufen`.",
            ),
            ephemeral=True,
        )

    @vip_group.command(name="rolle_staff", description="Staff-Rolle für VIP-Tickets setzen")
    @app_commands.describe(rolle="Rolle, die in VIP-Tickets gepingt wird")
    async def vip_staff_role(self, interaction: discord.Interaction, rolle: discord.Role) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "INSERT INTO monetization_settings (guild_id, staff_role_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET staff_role_id = excluded.staff_role_id",
            (interaction.guild.id, rolle.id),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Gespeichert", f"VIP-Support-Rolle ist jetzt {rolle.mention}."), ephemeral=True,
        )

    @vip_group.command(name="logchannel", description="Channel für VIP-Ablauf- und Empfehlungs-Meldungen")
    @app_commands.describe(channel="Ziel-Channel (leer = deaktivieren)")
    async def vip_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "INSERT INTO monetization_settings (guild_id, log_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id",
            (interaction.guild.id, channel.id if channel else None),
        )
        await self.bot.db.db.commit()
        msg = f"Meldungen gehen jetzt in {channel.mention}." if channel else "Meldungen sind jetzt deaktiviert."
        await interaction.response.send_message(embed=success_embed("Gespeichert", msg), ephemeral=True)

    @vip_group.command(name="geben", description="VIP manuell vergeben/verlängern (z.B. Gewinnspiel-Preis)")
    @app_commands.describe(user="Mitglied", tage="Laufzeit in Tagen (Standard: konfigurierte Laufzeit)")
    async def vip_grant(
        self, interaction: discord.Interaction, user: discord.Member, tage: Optional[int] = None,
    ) -> None:
        assert interaction.guild is not None
        settings = await _get_settings(self.bot, interaction.guild.id)
        role_id = settings.get("vip_role_id")
        if not role_id:
            await interaction.response.send_message(embed=error_embed("VIP nicht eingerichtet"), ephemeral=True)
            return
        days = int(tage) if tage else int(settings.get("vip_days") or 30)
        expiry = await _grant_vip(self.bot, interaction.guild, user.id, days, int(role_id))
        await interaction.response.send_message(
            embed=success_embed("VIP vergeben", f"{user.mention} ist jetzt VIP bis <t:{int(expiry.timestamp())}:f>."),
            ephemeral=True,
        )

    @app_commands.command(name="vipkaufen", description="VIP-Abo kaufen oder verlängern")
    async def vip_buy(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(embed=error_embed("Nur auf dem Server"), ephemeral=True)
            return
        await _create_vip_ticket(self.bot, interaction)

    @app_commands.command(name="vipstatus", description="Eigenen (oder fremden) VIP-Status anzeigen")
    @app_commands.describe(user="Anderes Mitglied prüfen (Staff)")
    async def vip_status(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        assert interaction.guild is not None
        target = user or interaction.user
        if user is not None and not await is_staff(self.bot, interaction):
            await interaction.response.send_message(embed=error_embed("Nur Staff kann andere prüfen"), ephemeral=True)
            return
        row = await self.bot.db.fetchone(
            "SELECT expires_at FROM vip_subscriptions WHERE guild_id = ? AND user_id = ?",
            (interaction.guild.id, target.id),
        )
        if not row:
            await interaction.response.send_message(
                embed=base_embed("VIP-Status", f"{target.mention} ist aktuell **kein VIP**."), ephemeral=True,
            )
            return
        try:
            expiry = datetime.strptime(row["expires_at"], _TIME_FMT).replace(tzinfo=timezone.utc)
            body = f"{target.mention} ist VIP bis <t:{int(expiry.timestamp())}:f>."
        except (ValueError, TypeError):
            body = f"{target.mention} ist VIP."
        await interaction.response.send_message(embed=base_embed("VIP-Status", body), ephemeral=True)

    # ── Empfehlungen ─────────────────────────────────────────────────

    @referral_group.command(name="code", description="Deinen persönlichen Empfehlungs-Code anzeigen")
    async def referral_code(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        row = await self.bot.db.fetchone(
            "SELECT code FROM referral_codes WHERE guild_id = ? AND user_id = ?",
            (interaction.guild.id, interaction.user.id),
        )
        if row:
            code = row["code"]
        else:
            code = _gen_referral_code()
            for _ in range(5):
                existing = await self.bot.db.fetchone(
                    "SELECT 1 FROM referral_codes WHERE guild_id = ? AND code = ?", (interaction.guild.id, code)
                )
                if not existing:
                    break
                code = _gen_referral_code()
            await self.bot.db.db.execute(
                "INSERT INTO referral_codes (guild_id, user_id, code) VALUES (?, ?, ?)",
                (interaction.guild.id, interaction.user.id, code),
            )
            await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed(
                "Dein Empfehlungs-Code",
                f"`{code}`\n\nNeue Mitglieder tragen ihn mit `/empfehlung einloesen code:{code}` ein — "
                "du bekommst dann bei jedem ihrer Käufe automatisch Credits gutgeschrieben.",
            ),
            ephemeral=True,
        )

    @referral_group.command(name="einloesen", description="Empfehlungs-Code eines anderen Mitglieds eintragen")
    @app_commands.describe(code="Code deines Werbers")
    async def referral_redeem(self, interaction: discord.Interaction, code: str) -> None:
        assert interaction.guild is not None
        existing = await self.bot.db.fetchone(
            "SELECT 1 FROM referral_links WHERE guild_id = ? AND invited_user_id = ?",
            (interaction.guild.id, interaction.user.id),
        )
        if existing:
            await interaction.response.send_message(
                embed=error_embed("Du hast schon einen Werber eingetragen."), ephemeral=True,
            )
            return
        row = await self.bot.db.fetchone(
            "SELECT user_id FROM referral_codes WHERE guild_id = ? AND code = ?",
            (interaction.guild.id, code.strip().upper()),
        )
        if not row:
            await interaction.response.send_message(embed=error_embed("Code nicht gefunden"), ephemeral=True)
            return
        referrer_id = int(row["user_id"])
        if referrer_id == interaction.user.id:
            await interaction.response.send_message(embed=error_embed("Du kannst dich nicht selbst werben."), ephemeral=True)
            return
        await self.bot.db.db.execute(
            "INSERT INTO referral_links (guild_id, invited_user_id, referrer_user_id) VALUES (?, ?, ?)",
            (interaction.guild.id, interaction.user.id, referrer_id),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Eingetragen", f"<@{referrer_id}> ist jetzt dein Werber."), ephemeral=True,
        )

    @referral_group.command(name="einrichten", description="Empfehlungs-Provision in % festlegen (0 = aus)")
    @app_commands.describe(prozent="Wie viel % jedes Kaufs eines Geworbenen der Werber als Credits bekommt")
    @app_commands.default_permissions(manage_guild=True)
    async def referral_setup(
        self, interaction: discord.Interaction, prozent: app_commands.Range[float, 0, 100],
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.db.execute(
            "INSERT INTO monetization_settings (guild_id, referral_percent) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET referral_percent = excluded.referral_percent",
            (interaction.guild.id, float(prozent)),
        )
        await self.bot.db.db.commit()
        msg = f"Empfehlungs-Provision ist jetzt **{prozent:g}%**." if prozent > 0 else "Empfehlungs-Provision ist jetzt deaktiviert."
        await interaction.response.send_message(embed=success_embed("Gespeichert", msg), ephemeral=True)

    # ── Mystery Box ──────────────────────────────────────────────────

    async def _box_ac(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not interaction.guild_id:
            return []
        boxes = await _list_boxes(self.bot, interaction.guild_id)
        q = (current or "").lower().strip()
        if q:
            boxes = [b for b in boxes if q in b["name"].lower()]
        return [app_commands.Choice(name=b["name"][:100], value=b["name"]) for b in boxes[:25]]

    @box_group.command(name="erstellen", description="Neue Mystery-Box anlegen")
    @app_commands.describe(name="Name der Box", preis="Preis in Credits")
    async def box_create(self, interaction: discord.Interaction, name: str, preis: float) -> None:
        assert interaction.guild is not None
        existing = await _get_box_by_name(self.bot, interaction.guild.id, name)
        if existing:
            await interaction.response.send_message(embed=error_embed("Box gibt es schon"), ephemeral=True)
            return
        await self.bot.db.db.execute(
            "INSERT INTO mystery_boxes (guild_id, name, price) VALUES (?, ?, ?)",
            (interaction.guild.id, name.strip(), float(preis)),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Box angelegt", f"**{name}** · {format_credits(preis)}\nJetzt Preise: `/box preis`"),
            ephemeral=True,
        )

    @box_group.command(name="preis", description="Gewichteten Preis zu einer Box hinzufügen")
    @app_commands.describe(
        box="Box (tippen zum Suchen)",
        art="Was gewonnen wird",
        bezeichnung="Anzeigename des Preises",
        gewicht="Gewichtung (höher = wahrscheinlicher), z.B. 10",
        credits_betrag="Nur bei Art=Credits: Anzahl Credits",
        rolle="Nur bei Art=Rolle: die Rolle",
        rabatt_prozent="Nur bei Art=Rabattcode: Rabatt in %",
    )
    @app_commands.choices(
        art=[
            app_commands.Choice(name="Credits", value="credits"),
            app_commands.Choice(name="Rolle", value="role"),
            app_commands.Choice(name="Rabattcode (%)", value="code"),
            app_commands.Choice(name="Niete", value="nichts"),
        ],
    )
    async def box_add_prize(
        self,
        interaction: discord.Interaction,
        box: str,
        art: app_commands.Choice[str],
        bezeichnung: str,
        gewicht: app_commands.Range[int, 1, 1000] = 10,
        credits_betrag: Optional[float] = None,
        rolle: Optional[discord.Role] = None,
        rabatt_prozent: Optional[float] = None,
    ) -> None:
        assert interaction.guild is not None
        box_row = await _get_box_by_name(self.bot, interaction.guild.id, box)
        if not box_row:
            await interaction.response.send_message(embed=error_embed("Box nicht gefunden"), ephemeral=True)
            return
        value = ""
        if art.value == "credits":
            if credits_betrag is None or credits_betrag <= 0:
                await interaction.response.send_message(
                    embed=error_embed("credits_betrag fehlt/ungültig"), ephemeral=True,
                )
                return
            value = str(float(credits_betrag))
        elif art.value == "role":
            if rolle is None:
                await interaction.response.send_message(embed=error_embed("rolle fehlt"), ephemeral=True)
                return
            value = str(rolle.id)
        elif art.value == "code":
            if rabatt_prozent is None or not (0 < rabatt_prozent < 100):
                await interaction.response.send_message(
                    embed=error_embed("rabatt_prozent fehlt/ungültig (0–100)"), ephemeral=True,
                )
                return
            value = f"percent:{rabatt_prozent}"

        await self.bot.db.db.execute(
            "INSERT INTO mystery_box_prizes (box_id, label, kind, value, weight) VALUES (?, ?, ?, ?, ?)",
            (box_row["id"], bezeichnung.strip(), art.value, value, int(gewicht)),
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed(
                "Preis hinzugefügt", f"**{bezeichnung}** ({KIND_LABELS[art.value]}) · Gewicht {gewicht} in **{box}**",
            ),
            ephemeral=True,
        )

    @box_add_prize.autocomplete("box")
    async def box_add_prize_ac(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self._box_ac(interaction, current)

    @box_group.command(name="liste", description="Box + ihre Preistabelle anzeigen")
    @app_commands.describe(box="Box (tippen zum Suchen)")
    async def box_list(self, interaction: discord.Interaction, box: str) -> None:
        assert interaction.guild is not None
        box_row = await _get_box_by_name(self.bot, interaction.guild.id, box)
        if not box_row:
            await interaction.response.send_message(embed=error_embed("Box nicht gefunden"), ephemeral=True)
            return
        prizes = await _list_prizes(self.bot, int(box_row["id"]))
        total_weight = sum(int(p["weight"]) for p in prizes) or 1
        lines = [
            f"`{p['id']}` **{p['label']}** ({KIND_LABELS.get(p['kind'], p['kind'])}) · "
            f"{int(p['weight'])/total_weight*100:.1f}%"
            for p in prizes
        ] or ["_Keine Preise._"]
        await interaction.response.send_message(
            embed=base_embed(
                f"📦 {box_row['name']} — {format_credits(float(box_row['price']))}",
                "\n".join(lines)[:4000],
            ),
            ephemeral=True,
        )

    @box_list.autocomplete("box")
    async def box_list_ac(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self._box_ac(interaction, current)

    @box_group.command(name="panel", description="Mystery-Box-Panel posten")
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    async def box_panel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        boxes = [b for b in await _list_boxes(self.bot, interaction.guild.id) if int(b["active"])]
        if not boxes:
            await interaction.response.send_message(embed=error_embed("Keine aktiven Boxen"), ephemeral=True)
            return
        body = "\n".join(f"• **{b['name']}** — {format_credits(float(b['price']))}" for b in boxes)
        embed = base_embed(
            "🎁 Mystery-Boxen",
            f"Zahl mit deinen Credits und riskier's — jede Box hat eine eigene Gewinn-Tabelle.\n\n{body}",
        )
        msg = await target.send(embed=embed, view=BoxPanelView(self.bot, boxes))
        await interaction.response.send_message(
            embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(MonetizationCog(bot))
