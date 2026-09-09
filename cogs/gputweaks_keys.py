"""
gputweaks_keys.py
====================

Verkauf von Lizenzkeys für "y3zz GPU Tweaks" direkt über diesen Bot -
Aufbau 1:1 wie cogs/mousetweaks_keys.py, nur eigenständige Tabellen,
Preise und Custom-IDs (getrenntes Produkt, getrennter Lizenz-Secret):

  - Kunde klickt auf einem Panel "Key kaufen" -> wählt Laufzeit
    (14 Tage / 30 Tage / Lifetime) -> privates Ticket wird erstellt
    (fortlaufend nummeriert: Key-Ticket #1, #2, ...). Keine Hardware-ID
    noetig - der Key ist noch an KEIN Geraet gebunden.
  - Staff bestätigt im Ticket mit einem Klick ("✅ Bestätigen") ->
    der Bot erzeugt automatisch einen gültigen, noch unadressierten
    Lizenzkey und schickt ihn dem Kunden per DM. Die App bindet ihn
    automatisch an das Geraet des Kunden, sobald er ihn dort zum
    ersten Mal eintraegt. Kein manueller Schritt außer dem einen
    Klick nötig.
  - Alternativ: `/gtkey generate` erzeugt (für Staff) sofort einen
    gültigen Key ohne Ticket - z.B. wenn die Zahlung schon anderswo
    (Ticket, Überweisung, persönlich) bestätigt wurde.
  - Nach jeder Bestätigung wird der Kunde per DM eingeladen, im
    gemeinsamen Tweak-Vouch-Kanal (siehe /tweakvouchsetup) einen
    Vouch für genau diesen (den neuesten) Kauf zu hinterlassen.

Braucht: GPUTWEAKS_LICENSE_SECRET in .env (siehe .env.example) -
muss exakt mit LICENSE_SECRET in licensing.py der y3zz-GPU-Tweaks-App
übereinstimmen.
Eigene Tabellen (gt_settings, gt_keys) - keine Änderung an db/database.py
nötig.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import gputweaks_licensing as gtlic
from utils import tweak_vouch
from utils.revenue_sync import sync_revenue_now
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

TIER_ORDER = (gtlic.TIER_14D, gtlic.TIER_30D, gtlic.TIER_LIFETIME)


# ── DB Bootstrap & Helpers (eigene Tabellen, kein Eingriff in database.py) ──

async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS gt_settings (
            guild_id INTEGER PRIMARY KEY,
            price_14d REAL NOT NULL DEFAULT 0,
            price_30d REAL NOT NULL DEFAULT 0,
            price_lifetime REAL NOT NULL DEFAULT 0,
            next_key_number INTEGER NOT NULL DEFAULT 1,
            support_role_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS gt_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            key_number INTEGER NOT NULL,
            user_id INTEGER,
            hwid TEXT NOT NULL DEFAULT '',
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
        "SELECT * FROM gt_settings WHERE guild_id = ?", (guild_id,)
    )
    if row:
        return dict(row)
    defaults = {
        "price_14d": config.GPUTWEAKS_PRICE_14D,
        "price_30d": config.GPUTWEAKS_PRICE_30D,
        "price_lifetime": config.GPUTWEAKS_PRICE_LIFETIME,
    }
    await bot.db.db.execute(
        """
        INSERT INTO gt_settings (guild_id, price_14d, price_30d, price_lifetime)
        VALUES (?, ?, ?, ?)
        """,
        (guild_id, defaults["price_14d"], defaults["price_30d"], defaults["price_lifetime"]),
    )
    await bot.db.db.commit()
    row = await bot.db.fetchone(
        "SELECT * FROM gt_settings WHERE guild_id = ?", (guild_id,)
    )
    return dict(row)  # type: ignore[arg-type]


async def _update_settings(bot: "ShopBot", guild_id: int, **fields) -> None:
    await _get_settings(bot, guild_id)
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [guild_id]
    await bot.db.db.execute(
        f"UPDATE gt_settings SET {cols} WHERE guild_id = ?", values
    )
    await bot.db.db.commit()


async def _next_key_number(bot: "ShopBot", guild_id: int) -> int:
    settings = await _get_settings(bot, guild_id)
    n = int(settings.get("next_key_number") or 1)
    await bot.db.db.execute(
        "UPDATE gt_settings SET next_key_number = ? WHERE guild_id = ?",
        (n + 1, guild_id),
    )
    await bot.db.db.commit()
    return n


def _price_for(settings: dict, tier: str) -> float:
    return float(settings.get({"14d": "price_14d", "30d": "price_30d", "lifetime": "price_lifetime"}[tier]) or 0)


async def _count_open_keys(bot: "ShopBot", guild_id: int, user_id: int) -> int:
    row = await bot.db.fetchone(
        """
        SELECT COUNT(*) AS cnt FROM gt_keys
        WHERE guild_id = ? AND user_id = ? AND status = 'pending'
        """,
        (guild_id, user_id),
    )
    return int(row["cnt"]) if row else 0


async def _create_pending_key(
    bot: "ShopBot", guild_id: int, user_id: int, tier: str, hwid: str, note: str, price: float
) -> tuple[int, int]:
    key_number = await _next_key_number(bot, guild_id)
    cur = await bot.db.db.execute(
        """
        INSERT INTO gt_keys (guild_id, key_number, user_id, hwid, tier, price, note, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (guild_id, key_number, user_id, hwid, tier, price, note),
    )
    await bot.db.db.commit()
    return int(cur.lastrowid), key_number  # type: ignore[arg-type]


async def _get_key_by_channel(bot: "ShopBot", channel_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM gt_keys WHERE ticket_channel_id = ?", (channel_id,)
    )
    return dict(row) if row else None


async def _get_key_by_id(bot: "ShopBot", key_id: int) -> Optional[dict]:
    row = await bot.db.fetchone("SELECT * FROM gt_keys WHERE id = ?", (key_id,))
    return dict(row) if row else None


async def _get_latest_confirmed_key(bot: "ShopBot", guild_id: int, user_id: int) -> Optional[dict]:
    """Fuer den Self-Service HWID-Reset: der zuletzt bestaetigte (bezahlte)
    Kauf dieses Kunden - nur wer schonmal einen Key bestaetigt bekommen hat,
    darf sich selbst einen neuen, unadressierten Ersatzkey ausstellen."""
    row = await bot.db.fetchone(
        """
        SELECT * FROM gt_keys
        WHERE guild_id = ? AND user_id = ? AND status = 'confirmed'
        ORDER BY id DESC LIMIT 1
        """,
        (guild_id, user_id),
    )
    return dict(row) if row else None


async def _delete_key_row(bot: "ShopBot", key_id: int) -> None:
    await bot.db.db.execute("DELETE FROM gt_keys WHERE id = ?", (key_id,))
    await bot.db.db.commit()


async def _set_ticket_channel(bot: "ShopBot", key_id: int, channel_id: int) -> None:
    await bot.db.db.execute(
        "UPDATE gt_keys SET ticket_channel_id = ? WHERE id = ?", (channel_id, key_id)
    )
    await bot.db.db.commit()


async def _mark_confirmed(bot: "ShopBot", key_id: int, license_key: str, staff_id: int) -> None:
    await bot.db.db.execute(
        """
        UPDATE gt_keys
        SET status = 'confirmed', license_key = ?, created_by = ?,
            confirmed_at = datetime('now')
        WHERE id = ?
        """,
        (license_key, staff_id, key_id),
    )
    await bot.db.db.commit()
    asyncio.create_task(sync_revenue_now(bot))


async def _mark_rejected(bot: "ShopBot", key_id: int, staff_id: int) -> None:
    await bot.db.db.execute(
        """
        UPDATE gt_keys
        SET status = 'rejected', created_by = ?, confirmed_at = datetime('now')
        WHERE id = ?
        """,
        (staff_id, key_id),
    )
    await bot.db.db.commit()


async def _insert_direct_key(
    bot: "ShopBot",
    guild_id: int,
    user_id: Optional[int],
    tier: str,
    hwid: str,
    note: str,
    license_key: str,
    staff_id: int,
) -> int:
    key_number = await _next_key_number(bot, guild_id)
    await bot.db.db.execute(
        """
        INSERT INTO gt_keys
          (guild_id, key_number, user_id, hwid, tier, price, note,
           status, license_key, created_by, confirmed_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, 'confirmed', ?, ?, datetime('now'))
        """,
        (guild_id, key_number, user_id, hwid, tier, note, license_key, staff_id),
    )
    await bot.db.db.commit()
    return key_number


async def _list_recent_keys(bot: "ShopBot", guild_id: int, limit: int = 15) -> list[dict]:
    rows = await bot.db.fetchall(
        """
        SELECT * FROM gt_keys WHERE guild_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (guild_id, limit),
    )
    return [dict(r) for r in rows]


# ── UI: Panel, Tier-Auswahl, HWID-Modal, Ticket-Buttons ─────────────────

def _panel_embed(settings: dict) -> discord.Embed:
    lines = []
    for tier in TIER_ORDER:
        price = _price_for(settings, tier)
        price_txt = format_price(price) if price > 0 else "Preis auf Anfrage"
        lines.append(f"**{gtlic.TIER_LABELS[tier]}** — {price_txt}")
    embed = base_embed(
        "🖥️ y3zz GPU Tweaks — Lizenzkey",
        "GPU-Optimierer (Deep Scan Auto-Tune, Power/Takt/Lüfter, Farbtiefe, "
        "Kantenschärfung, Schattenqualität) mit 80°C-Sicherheitsgrenze.\n\n"
        "Verfügbare Laufzeiten:\n" + "\n".join(lines) + "\n\n"
        "Klicke **Key kaufen** und wähle eine Laufzeit — danach wird ein "
        "privates Ticket erstellt. Keine Hardware-ID nötig: der Key bindet "
        "sich automatisch an dein Gerät, sobald du ihn in der App einträgst.",
    )
    return embed


async def handle_buy_key(bot: "ShopBot", interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return
    if not gtlic.licensing_configured():
        await interaction.response.send_message(
            embed=error_embed(
                "Noch nicht eingerichtet",
                "GPUTWEAKS_LICENSE_SECRET ist noch nicht gesetzt (Staff).",
            ),
            ephemeral=True,
        )
        return
    open_n = await _count_open_keys(bot, interaction.guild.id, interaction.user.id)
    if open_n >= 1:
        await interaction.response.send_message(
            embed=error_embed(
                "Bereits offen",
                "Du hast schon eine offene Key-Bestellung. "
                "Schließe erst dieses Ticket.",
            ),
            ephemeral=True,
        )
        return
    settings = await _get_settings(bot, interaction.guild.id)
    await interaction.response.send_message(
        embed=base_embed("Laufzeit wählen", "Für welche Laufzeit möchtest du einen Key?"),
        view=TierSelectView(bot, settings),
        ephemeral=True,
    )


async def handle_reset_hwid(bot: "ShopBot", interaction: discord.Interaction) -> None:
    """Self-Service: nur fuer Kunden mit mindestens einem bestaetigten
    Kauf. Discord kann Buttons auf einer geteilten Panel-Nachricht nicht
    pro Nutzer aus-/einblenden - der Button ist fuer alle sichtbar, aber
    bei jedem ohne bestaetigten Kauf antwortet er nur privat mit einem
    Hinweis, statt etwas auszuloesen.

    Stellt einen NEUEN, unadressierten Ersatzkey aus (gleiche Laufzeit/
    Ablaufdatum wie der urspruengliche Kauf) - der bindet sich beim
    naechsten Eintragen automatisch an das dann genutzte Geraet. Der
    alte, bereits gebundene Key bleibt auf seinem bisherigen Geraet
    weiter gueltig."""
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return
    if not gtlic.licensing_configured():
        await interaction.response.send_message(
            embed=error_embed(
                "Noch nicht eingerichtet",
                "GPUTWEAKS_LICENSE_SECRET ist noch nicht gesetzt (Staff).",
            ),
            ephemeral=True,
        )
        return
    row = await _get_latest_confirmed_key(bot, interaction.guild.id, interaction.user.id)
    if not row:
        await interaction.response.send_message(
            embed=error_embed(
                "Kein Kauf gefunden",
                "Dieser Button ist nur für Kunden mit einem bereits bestätigten "
                "Key gedacht. Kauf zuerst einen über **Key kaufen**.",
            ),
            ephemeral=True,
        )
        return
    await interaction.response.defer(ephemeral=True)

    # Urspruengliches Ausstellungsdatum uebernehmen (nicht "jetzt") - ein
    # Reset soll die Laufzeit nicht verlaengern, da der Ablauf aus
    # Tier+issued berechnet wird.
    issued = None
    if row.get("license_key"):
        ok, old_payload, _err = gtlic.verify_own_key(row["license_key"])
        if ok and old_payload:
            issued = old_payload.get("issued")

    new_key = gtlic.generate_license_key(None, tier=row["tier"], issued=issued)
    expires = gtlic.tier_to_expiry(row["tier"], issued)
    await _insert_direct_key(
        bot, interaction.guild.id, interaction.user.id, row["tier"],
        "", row.get("note") or "", new_key, interaction.user.id,
    )

    try:
        await interaction.user.send(
            embed=success_embed(
                "🔄 Neuer y3zz GPU Tweaks Key (HWID-Reset)",
                f"Laufzeit: **{gtlic.describe_tier(row['tier'], expires)}**\n\n"
                f"```\n{new_key}\n```\n"
                "In der App unter **Lizenzkey einfügen** eintragen - bindet sich "
                "automatisch an dieses Gerät. Der alte Key funktioniert auf einem "
                "bereits aktivierten Gerät weiterhin, ist danach aber nicht mehr "
                "auf einem weiteren Gerät nutzbar.",
            )
        )
        await interaction.followup.send(
            embed=success_embed("Neuer Key verschickt", "Schau in deine DMs."),
            ephemeral=True,
        )
    except discord.HTTPException:
        await interaction.followup.send(
            embed=error_embed(
                "DM fehlgeschlagen",
                "Bitte aktiviere DMs von Servermitgliedern (Datenschutzeinstellungen) "
                "und klicke erneut.",
            ),
            ephemeral=True,
        )


class GputweaksKeyPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Key kaufen",
        style=discord.ButtonStyle.success,
        custom_id="gputweaks:buy_key",
        emoji="🔑",
    )
    async def buy_key(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await handle_buy_key(self.bot, interaction)

    @discord.ui.button(
        label="HWID zurücksetzen",
        style=discord.ButtonStyle.secondary,
        custom_id="gputweaks:reset_hwid",
        emoji="🔄",
    )
    async def reset_hwid(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await handle_reset_hwid(self.bot, interaction)


class TierSelect(discord.ui.Select):
    def __init__(self, bot: "ShopBot", settings: dict) -> None:
        self.bot = bot
        options = []
        for tier in TIER_ORDER:
            price = _price_for(settings, tier)
            price_txt = format_price(price) if price > 0 else "Preis auf Anfrage"
            options.append(
                discord.SelectOption(
                    label=gtlic.TIER_LABELS[tier],
                    value=tier,
                    description=price_txt,
                )
            )
        super().__init__(
            placeholder="Laufzeit auswählen…", options=options, min_values=1, max_values=1
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        tier = self.values[0]
        # Keine Hardware-ID mehr beim Kauf abfragen - der Key wird sofort
        # unadressiert ausgegeben und bindet sich automatisch an das Geraet
        # des Kunden, sobald er ihn zum ersten Mal in der App eintraegt.
        await _create_key_ticket_channel(self.bot, interaction, tier=tier, hwid="", note="")


class TierSelectView(discord.ui.View):
    def __init__(self, bot: "ShopBot", settings: dict) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.add_item(TierSelect(bot, settings))


async def _create_key_ticket_channel(
    bot: "ShopBot",
    interaction: discord.Interaction,
    *,
    tier: str,
    hwid: str,
    note: str,
) -> None:
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    settings = await bot.db.ensure_guild(guild.id)
    gt_settings = await _get_settings(bot, guild.id)
    price = _price_for(gt_settings, tier)

    category_id = settings.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None
    staff_role = await _resolve_support_role(bot, guild, gt_settings)
    me = guild.me
    if me is None:
        await interaction.followup.send(
            embed=error_embed("Bot-Mitgliedschaft fehlt"), ephemeral=True
        )
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

    key_id, key_number = await _create_pending_key(
        bot, guild.id, interaction.user.id, tier, hwid, note, price
    )

    safe = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in interaction.user.name.lower()
    )[:18]
    name = f"gtkey-{key_number:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason=f"GPU-Tweaks-Key-Ticket von {interaction.user}",
        )
    except discord.HTTPException as e:
        await _delete_key_row(bot, key_id)
        await interaction.followup.send(
            embed=error_embed("Channel fehlgeschlagen", str(e)[:400]), ephemeral=True
        )
        return

    await _set_ticket_channel(bot, key_id, channel.id)

    price_txt = format_price(price) if price > 0 else "Preis auf Anfrage — Staff nennt dir den Betrag"
    embed = base_embed(
        f"🔑 Key-Ticket #{key_number}",
        f"Käufer: {interaction.user.mention}\n"
        f"Laufzeit: **{gtlic.TIER_LABELS[tier]}**\n"
        f"Preis: **{price_txt}**\n"
        + (f"Hardware-ID: `{hwid}`\n" if hwid else "Noch nicht an ein Gerät gebunden - bindet sich automatisch.\n")
        + (f"Notiz: {note}\n" if note else "")
        + f"\n**{config.PAYMENT_NOTICE}**\n"
        f"Zahlung an **{payee_name(settings)}**:\n{payee_details_text(settings) or '_Keine Details hinterlegt_'}\n\n"
        "Sobald die Zahlung eingegangen ist, klickt Staff **✅ Bestätigen** — "
        "der Key wird automatisch erzeugt und dir per DM geschickt.",
    )
    mention = staff_role.mention if staff_role else "Staff"
    await channel.send(
        content=f"{interaction.user.mention} {mention}",
        embed=embed,
        view=GputweaksKeyTicketView(bot),
    )
    await interaction.followup.send(
        embed=success_embed("Ticket erstellt", f"Dein Ticket: {channel.mention}"),
        ephemeral=True,
    )


async def _resolve_support_role(
    bot: "ShopBot", guild: discord.Guild, gt_settings: Optional[dict] = None
) -> Optional[discord.Role]:
    """Eigene GPU-Tweaks-Support-Rolle, falls gesetzt (/gtkeysetup support_role:@...)
    - sonst Fallback auf die normale Shop-Staff-Rolle (/setup)."""
    if gt_settings is None:
        gt_settings = await _get_settings(bot, guild.id)
    role_id = gt_settings.get("support_role_id")
    if role_id:
        role = guild.get_role(int(role_id))
        if role is not None:
            return role
    settings = await bot.db.ensure_guild(guild.id)
    staff_role_id = settings.get("staff_role_id")
    return guild.get_role(int(staff_role_id)) if staff_role_id else None


async def _is_gputweaks_staff(bot: "ShopBot", interaction: discord.Interaction) -> bool:
    """Wie is_staff(), aber die eigene GPU-Tweaks-Support-Rolle zählt
    zusätzlich zur normalen Shop-Staff-Rolle."""
    user = interaction.user
    if isinstance(user, discord.Member) and user.guild_permissions.administrator:
        return True
    assert interaction.guild is not None
    gt_settings = await _get_settings(bot, interaction.guild.id)
    role_id = gt_settings.get("support_role_id")
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


class GputweaksKeyTicketView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Bestätigen",
        style=discord.ButtonStyle.success,
        custom_id="gtkey:confirm",
        emoji="✅",
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            return
        if not await _is_gputweaks_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        row = await _get_key_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Kein Key-Ticket"), ephemeral=True
            )
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"),
                ephemeral=True,
            )
            return
        if not gtlic.licensing_configured():
            await interaction.response.send_message(
                embed=error_embed(
                    "Nicht eingerichtet", "GPUTWEAKS_LICENSE_SECRET fehlt in .env."
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer()

        license_key = gtlic.generate_license_key(row["hwid"] or None, tier=row["tier"])
        expires = gtlic.tier_to_expiry(row["tier"])
        await _mark_confirmed(self.bot, int(row["id"]), license_key, interaction.user.id)

        buyer = await _resolve_member(interaction.guild, row.get("user_id"))
        dm_ok = True
        if buyer is not None:
            try:
                await buyer.send(
                    embed=success_embed(
                        "🔑 Dein y3zz GPU Tweaks Key",
                        f"Laufzeit: **{gtlic.describe_tier(row['tier'], expires)}**\n\n"
                        f"```\n{license_key}\n```\n"
                        "In der App unter **Lizenzkey einfügen** eintragen. "
                        "Der Key bindet sich beim ersten Eintragen automatisch "
                        "an dein Gerät - danach funktioniert er nur noch dort.",
                    )
                )
            except discord.HTTPException:
                dm_ok = False
            await tweak_vouch.request_vouch(
                self.bot, interaction.guild, buyer,
                product="y3zz GPU Tweaks",
                tier_label=gtlic.describe_tier(row["tier"], expires),
            )

        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

        body = f"Bestätigt von {interaction.user.mention}.\n```\n{license_key}\n```"
        if not dm_ok:
            body += "\n⚠️ DM an Käufer fehlgeschlagen (DMs geschlossen) — Key oben manuell weitergeben."
        await interaction.followup.send(embed=success_embed("Key erzeugt", body))

    @discord.ui.button(
        label="Ablehnen",
        style=discord.ButtonStyle.danger,
        custom_id="gtkey:reject",
        emoji="❌",
    )
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            return
        if not await _is_gputweaks_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Nur Staff"), ephemeral=True
            )
            return
        row = await _get_key_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Kein Key-Ticket"), ephemeral=True
            )
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                embed=error_embed("Bereits bearbeitet", f"Status: `{row['status']}`"),
                ephemeral=True,
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
                        "Deine GPU-Tweaks-Key-Bestellung wurde abgelehnt "
                        "(z.B. keine Zahlung erkannt). Melde dich im Ticket "
                        "für Rückfragen.",
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
        await interaction.followup.send(
            embed=warn_embed("Abgelehnt", f"Abgelehnt von {interaction.user.mention}.")
        )

    @discord.ui.button(
        label="Schließen",
        style=discord.ButtonStyle.secondary,
        custom_id="gtkey:close",
        emoji="🔒",
    )
    async def close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None or not isinstance(
            interaction.channel, discord.TextChannel
        ):
            return
        row = await _get_key_by_channel(self.bot, interaction.channel_id)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Kein Key-Ticket"), ephemeral=True
            )
            return
        staff = await _is_gputweaks_staff(self.bot, interaction)
        is_owner = row.get("user_id") and interaction.user.id == int(row["user_id"])
        if not staff and not is_owner:
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        await interaction.response.defer()
        if row["status"] == "pending":
            # Ohne Entscheidung geschlossen -> Zähler für "offene Bestellung"
            # freigeben, sonst kann der Kunde nie wieder ein Ticket öffnen.
            await _mark_rejected(self.bot, int(row["id"]), interaction.user.id)
        await interaction.followup.send(
            embed=warn_embed(
                "Ticket wird geschlossen",
                f"Geschlossen von {interaction.user.mention}. Channel wird in 5 Sekunden gelöscht.",
            )
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Key-Ticket geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass


# ── Slash-Commands ───────────────────────────────────────────────────────

class GputweaksKeysCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="gtkeysetup",
        description="Preise für y3zz-GPU-Tweaks-Keys setzen (Staff)",
    )
    @app_commands.describe(
        price_14d="Preis für 14 Tage (leer = unverändert)",
        price_30d="Preis für 30 Tage (leer = unverändert)",
        price_lifetime="Preis für Lifetime (leer = unverändert)",
        support_role=(
            "Eigene Support-Rolle für GPU-Tweaks-Ticket (sieht Tickets, darf "
            "bestätigen/ablehnen). Leer = weiter unverändert."
        ),
        clear_support_role="Eigene Support-Rolle entfernen (Fallback: normale Shop-Staff-Rolle)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def gtkeysetup(
        self,
        interaction: discord.Interaction,
        price_14d: Optional[float] = None,
        price_30d: Optional[float] = None,
        price_lifetime: Optional[float] = None,
        support_role: discord.Role | None = None,
        clear_support_role: bool = False,
    ) -> None:
        assert interaction.guild is not None
        fields = {}
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
        secret_ok = gtlic.licensing_configured()

        role_id = settings.get("support_role_id")
        role = interaction.guild.get_role(int(role_id)) if role_id else None
        role_line = (
            f"Support-Rolle: {role.mention}"
            if role
            else "Support-Rolle: **nicht gesetzt** (Fallback: normale Shop-Staff-Rolle aus `/setup`)"
        )

        await interaction.response.send_message(
            embed=success_embed(
                "GPU-Tweaks Key-Einstellungen",
                f"14 Tage: **{format_price(_price_for(settings, gtlic.TIER_14D))}**\n"
                f"30 Tage: **{format_price(_price_for(settings, gtlic.TIER_30D))}**\n"
                f"Lifetime: **{format_price(_price_for(settings, gtlic.TIER_LIFETIME))}**\n"
                f"{role_line}\n\n"
                + (
                    "✅ GPUTWEAKS_LICENSE_SECRET ist gesetzt."
                    if secret_ok
                    else "⚠️ GPUTWEAKS_LICENSE_SECRET fehlt noch in der .env "
                    "(muss mit LICENSE_SECRET in licensing.py der App übereinstimmen)."
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="gtkeypanel",
        description="Kauf-Panel für y3zz-GPU-Tweaks-Keys posten (Staff)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def gtkeypanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
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
            embed=_panel_embed(settings), view=GputweaksKeyPanelView(self.bot)
        )
        await interaction.followup.send(
            embed=success_embed("Key-Panel gepostet", f"In {target.mention}: {msg.jump_url}"),
            ephemeral=True,
        )

    gtkey = app_commands.Group(
        name="gtkey",
        description="y3zz-GPU-Tweaks-Lizenzkeys verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @gtkey.command(name="generate", description="Sofort einen gültigen Key erzeugen (Staff)")
    @app_commands.describe(
        tier="Laufzeit",
        member="Discord-Mitglied (bekommt den Key automatisch per DM)",
        note="Notiz (optional)",
        hwid=(
            "Hardware-ID des Kunden - optional. Leer lassen, damit sich der Key "
            "automatisch beim ersten Eintragen an das Geraet des Kunden bindet."
        ),
    )
    @app_commands.choices(
        tier=[
            app_commands.Choice(name="Lifetime", value=gtlic.TIER_LIFETIME),
            app_commands.Choice(name="14 Tage", value=gtlic.TIER_14D),
            app_commands.Choice(name="30 Tage", value=gtlic.TIER_30D),
        ]
    )
    async def gtkey_generate(
        self,
        interaction: discord.Interaction,
        tier: app_commands.Choice[str],
        member: discord.Member | None = None,
        note: str = "",
        hwid: str = "",
    ) -> None:
        assert interaction.guild is not None
        if not gtlic.licensing_configured():
            await interaction.response.send_message(
                embed=error_embed(
                    "Nicht eingerichtet",
                    "GPUTWEAKS_LICENSE_SECRET fehlt in der .env.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        expires = gtlic.tier_to_expiry(tier.value)
        license_key = gtlic.generate_license_key(hwid or None, tier=tier.value)
        key_number = await _insert_direct_key(
            self.bot,
            interaction.guild.id,
            member.id if member else None,
            tier.value,
            hwid,
            note,
            license_key,
            interaction.user.id,
        )

        dm_note = ""
        if member is not None:
            try:
                await member.send(
                    embed=success_embed(
                        "🔑 Dein y3zz GPU Tweaks Key",
                        f"Laufzeit: **{gtlic.describe_tier(tier.value, expires)}**\n\n"
                        f"```\n{license_key}\n```\n"
                        "In der App unter **Lizenzkey einfügen** eintragen.",
                    )
                )
                dm_note = f"\n📨 Per DM an {member.mention} geschickt."
            except discord.HTTPException:
                dm_note = "\n⚠️ DM fehlgeschlagen (DMs geschlossen) — Key unten manuell weitergeben."
            await tweak_vouch.request_vouch(
                self.bot, interaction.guild, member,
                product="y3zz GPU Tweaks",
                tier_label=gtlic.describe_tier(tier.value, expires),
            )

        hwid_line = f"Hardware-ID: `{hwid}`\n" if hwid else "Noch nicht an ein Gerät gebunden - bindet sich automatisch.\n"
        await interaction.followup.send(
            embed=success_embed(
                f"Key #{key_number} erzeugt",
                f"Laufzeit: **{gtlic.describe_tier(tier.value, expires)}**\n"
                f"{hwid_line}\n"
                f"```\n{license_key}\n```{dm_note}",
            ),
            ephemeral=True,
        )

    @gtkey.command(name="list", description="Zuletzt ausgestellte Keys anzeigen (Staff)")
    async def gtkey_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        rows = await _list_recent_keys(self.bot, interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                embed=success_embed("Keine Keys", "Noch keine Keys ausgestellt."),
                ephemeral=True,
            )
            return
        lines = []
        for r in rows:
            who = f"<@{r['user_id']}>" if r.get("user_id") else "(kein Discord-User)"
            lines.append(
                f"**#{r['key_number']}** · {gtlic.TIER_LABELS.get(r['tier'], r['tier'])} "
                f"· `{r['status']}` · {who}"
            )
        await interaction.response.send_message(
            embed=success_embed("Letzte GPU-Tweaks-Keys", "\n".join(lines[:15])),
            ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(GputweaksKeysCog(bot))
