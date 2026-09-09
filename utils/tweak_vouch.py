"""
tweak_vouch.py
===============

Eigenständiges Stern-Bewertungssystem für alle Tweak-Produkte (Ferdi
Mousetweaks, y3zz GPU Tweaks, Custom Packs, ...) - optisch identisch zum
normalen Shop-Vouch (Sterne-Buttons per DM -> Modal für Text -> Embed im
Kanal, siehe utils/vouch_request.py), aber bewusst KOMPLETT getrennt:

  - Eigener Kanal (siehe /tweakvouchsetup), nicht der normale Vouch-Kanal.
  - Eigene Custom-IDs ("tweakvouch:N"), nicht "vouchrate:N".
  - KEIN Zugriff auf die `orders`-Tabelle oder vouch_stats - zählt also
    nicht in die normale Vouch-/Bestellungen-Übersicht (cogs/vouch.py).
  - Bezieht sich immer auf GENAU den Kauf, nach dem die DM ausgelöst wurde
    (Ticket-Confirm oder /key generate / Pack-Lieferung) - das ist per
    Definition immer der neueste Kauf, keine Warteschlange wie beim
    normalen `/vouch`.

Eigene Tabellen (tweak_vouch_settings, tweak_vouches) - keine Änderung an
db/database.py nötig. Die Ratings werden zusätzlich (best-effort) an die
Website gesynct (siehe integrations/shop_api.py, source="tweak").
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from integrations.shop_api import shop_api
from utils.embeds import base_embed, error_embed, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


async def _ensure_table(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tweak_vouch_settings (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS tweak_vouches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            tier_label TEXT NOT NULL DEFAULT '',
            rating INTEGER NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    await bot.db.db.commit()


async def _save_vouch(
    bot: "ShopBot",
    *,
    guild_id: int,
    user_id: int,
    product: str,
    tier_label: str,
    rating: int,
    message: str,
) -> int:
    await _ensure_table(bot)
    cursor = await bot.db.db.execute(
        """
        INSERT INTO tweak_vouches (guild_id, user_id, product, tier_label, rating, message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (guild_id, user_id, product, tier_label, rating, message),
    )
    await bot.db.db.commit()
    return int(cursor.lastrowid)


async def get_channel_id(bot: "ShopBot", guild_id: int) -> Optional[int]:
    await _ensure_table(bot)
    row = await bot.db.fetchone(
        "SELECT channel_id FROM tweak_vouch_settings WHERE guild_id = ?", (guild_id,)
    )
    return int(row["channel_id"]) if row and row["channel_id"] else None


async def set_channel_id(bot: "ShopBot", guild_id: int, channel_id: Optional[int]) -> None:
    await _ensure_table(bot)
    await bot.db.db.execute(
        """
        INSERT INTO tweak_vouch_settings (guild_id, channel_id) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id
        """,
        (guild_id, channel_id),
    )
    await bot.db.db.commit()


def _stars(rating: int) -> str:
    rating = max(1, min(5, int(rating)))
    return "★" * rating + "☆" * (5 - rating)


class TweakVouchMessageModal(discord.ui.Modal, title="Tweaks Vouch"):
    message = discord.ui.TextInput(
        label="Dein Feedback",
        style=discord.TextStyle.paragraph,
        placeholder="Wie war der Tweak / das Pack?",
        max_length=1000,
        required=True,
    )

    def __init__(self, bot: "ShopBot", guild_id: int, rating: int, product: str, tier_label: str) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.rating = rating
        self.product = product
        self.tier_label = tier_label

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.message.value).strip()
        if not text:
            await interaction.response.send_message(embed=error_embed("Leerer Text"), ephemeral=True)
            return

        channel_id = await get_channel_id(self.bot, self.guild_id)
        guild = self.bot.get_guild(self.guild_id)
        channel = guild.get_channel(channel_id) if (guild and channel_id) else None
        if channel is None:
            await interaction.response.send_message(
                embed=error_embed("Kein Tweak-Vouch-Kanal", "Staff muss `/tweakvouchsetup` ausführen."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🛠️ Neuer Tweaks Vouch",
            description=text[:1500],
            color=0x2F6BFF,
        )
        embed.add_field(name="Bewertung", value=_stars(self.rating), inline=True)
        embed.add_field(name="Produkt", value=f"{self.product} — {self.tier_label}", inline=True)
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        await channel.send(embed=embed)

        vouch_id = await _save_vouch(
            self.bot,
            guild_id=self.guild_id,
            user_id=interaction.user.id,
            product=self.product,
            tier_label=self.tier_label,
            rating=self.rating,
            message=text,
        )
        if shop_api.enabled:
            # external_id ist website-weit UNIQUE über alle Vouch-Quellen;
            # negativ gespiegelt, um Kollisionen mit orders.id (Ticket-Vouches) zu vermeiden.
            await shop_api.sync_vouch(
                giver_name=str(interaction.user),
                message=f"{self.product} — {self.tier_label}: {text}",
                is_positive=self.rating >= 4,
                external_id=-vouch_id,
                rating=self.rating,
                source="tweak",
            )

        await interaction.response.send_message(
            embed=success_embed("Vouch gesendet", "Danke für dein Feedback!"), ephemeral=True,
        )


class TweakVouchRatingView(discord.ui.View):
    """Persistente Sterne-Buttons für die Tweaks-Vouch-DM."""

    def __init__(self, bot: "ShopBot | None" = None, guild_id: int = 0, product: str = "", tier_label: str = "") -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.product = product
        self.tier_label = tier_label

    async def _pick(self, interaction: discord.Interaction, rating: int) -> None:
        bot = self.bot or interaction.client  # type: ignore[assignment]
        await interaction.response.send_modal(
            TweakVouchMessageModal(bot, self.guild_id, rating, self.product, self.tier_label)
        )

    @discord.ui.button(label="1 ★", style=discord.ButtonStyle.secondary, custom_id="tweakvouch:1", row=0)
    async def star1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 1)

    @discord.ui.button(label="2 ★", style=discord.ButtonStyle.secondary, custom_id="tweakvouch:2", row=0)
    async def star2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 2)

    @discord.ui.button(label="3 ★", style=discord.ButtonStyle.primary, custom_id="tweakvouch:3", row=0)
    async def star3(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 3)

    @discord.ui.button(label="4 ★", style=discord.ButtonStyle.success, custom_id="tweakvouch:4", row=0)
    async def star4(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 4)

    @discord.ui.button(label="5 ★", style=discord.ButtonStyle.success, custom_id="tweakvouch:5", row=0)
    async def star5(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._pick(interaction, 5)


async def request_vouch(
    bot: "ShopBot",
    guild: discord.Guild,
    member: "discord.abc.User",
    *,
    product: str,
    tier_label: str,
) -> bool:
    """DM an den Kunden direkt nach Kauf-Bestätigung - bezieht sich immer auf
    genau diesen (den neuesten) Kauf. Best-effort: gibt False zurück (statt
    Fehler zu werfen), wenn kein Kanal gesetzt ist oder die DM fehlschlägt."""
    channel_id = await get_channel_id(bot, guild.id)
    if not channel_id:
        return False
    channel = guild.get_channel(channel_id)
    if channel is None:
        return False
    try:
        await member.send(
            content="🛠️ **Tweaks Vouch**",
            embed=base_embed(
                "⭐ Bewertung abgeben",
                f"Danke für deinen Kauf **{product} — {tier_label}**!\n\n"
                f"Tippe auf die Sterne unten und schreib kurz dein Feedback — "
                f"wird automatisch in {channel.mention} gepostet.",
            ),
            view=TweakVouchRatingView(bot, guild.id, product, tier_label),
        )
        return True
    except discord.HTTPException:
        return False
