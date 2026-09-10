"""
packai_keys.py
==============

Verkauf von Pack-AI-Lizenzkeys (Tokens: 14d / 30d / Lifetime) über den Shop-Bot.

Flow:
  - Panel „Pack AI kaufen“ → Laufzeit wählen → privates Ticket
  - Ticket zeigt PayPal-Adresse + Betrag + Payee-Details
  - Staff ✅ Bestätigen → Key über Pack-AI License-API erzeugen → DM an Käufer

.env:
  PACKAI_LICENSE_API_URL=http://127.0.0.1:8787
  PACKAI_LICENSE_API_SECRET=...
  PACKAI_PRICE_14D=4.99
  PACKAI_PRICE_30D=9.99
  PACKAI_PRICE_LIFETIME=29.99
  PAYPAL_EMAIL=... (global, bereits im Shop)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional

import discord
import requests
from discord import app_commands
from discord.ext import commands

import config
from integrations.shop_api import shop_api
from utils import tweak_vouch
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

TIER_14D = "14d"
TIER_30D = "30d"
TIER_LIFETIME = "lifetime"
TIER_ORDER = (TIER_14D, TIER_30D, TIER_LIFETIME)

TIER_LABELS = {
    TIER_14D: "14 Tage · 50 Tokens",
    TIER_30D: "30 Tage · 200 Tokens",
    TIER_LIFETIME: "Lifetime · 2000 Tokens",
}

TIER_TOKENS = {TIER_14D: 50, TIER_30D: 200, TIER_LIFETIME: 2000}


def _api_base() -> str:
    url = (getattr(config, "PACKAI_LICENSE_API_URL", "") or "").strip().rstrip("/")
    if url:
        return url
    return "http://127.0.0.1:8787"


def _api_secret() -> str:
    return (getattr(config, "PACKAI_LICENSE_API_SECRET", "") or "").strip()


def _api_configured() -> bool:
    return bool(_api_secret()) and _api_secret() != "change-me"


def _create_license_key(plan: str, created_by: str, note: str = "") -> tuple[bool, str, str]:
    """Returns (ok, key_or_error, raw_body)."""
    try:
        r = requests.post(
            f"{_api_base()}/admin/create",
            json={
                "plan": plan,
                "count": 1,
                "created_by": created_by,
                "note": note,
            },
            headers={
                "X-PackAI-Secret": _api_secret(),
                "Content-Type": "application/json",
            },
            timeout=20,
        )
    except requests.RequestException as e:
        return False, str(e), ""
    body = r.text[:800]
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {body}", body
    try:
        data = r.json()
    except Exception:
        return False, body, body
    keys = data.get("keys") or []
    if not keys:
        return False, "API lieferte keinen Key", body
    return True, str(keys[0]), body


def _paypal_block(price: float) -> str:
    email = getattr(config, "PAYPAL_EMAIL", "") or "k1ngf3rdi@gmail.com"
    amount = f"{float(price):.2f} €" if price > 0 else "Betrag laut Staff"
    return (
        f"**PayPal:** `{email}`\n"
        f"**Betrag:** **{amount}**\n"
        f"_Friends & Family / Freunde & Familie — Verwendungszweck: Discord-Name + Pack AI_"
    )


# ── DB ───────────────────────────────────────────────────────────────────

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS packai_settings (
            guild_id INTEGER PRIMARY KEY,
            price_14d REAL NOT NULL DEFAULT 0,
            price_30d REAL NOT NULL DEFAULT 0,
            price_lifetime REAL NOT NULL DEFAULT 0,
            next_key_number INTEGER NOT NULL DEFAULT 1,
            support_role_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS packai_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            key_number INTEGER NOT NULL,
            user_id INTEGER,
            tier TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            license_key TEXT,
            ticket_channel_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        """
    )
    await bot.db.db.commit()


async def _get_settings(bot: "ShopBot", guild_id: int) -> dict:
    row = await bot.db.fetchone(
        "SELECT * FROM packai_settings WHERE guild_id = ?", (guild_id,)
    )
    if row:
        return dict(row)
    defaults = {
        "price_14d": float(getattr(config, "PACKAI_PRICE_14D", 4.99) or 4.99),
        "price_30d": float(getattr(config, "PACKAI_PRICE_30D", 9.99) or 9.99),
        "price_lifetime": float(getattr(config, "PACKAI_PRICE_LIFETIME", 29.99) or 29.99),
    }
    await bot.db.db.execute(
        """
        INSERT INTO packai_settings (guild_id, price_14d, price_30d, price_lifetime)
        VALUES (?, ?, ?, ?)
        """,
        (guild_id, defaults["price_14d"], defaults["price_30d"], defaults["price_lifetime"]),
    )
    await bot.db.db.commit()
    row = await bot.db.fetchone(
        "SELECT * FROM packai_settings WHERE guild_id = ?", (guild_id,)
    )
    return dict(row)  # type: ignore[arg-type]


async def _update_settings(bot: "ShopBot", guild_id: int, **fields: Any) -> None:
    await _get_settings(bot, guild_id)
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [guild_id]
    await bot.db.db.execute(
        f"UPDATE packai_settings SET {cols} WHERE guild_id = ?", values
    )
    await bot.db.db.commit()


async def _next_key_number(bot: "ShopBot", guild_id: int) -> int:
    settings = await _get_settings(bot, guild_id)
    n = int(settings.get("next_key_number") or 1)
    await bot.db.db.execute(
        "UPDATE packai_settings SET next_key_number = ? WHERE guild_id = ?",
        (n + 1, guild_id),
    )
    await bot.db.db.commit()
    return n


def _price_for(settings: dict, tier: str) -> float:
    return float(
        settings.get(
            {"14d": "price_14d", "30d": "price_30d", "lifetime": "price_lifetime"}[tier]
        )
        or 0
    )


async def _count_open_keys(bot: "ShopBot", guild_id: int, user_id: int) -> int:
    row = await bot.db.fetchone(
        """
        SELECT COUNT(*) AS cnt FROM packai_keys
        WHERE guild_id = ? AND user_id = ? AND status = 'pending'
        """,
        (guild_id, user_id),
    )
    return int(row["cnt"]) if row else 0


async def _create_pending_key(
    bot: "ShopBot", guild_id: int, user_id: int, tier: str, note: str, price: float
) -> tuple[int, int]:
    key_number = await _next_key_number(bot, guild_id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO packai_keys (guild_id, key_number, user_id, tier, price, note, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """,
        (guild_id, key_number, user_id, tier, price, note),
    )
    await bot.db.db.commit()
    return int(cur.lastrowid), key_number  # type: ignore[arg-type]


async def _delete_key_row(bot: "ShopBot", key_id: int) -> None:
    await bot.db.db.execute("DELETE FROM packai_keys WHERE id = ?", (key_id,))
    await bot.db.db.commit()


async def _set_ticket_channel(bot: "ShopBot", key_id: int, channel_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE packai_keys SET ticket_channel_id = ? WHERE id = ?",
        (channel_id, key_id),
    )
    await bot.db.db.commit()


async def _get_key_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM packai_keys WHERE ticket_channel_id = ?", (channel_id,)
    )
    return dict(row) if row else None


async def _mark_confirmed(
    bot: "ShopBot", key_id: int, license_key: str, staff_id: int
) -> None:
    await bot.db.db.execute(
        """
        UPDATE packai_keys
        SET status = 'confirmed', license_key = ?, created_by = ?,
            confirmed_at = datetime('now')
        WHERE id = ?
        """,
        (license_key, staff_id, key_id),
    )
    await bot.db.db.commit()
    row = await bot.db.fetchone("SELECT price FROM packai_keys WHERE id = ?", (key_id,))
    if row and float(row["price"] or 0) > 0:
        asyncio.create_task(shop_api.sync_revenue(float(row["price"])))


async def _mark_rejected(bot: "ShopBot", key_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        """
        UPDATE packai_keys
        SET status = 'rejected', created_by = ?, confirmed_at = datetime('now')
        WHERE id = ?
        """,
        (staff_id, key_id),
    )
    await bot.db.db.commit()


async def _resolve_support_role(
    bot: "ShopBot", guild: discord.Guild, settings: Optional[dict] = None
) -> Optional[discord.Role]:
    if settings is None:
        settings = await _get_settings(bot, guild.id)
    role_id = settings.get("support_role_id")
    if role_id:
        role = guild.get_role(int(role_id))
        if role is not None:
            return role
    g = await bot.db.ensure_guild(guild.id)
    staff_role_id = g.get("staff_role_id")
    return guild.get_role(int(staff_role_id)) if staff_role_id else None


async def _is_packai_staff(bot: "ShopBot", interaction: discord.Interaction) -> bool:
    user = interaction.user
    if isinstance(user, discord.Member) and user.guild_permissions.administrator:
        return True
    assert interaction.guild is not None
    settings = await _get_settings(bot, interaction.guild.id)
    role_id = settings.get("support_role_id")
    if role_id and isinstance(user, discord.Member):
        if any(r.id == int(role_id) for r in user.roles):
            return True
    return await is_staff(bot, interaction)


async def _resolve_member(
    guild: discord.Guild, user_id: Optional[int]
) -> Optional[discord.Member]:
    if not user_id:
        return None
    member = guild.get_member(int(user_id))
    if member is not None:
        return member
    try:
        return await guild.fetch_member(int(user_id))
    except discord.HTTPException:
        return None


# ── Panel / Buy ──────────────────────────────────────────────────────────

def _panel_embed(settings: dict) -> discord.Embed:
    lines = []
    for tier in TIER_ORDER:
        price = _price_for(settings, tier)
        price_txt = format_price(price) if price > 0 else "Preis auf Anfrage"
        tokens = TIER_TOKENS[tier]
        lines.append(f"**{TIER_LABELS[tier]}** — {price_txt} · `{tokens}` Tokens")

    email = getattr(config, "PAYPAL_EMAIL", "") or "k1ngf3rdi@gmail.com"
    embed = base_embed(
        "Pack AI — Lizenz & Tokens",
        "Lokales Texturepack-Studio (Prompt / Bild → Pack + Skies).\n"
        "1 Token = 1 Generierung · Key wird an deine HWID gebunden.\n\n"
        "**Pläne:**\n"
        + "\n".join(lines)
        + "\n\n"
        f"**Zahlung per PayPal:** `{email}`\n"
        "_Friends & Family · danach Ticket mit Zahlungsbeweis_\n\n"
        "Klicke **Pack AI kaufen**, wähle einen Plan — privates Ticket öffnet sich.",
    )
    embed.add_field(
        name="PayPal",
        value=f"`{email}`",
        inline=True,
    )
    embed.add_field(
        name="Hinweis",
        value=getattr(config, "PAYMENT_NOTICE", "Das gesamte Geld geht an TxtEmpire."),
        inline=False,
    )
    return embed


async def handle_buy_packai(bot: "ShopBot", interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return
    open_n = await _count_open_keys(bot, interaction.guild.id, interaction.user.id)
    if open_n >= 1:
        await interaction.response.send_message(
            embed=error_embed(
                "Bereits offen",
                "Du hast schon eine offene Pack-AI-Bestellung. Schließe erst das Ticket.",
            ),
            ephemeral=True,
        )
        return
    settings = await _get_settings(bot, interaction.guild.id)
    await interaction.response.send_message(
        embed=base_embed(
            "Pack AI — Plan wählen",
            f"PayPal: `{getattr(config, 'PAYPAL_EMAIL', '') or 'k1ngf3rdi@gmail.com'}`\n"
            "Wähle Laufzeit / Tokens:",
        ),
        view=PackAiTierSelectView(bot, settings),
        ephemeral=True,
    )


class PackAiKeyPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Pack AI kaufen",
        style=discord.ButtonStyle.primary,
        custom_id="packai:buy",
        emoji="🎨",
    )
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_buy_packai(self.bot, interaction)


class PackAiTierSelectView(discord.ui.View):
    def __init__(self, bot: "ShopBot", settings: dict) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        options = []
        for tier in TIER_ORDER:
            price = _price_for(settings, tier)
            price_txt = format_price(price) if price > 0 else "Anfrage"
            options.append(
                discord.SelectOption(
                    label=TIER_LABELS[tier][:100],
                    value=tier,
                    description=price_txt[:100],
                )
            )
        select = discord.ui.Select(
            placeholder="Plan wählen…",
            options=options,
            custom_id="packai:tier",
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.children[0], discord.ui.Select)
        tier = self.children[0].values[0]
        await open_packai_ticket(self.bot, interaction, tier, note="")


async def open_packai_ticket(
    bot: "ShopBot",
    interaction: discord.Interaction,
    tier: str,
    note: str,
) -> None:
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    settings = await bot.db.ensure_guild(guild.id)
    pa_settings = await _get_settings(bot, guild.id)
    price = _price_for(pa_settings, tier)

    category_id = settings.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None
    staff_role = await _resolve_support_role(bot, guild, pa_settings)
    me = guild.me
    if me is None:
        await interaction.followup.send(
            embed=error_embed("Bot-Mitgliedschaft fehlt"), ephemeral=True
        )
        return

    bot_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        manage_channels=True,
        manage_messages=True,
    )
    buyer_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        attach_files=True,
        embed_links=True,
        read_message_history=True,
    )
    staff_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        attach_files=True,
        embed_links=True,
        read_message_history=True,
        manage_messages=True,
    )
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: bot_perms,
    }
    if isinstance(interaction.user, discord.Member):
        overwrites[interaction.user] = buyer_perms
    if staff_role:
        overwrites[staff_role] = staff_perms

    key_id, key_number = await _create_pending_key(
        bot, guild.id, interaction.user.id, tier, note, price
    )

    safe = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower()
    )[:18]
    name = f"packai-{key_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason=f"Pack-AI-Key-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await _delete_key_row(bot, key_id)
        await interaction.followup.send(
            embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True
        )
        return

    await _set_ticket_channel(bot, key_id, channel.id)

    price_txt = format_price(price) if price > 0 else "Preis auf Anfrage"
    embed = base_embed(
        f"🎨 Pack AI Ticket #{key_number}",
        f"Käufer: {interaction.user.mention}\n"
        f"Plan: **{TIER_LABELS[tier]}**\n"
        f"Preis: **{price_txt}**\n"
        + (f"Notiz: {note}\n" if note else "")
        + f"\n**{getattr(config, 'PAYMENT_NOTICE', 'Das gesamte Geld geht an TxtEmpire.')}**\n\n"
        f"{_paypal_block(price)}\n\n"
        f"Weitere Zahlung an **{payee_name(settings)}**:\n"
        f"{payee_details_text(settings) or '_Keine weiteren Details_'}\n\n"
        "Nach Zahlung: Beleg hier posten. Staff klickt **✅ Bestätigen** — "
        "Key kommt per DM (in Pack AI unter Aktivieren einfügen, HWID-Bindung).",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(
        content=f"{interaction.user.mention} {mention}",
        embed=embed,
        view=PackAiKeyTicketView(bot),
    )
    await interaction.followup.send(
        embed=success_embed("Ticket erstellt", f"Dein Ticket: {channel.mention}"),
        ephemeral=True,
    )


class PackAiKeyTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Bestätigen",
        style=discord.ButtonStyle.success,
        custom_id="packai:confirm",
        emoji="✅",
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            return
        if not await _is_packai_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        row = await _get_key_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Kein Pack-AI-Ticket"), ephemeral=True
            )
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"),
                ephemeral=True,
            )
            return
        if not _api_configured():
            await interaction.response.send_message(
                embed=error_embed(
                    "API nicht eingerichtet",
                    "PACKAI_LICENSE_API_SECRET / PACKAI_LICENSE_API_URL in .env setzen "
                    "und License-Server starten.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer()

        ok, key_or_err, _ = await asyncio.to_thread(
            _create_license_key,
            row["tier"],
            f"{interaction.user} ({interaction.user.id})",
            f"ticket:{row['key_number']} buyer:{row.get('user_id')}",
        )
        if not ok:
            await interaction.followup.send(
                embed=error_embed("Key-API Fehler", key_or_err[:900]),
            )
            return

        license_key = key_or_err
        await _mark_confirmed(self.bot, int(row["id"]), license_key, interaction.user.id)

        buyer = await _resolve_member(interaction.guild, row.get("user_id"))
        dm_ok = True
        if buyer is not None:
            try:
                await buyer.send(
                    embed=success_embed(
                        "🎨 Dein Pack AI License Key",
                        f"Plan: **{TIER_LABELS[row['tier']]}**\n\n"
                        f"```\n{license_key}\n```\n"
                        "In **Pack AI** unter Lizenzkey einfügen → **Aktivieren**.\n"
                        "Der Key wird an deine Hardware (HWID) gebunden.",
                    )
                )
            except discord.HTTPException:
                dm_ok = False

        await interaction.followup.send(
            embed=success_embed(
                "Key ausgestellt",
                f"Plan **{TIER_LABELS[row['tier']]}**\n"
                f"```\n{license_key}\n```\n"
                + ("DM an Käufer gesendet." if dm_ok else "⚠️ DM fehlgeschlagen — Key oben kopieren."),
            )
        )
        if buyer is not None:
            try:
                await tweak_vouch.request_vouch(
                    self.bot,
                    interaction.guild,
                    buyer,
                    product="Pack AI",
                    tier_label=TIER_LABELS[row["tier"]],
                )
            except Exception:
                pass

    @discord.ui.button(
        label="Ablehnen",
        style=discord.ButtonStyle.danger,
        custom_id="packai:reject",
        emoji="✖️",
    )
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            return
        if not await _is_packai_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        row = await _get_key_by_channel(self.bot, interaction.channel_id)
        if not row or row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Nicht offen"), ephemeral=True
            )
            return
        await _mark_rejected(self.bot, int(row["id"]), interaction.user.id)
        await interaction.response.send_message(
            embed=warn_embed("Abgelehnt", f"Ticket #{row['key_number']} abgelehnt."),
        )


# ── Cog ──────────────────────────────────────────────────────────────────

class PackAiKeysCog(commands.Cog):
    """Slash: /packai panel|setup|gen|plans|buy"""

    packai = app_commands.Group(
        name="packai",
        description="Pack AI Lizenzkeys & Kauf-Panel",
    )

    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    @packai.command(name="plans", description="Zeigt Pack-AI Pläne, Tokens und PayPal")
    async def plans(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await _get_settings(self.bot, interaction.guild.id)
        lines = []
        for tier in TIER_ORDER:
            price = _price_for(settings, tier)
            price_txt = format_price(price) if price > 0 else "Anfrage"
            lines.append(f"**{TIER_LABELS[tier]}** — {price_txt}")
        email = getattr(config, "PAYPAL_EMAIL", "") or "—"
        await interaction.response.send_message(
            embed=base_embed(
                "Pack AI Pläne",
                "\n".join(lines)
                + f"\n\n**PayPal:** `{email}`\n"
                "_Friends & Family · danach Ticket / Staff bestätigt Key_",
            ),
            ephemeral=True,
        )

    @packai.command(name="buy", description="Pack AI kaufen (öffnet Plan-Auswahl)")
    async def buy(self, interaction: discord.Interaction) -> None:
        await handle_buy_packai(self.bot, interaction)

    @packai.command(name="setup", description="Preise für Pack-AI-Keys setzen (Staff)")
    @app_commands.describe(
        price_14d="Preis 14 Tage / 50 Tokens",
        price_30d="Preis 30 Tage / 200 Tokens",
        price_lifetime="Preis Lifetime / 2000 Tokens",
        support_role="Eigene Support-Rolle (optional)",
        clear_support_role="Support-Rolle entfernen",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup_cmd(
        self,
        interaction: discord.Interaction,
        price_14d: Optional[float] = None,
        price_30d: Optional[float] = None,
        price_lifetime: Optional[float] = None,
        support_role: discord.Role | None = None,
        clear_support_role: bool = False,
    ) -> None:
        assert interaction.guild is not None
        if not await _is_packai_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        fields: dict[str, Any] = {}
        if price_14d is not None:
            fields["price_14d"] = price_14d
        if price_30d is not None:
            fields["price_30d"] = price_30d
        if price_lifetime is not None:
            fields["price_lifetime"] = price_lifetime
        if clear_support_role:
            fields["support_role_id"] = None
        elif support_role is not None:
            fields["support_role_id"] = support_role.id
        if fields:
            await _update_settings(self.bot, interaction.guild.id, **fields)
        settings = await _get_settings(self.bot, interaction.guild.id)
        email = getattr(config, "PAYPAL_EMAIL", "") or "—"
        await interaction.response.send_message(
            embed=success_embed(
                "Pack AI Einstellungen",
                f"14d (50 Tokens): **{format_price(_price_for(settings, TIER_14D))}**\n"
                f"30d (200 Tokens): **{format_price(_price_for(settings, TIER_30D))}**\n"
                f"Lifetime (2000): **{format_price(_price_for(settings, TIER_LIFETIME))}**\n\n"
                f"PayPal: `{email}`\n"
                f"API: `{_api_base()}` · "
                + ("✅ Secret gesetzt" if _api_configured() else "⚠️ Secret fehlt"),
            ),
            ephemeral=True,
        )

    @packai.command(name="panel", description="Kauf-Panel für Pack AI posten (Staff)")
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        if not await _is_packai_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed("Kein Channel"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        settings = await _get_settings(self.bot, interaction.guild.id)
        msg = await target.send(
            embed=_panel_embed(settings), view=PackAiKeyPanelView(self.bot)
        )
        await interaction.followup.send(
            embed=success_embed(
                "Pack-AI-Panel gepostet", f"In {target.mention}: {msg.jump_url}"
            ),
            ephemeral=True,
        )

    @packai.command(name="gen", description="Pack-AI-Key sofort erzeugen (Staff)")
    @app_commands.describe(
        plan="14d / 30d / lifetime",
        user="Optional: Key per DM senden",
        note="Notiz",
    )
    @app_commands.choices(
        plan=[
            app_commands.Choice(name="14 Tage (50 Tokens)", value="14d"),
            app_commands.Choice(name="30 Tage (200 Tokens)", value="30d"),
            app_commands.Choice(name="Lifetime (2000 Tokens)", value="lifetime"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def gen(
        self,
        interaction: discord.Interaction,
        plan: app_commands.Choice[str],
        user: discord.User | None = None,
        note: str = "",
    ) -> None:
        if not await _is_packai_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        if not _api_configured():
            await interaction.response.send_message(
                embed=error_embed(
                    "API fehlt",
                    "In `.env` setzen:\n"
                    "`PACKAI_LICENSE_API_URL`\n"
                    "`PACKAI_LICENSE_API_SECRET`\n"
                    "Dann License-Server starten & Bot neu starten.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        ok, key_or_err, _ = await asyncio.to_thread(
            _create_license_key,
            plan.value,
            f"{interaction.user} ({interaction.user.id})",
            note or (f"dm:{user.id}" if user else ""),
        )
        if not ok:
            await interaction.followup.send(
                embed=error_embed("API Fehler", key_or_err[:900]), ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "Pack AI Key",
                f"**{plan.name}**\n```\n{key_or_err}\n```",
            ),
            ephemeral=True,
        )
        if user:
            try:
                await user.send(
                    embed=success_embed(
                        "Dein Pack AI License Key",
                        f"Plan: **{plan.name}**\n```\n{key_or_err}\n```\n"
                        "In Pack AI → Aktivieren (HWID-Bindung).",
                    )
                )
            except discord.HTTPException:
                await interaction.followup.send(
                    embed=warn_embed("DM fehlgeschlagen", user.mention), ephemeral=True
                )

    @packai.command(name="status", description="Prüft Pack-AI License-API")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            r = await asyncio.to_thread(
                lambda: __import__("requests").get(f"{_api_base()}/health", timeout=8)
            )
            body = r.text[:400]
            code = r.status_code
        except Exception as e:
            await interaction.followup.send(
                embed=error_embed("Offline", f"`{_api_base()}`\n{e}"),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "License-API",
                f"URL: `{_api_base()}`\nSecret: "
                + ("✅ gesetzt" if _api_configured() else "⚠️ fehlt")
                + f"\nStatus: `{code}`\n```{body}```",
            )
            if code == 200
            else error_embed("API Fehler", f"`{code}`\n```{body}```"),
            ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(PackAiKeysCog(bot))
    print(
        "[PackAI] Cog geladen — Slash: /packai plans|buy|panel|setup|gen|status"
    )
