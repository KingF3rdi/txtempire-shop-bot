"""
spawner_vouch.py
================

Vouch nach einem bestätigten Spawner-Handel (Kauf oder Verkauf):

  1. Staff bestätigt das Spawner-Ticket -> der Käufer bekommt eine DM mit
     Sterne-Buttons (request_spawner_vouch).
  2. Sterne -> Modal (Feedback) -> Vouch wird im normalen Vouch-Channel des
     Servers gepostet (guild_settings.vouch_channel_id, wie beim Shop-Vouch).
  3. Der Vouch gilt dem EINZELNEN Spawner-Staff, der den Handel bestätigt hat
     (steht als "Spawner-Staff" im Vouch und ist in spawner_vouches gespeichert).

Anders als die Tweak-Vouches (utils/tweak_vouch.py) steht der Kontext in der
Datenbank (spawner_vouch_requests, zugeordnet über die DM-Nachricht) — die
Buttons funktionieren also auch nach einem Bot-Neustart, und pro Ticket gibt
es genau einen Vouch. Eigene Tabellen, kein Zugriff auf die orders-Tabelle.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

import config
from utils.embeds import base_embed, error_embed, success_embed
from utils.vouch_channel_perms import get_vouch_text_channel

if TYPE_CHECKING:
    from bot import ShopBot


async def ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS spawner_vouch_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_id INTEGER NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            staff_name TEXT NOT NULL DEFAULT '',
            product TEXT NOT NULL,
            ign TEXT NOT NULL DEFAULT '',
            dm_message_id INTEGER,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS spawner_vouches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            rating INTEGER NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    await bot.db.db.commit()


def _stars(rating: int) -> str:
    rating = max(1, min(5, int(rating)))
    return "★" * rating + "☆" * (5 - rating)


def product_label(ticket: dict) -> str:
    verb = "Kauf" if ticket.get("direction") == "buy" else "Verkauf"
    return f"{verb}: {ticket['qty']}× {ticket['spawner_name']}"


def build_vouch_embed(
    user: discord.abc.User, request: dict, rating: int, text: str,
) -> discord.Embed:
    embed = discord.Embed(title="🧱 Neuer Spawner-Vouch", description=text[:1500], color=config.EMBED_COLOR)
    embed.add_field(name="Bewertung", value=_stars(rating), inline=True)
    embed.add_field(name="Handel", value=request["product"], inline=True)
    embed.add_field(name="Spawner-Staff", value=f"<@{request['staff_id']}>", inline=True)
    if request.get("ign"):
        embed.add_field(name="IGN", value=request["ign"], inline=True)
    embed.set_author(name=str(user), icon_url=user.display_avatar.url)
    return embed


async def _get_open_request(bot: "ShopBot", dm_message_id: int) -> Optional[dict]:
    row = await bot.db.fetchone(
        "SELECT * FROM spawner_vouch_requests WHERE dm_message_id = ? AND status = 'open'", (dm_message_id,)
    )
    return dict(row) if row else None


async def _get_request(bot: "ShopBot", request_id: int) -> Optional[dict]:
    row = await bot.db.fetchone("SELECT * FROM spawner_vouch_requests WHERE id = ?", (request_id,))
    return dict(row) if row else None


async def _claim(bot: "ShopBot", request_id: int) -> bool:
    """Markiert die Anfrage atomar als erledigt — nur der erste Aufruf gewinnt."""
    cur = await bot.db.db.execute(
        "UPDATE spawner_vouch_requests SET status = 'done' WHERE id = ? AND status = 'open'", (request_id,)
    )
    await bot.db.db.commit()
    return (cur.rowcount or 0) > 0


async def _reopen(bot: "ShopBot", request_id: int) -> None:
    await bot.db.db.execute("UPDATE spawner_vouch_requests SET status = 'open' WHERE id = ?", (request_id,))
    await bot.db.db.commit()


class SpawnerVouchModal(discord.ui.Modal, title="Spawner-Vouch"):
    message = discord.ui.TextInput(
        label="Dein Feedback",
        style=discord.TextStyle.paragraph,
        placeholder="Wie war der Handel mit dem Staff?",
        max_length=1000,
        required=True,
    )

    def __init__(self, bot: "ShopBot", request_id: int, rating: int) -> None:
        super().__init__()
        self.bot = bot
        self.request_id = request_id
        self.rating = rating

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.message.value).strip()
        if not text:
            await interaction.response.send_message(embed=error_embed("Leerer Text"), ephemeral=True)
            return
        request = await _get_request(self.bot, self.request_id)
        if not request or request["status"] != "open":
            await interaction.response.send_message(
                embed=error_embed("Schon abgegeben", "Für diesen Handel gibt es schon einen Vouch."), ephemeral=True,
            )
            return
        channel = await get_vouch_text_channel(self.bot, int(request["guild_id"]))
        if channel is None:
            await interaction.response.send_message(
                embed=error_embed("Vouch-Channel fehlt", "Ein Admin muss `/setup` mit einem Vouch-Channel ausführen."),
                ephemeral=True,
            )
            return
        if not await _claim(self.bot, self.request_id):  # Doppelklick / zweiter Versuch
            await interaction.response.send_message(
                embed=error_embed("Schon abgegeben", "Für diesen Handel gibt es schon einen Vouch."), ephemeral=True,
            )
            return
        try:
            await channel.send(embed=build_vouch_embed(interaction.user, request, self.rating, text))
        except discord.HTTPException:
            await _reopen(self.bot, self.request_id)
            await interaction.response.send_message(
                embed=error_embed("Posten fehlgeschlagen", "Bitte versuch es gleich noch einmal."), ephemeral=True,
            )
            return

        await self.bot.db.db.execute(
            "INSERT INTO spawner_vouches (guild_id, ticket_id, user_id, staff_id, product, rating, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request["guild_id"], request["ticket_id"], request["user_id"], request["staff_id"],
             request["product"], self.rating, text),
        )
        await self.bot.db.db.commit()
        try:  # Stats-Übersicht wieder unter den neuesten Vouch setzen (best-effort)
            from utils.vouch_stats import refresh_vouch_stats_under_latest

            await refresh_vouch_stats_under_latest(self.bot, channel, int(request["guild_id"]))
        except Exception as e:
            print(f"[SpawnerVouch] Stats-Refresh fehlgeschlagen: {e!r}")

        await interaction.response.send_message(
            embed=success_embed("Vouch gesendet", f"Danke! Dein Vouch wurde in {channel.mention} gepostet."),
            ephemeral=True,
        )
        if interaction.message is not None:  # Sterne in der DM abschalten
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                pass


class SpawnerVouchRatingView(discord.ui.View):
    """Persistente Sterne-Buttons der Spawner-Vouch-DM (Kontext aus der DB, über die DM-Nachricht)."""

    def __init__(self, bot: "ShopBot | None" = None) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _pick(self, interaction: discord.Interaction, rating: int) -> None:
        bot = self.bot or interaction.client  # type: ignore[assignment]
        request = await _get_open_request(bot, interaction.message.id if interaction.message else 0)
        if not request:
            await interaction.response.send_message(
                embed=error_embed("Schon abgegeben", "Für diesen Handel gibt es keinen offenen Vouch mehr."),
                ephemeral=True,
            )
            return
        if interaction.user.id != int(request["user_id"]):
            await interaction.response.send_message(embed=error_embed("Nur der Käufer"), ephemeral=True)
            return
        await interaction.response.send_modal(SpawnerVouchModal(bot, int(request["id"]), rating))

    @discord.ui.button(label="1 ★", style=discord.ButtonStyle.secondary, custom_id="spvouch:1", row=0)
    async def star1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 1)

    @discord.ui.button(label="2 ★", style=discord.ButtonStyle.secondary, custom_id="spvouch:2", row=0)
    async def star2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 2)

    @discord.ui.button(label="3 ★", style=discord.ButtonStyle.primary, custom_id="spvouch:3", row=0)
    async def star3(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 3)

    @discord.ui.button(label="4 ★", style=discord.ButtonStyle.success, custom_id="spvouch:4", row=0)
    async def star4(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 4)

    @discord.ui.button(label="5 ★", style=discord.ButtonStyle.success, custom_id="spvouch:5", row=0)
    async def star5(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 5)


async def request_spawner_vouch(
    bot: "ShopBot",
    guild: discord.Guild,
    member: "discord.abc.User",
    *,
    ticket: dict,
    staff: "discord.abc.User",
) -> str:
    """DM mit Sterne-Buttons an den Käufer, bezogen auf den Staff, der den Handel bestätigt hat.
    Ergebnis: "sent" | "no_channel" | "dm_failed" | "already" (pro Ticket nur eine Anfrage)."""
    channel = await get_vouch_text_channel(bot, guild.id)
    if channel is None:
        return "no_channel"
    product = product_label(ticket)
    try:
        cur = await bot.db.db.execute(
            "INSERT INTO spawner_vouch_requests (guild_id, ticket_id, user_id, staff_id, staff_name, product, ign) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild.id, ticket["id"], member.id, staff.id, staff.display_name, product, ticket.get("ign") or ""),
        )
    except Exception:  # UNIQUE(ticket_id): für dieses Ticket wurde schon angefragt
        return "already"
    await bot.db.db.commit()
    request_id = int(cur.lastrowid)  # type: ignore[arg-type]
    try:
        message = await member.send(
            content="🧱 **Spawner-Vouch**",
            embed=base_embed(
                "⭐ Bewertung abgeben",
                f"Danke für deinen Spawner-Handel (**{product}**) mit **{staff.display_name}**!\n\n"
                f"Bewerte **{staff.display_name}** mit den Sternen unten und schreib kurz dein Feedback — "
                f"es wird automatisch in {channel.mention} gepostet.",
            ),
            view=SpawnerVouchRatingView(bot),
        )
    except discord.HTTPException:
        await bot.db.db.execute("DELETE FROM spawner_vouch_requests WHERE id = ?", (request_id,))
        await bot.db.db.commit()
        return "dm_failed"
    await bot.db.db.execute("UPDATE spawner_vouch_requests SET dm_message_id = ? WHERE id = ?", (message.id, request_id))
    await bot.db.db.commit()
    return "sent"
