"""Empfehlungs-Provision: Werber bekommt bei jedem Kauf des Geworbenen Credits.

Tabellen werden von cogs/monetization.py angelegt (_ensure_tables). Diese
Datei ist bewusst fehlertolerant (try/except) — ein Problem hier darf nie
die eigentliche Kauf-Bestätigung blockieren.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot import ShopBot


async def _get_monetization_settings(bot: "ShopBot", guild_id: int) -> dict:
    await bot.db.db.execute(
        "INSERT OR IGNORE INTO monetization_settings (guild_id) VALUES (?)", (guild_id,)
    )
    await bot.db.db.commit()
    row = await bot.db.fetchone(
        "SELECT * FROM monetization_settings WHERE guild_id = ?", (guild_id,)
    )
    return dict(row) if row else {}


async def credit_referral(
    bot: "ShopBot", guild: discord.Guild, buyer_id: int, purchase_amount: float
) -> None:
    """Schreibt dem Werber eines Käufers einen %-Anteil des Kaufs als Credits gut."""
    try:
        if purchase_amount <= 0:
            return
        settings = await _get_monetization_settings(bot, guild.id)
        pct = float(settings.get("referral_percent") or 0)
        if pct <= 0:
            return
        row = await bot.db.fetchone(
            "SELECT referrer_user_id FROM referral_links WHERE guild_id = ? AND invited_user_id = ?",
            (guild.id, buyer_id),
        )
        if not row or not row["referrer_user_id"]:
            return
        referrer_id = int(row["referrer_user_id"])
        if referrer_id == buyer_id:
            return
        bonus = round(purchase_amount * pct / 100, 2)
        if bonus <= 0:
            return

        await bot.db.add_credits(guild.id, referrer_id, bonus)
        await bot.db.db.execute(
            "UPDATE referral_links SET credited_total = credited_total + ? "
            "WHERE guild_id = ? AND invited_user_id = ?",
            (bonus, guild.id, buyer_id),
        )
        await bot.db.db.commit()

        from utils.credits import format_credits

        referrer = guild.get_member(referrer_id)
        if referrer is not None:
            try:
                await referrer.send(
                    f"💰 Dein geworbenes Mitglied <@{buyer_id}> hat eingekauft — du hast "
                    f"**{format_credits(bonus)} Credits** Empfehlungs-Bonus erhalten!"
                )
            except discord.HTTPException:
                pass

        log_channel_id = settings.get("log_channel_id")
        if log_channel_id:
            channel = guild.get_channel(int(log_channel_id))
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(
                        f"💰 Empfehlungs-Bonus: <@{referrer_id}> bekommt "
                        f"**{format_credits(bonus)} Credits** für den Kauf von <@{buyer_id}> "
                        f"({pct:g}% von {purchase_amount:g})."
                    )
                except discord.HTTPException:
                    pass
    except Exception as e:
        print(f"[Referral] credit_referral fehlgeschlagen: {e!r}")
