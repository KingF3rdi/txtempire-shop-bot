"""
antiraid.py
============

Anti-Raid-Schutz: erkennt ungewöhnlich viele Server-Beitritte in kurzer Zeit
und reagiert automatisch auf frisch erstellte Accounts, die währenddessen
beitreten. Per Befehl an-/ausschaltbar, keine Änderung an db/database.py
nötig (eigene Tabelle antiraid_settings).

Erkennung: pro Server wird (nur im Arbeitsspeicher, kein Verlauf nötig) eine
Liste der letzten Beitritts-Zeitpunkte geführt. Kommen innerhalb von
"fenster_sek" Sekunden mindestens "schwelle" Beitritte zusammen, gilt das als
Raid — jedes NEUE Mitglied, das währenddessen beitritt UND dessen Account
jünger als "min_account_alter_stunden" ist, wird automatisch gekickt/gebannt
(je nach "aktion"). Ein Alarm geht zusätzlich (höchstens alle 30s) in den
konfigurierten Channel.
"""
from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import base_embed, error_embed, success_embed, warn_embed

if TYPE_CHECKING:
    from bot import ShopBot

ACTIONS: tuple[str, ...] = ("kick", "ban", "nur_alarm")
ALERT_COOLDOWN_SECONDS = 30.0

# Nur Laufzeit-Zustand (kein Verlauf nötig, ein Neustart "vergisst" laufende Raids harmlos).
_recent_joins: dict[int, deque[float]] = {}
_last_alert: dict[int, float] = {}


async def _ensure_table(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS antiraid_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            join_threshold INTEGER NOT NULL DEFAULT 5,
            window_seconds INTEGER NOT NULL DEFAULT 10,
            min_account_age_hours INTEGER NOT NULL DEFAULT 24,
            action TEXT NOT NULL DEFAULT 'kick',
            alert_channel_id INTEGER
        );
        """
    )
    await bot.db.db.commit()


async def _get_settings(bot: "ShopBot", guild_id: int) -> dict:
    row = await bot.db.fetchone("SELECT * FROM antiraid_settings WHERE guild_id = ?", (guild_id,))
    if row:
        return dict(row)
    return {
        "guild_id": guild_id, "enabled": 0, "join_threshold": 5, "window_seconds": 10,
        "min_account_age_hours": 24, "action": "kick", "alert_channel_id": None,
    }


async def _upsert(bot: "ShopBot", guild_id: int, **fields) -> None:
    current = await _get_settings(bot, guild_id)
    current.update(fields)
    await bot.db.db.execute(
        """
        INSERT INTO antiraid_settings
            (guild_id, enabled, join_threshold, window_seconds, min_account_age_hours, action, alert_channel_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            enabled = excluded.enabled,
            join_threshold = excluded.join_threshold,
            window_seconds = excluded.window_seconds,
            min_account_age_hours = excluded.min_account_age_hours,
            action = excluded.action,
            alert_channel_id = excluded.alert_channel_id
        """,
        (
            guild_id, int(bool(current["enabled"])), int(current["join_threshold"]),
            int(current["window_seconds"]), int(current["min_account_age_hours"]),
            str(current["action"]), current.get("alert_channel_id"),
        ),
    )
    await bot.db.db.commit()


def _account_age_hours(member: discord.Member) -> float:
    created = member.created_at
    now = discord.utils.utcnow()
    return (now - created).total_seconds() / 3600.0


async def _alert(bot: "ShopBot", guild: discord.Guild, settings: dict, *, joins_in_window: int, member: discord.Member, action_taken: str) -> None:
    now = time.monotonic()
    last = _last_alert.get(guild.id, 0.0)
    if now - last < ALERT_COOLDOWN_SECONDS:
        return
    _last_alert[guild.id] = now

    channel_id = settings.get("alert_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        await channel.send(
            embed=warn_embed(
                "🚨 Anti-Raid: ungewöhnlich viele Beitritte",
                f"**{joins_in_window}** Beitritte innerhalb von **{settings['window_seconds']}s** erkannt.\n"
                f"Zuletzt: {member.mention} (`{member}`), Account erstellt vor "
                f"{_account_age_hours(member):.1f}h.\n"
                f"Aktion: **{action_taken}**",
            )
        )
    except discord.HTTPException:
        pass


class AntiRaidCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    antiraid_group = app_commands.Group(
        name="antiraid", description="Anti-Raid-Schutz verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @antiraid_group.command(name="an", description="Anti-Raid-Schutz aktivieren")
    async def an(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await _upsert(self.bot, interaction.guild.id, enabled=1)
        settings = await _get_settings(self.bot, interaction.guild.id)
        note = "" if settings.get("alert_channel_id") else "\n⚠️ Kein Alarm-Channel gesetzt — nutze `/antiraid einstellungen`."
        await interaction.response.send_message(
            embed=success_embed("Aktiviert", f"Anti-Raid-Schutz ist jetzt **an**.{note}"), ephemeral=True,
        )

    @antiraid_group.command(name="aus", description="Anti-Raid-Schutz deaktivieren")
    async def aus(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await _upsert(self.bot, interaction.guild.id, enabled=0)
        await interaction.response.send_message(
            embed=success_embed("Deaktiviert", "Anti-Raid-Schutz ist jetzt **aus**."), ephemeral=True,
        )

    @antiraid_group.command(name="einstellungen", description="Anti-Raid-Schwellenwerte konfigurieren")
    @app_commands.describe(
        schwelle="Ab wie vielen Beitritten im Zeitfenster gilt es als Raid (Standard 5)",
        fenster_sekunden="Zeitfenster in Sekunden (Standard 10)",
        min_account_alter_stunden="Accounts jünger als das werden während eines Raids automatisch behandelt (Standard 24)",
        aktion="Was mit frischen Accounts während eines Raids passiert",
        alarm_channel="Channel für Anti-Raid-Alarme",
    )
    @app_commands.choices(aktion=[
        app_commands.Choice(name="Kicken", value="kick"),
        app_commands.Choice(name="Bannen", value="ban"),
        app_commands.Choice(name="Nur Alarm (nichts tun)", value="nur_alarm"),
    ])
    async def einstellungen(
        self, interaction: discord.Interaction,
        schwelle: Optional[int] = None,
        fenster_sekunden: Optional[int] = None,
        min_account_alter_stunden: Optional[int] = None,
        aktion: Optional[app_commands.Choice[str]] = None,
        alarm_channel: Optional[discord.TextChannel] = None,
    ) -> None:
        assert interaction.guild is not None
        if schwelle is not None and schwelle < 2:
            await interaction.response.send_message(
                embed=error_embed("Ungültig", "Schwelle muss mindestens 2 sein."), ephemeral=True,
            )
            return
        fields: dict = {}
        if schwelle is not None:
            fields["join_threshold"] = schwelle
        if fenster_sekunden is not None:
            fields["window_seconds"] = max(1, fenster_sekunden)
        if min_account_alter_stunden is not None:
            fields["min_account_age_hours"] = max(0, min_account_alter_stunden)
        if aktion is not None:
            fields["action"] = aktion.value
        if alarm_channel is not None:
            fields["alert_channel_id"] = alarm_channel.id
        await _upsert(self.bot, interaction.guild.id, **fields)
        settings = await _get_settings(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed("Gespeichert", _settings_text(interaction.guild, settings)), ephemeral=True,
        )

    @antiraid_group.command(name="status", description="Aktuelle Anti-Raid-Einstellungen anzeigen")
    async def status(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await _get_settings(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embed=base_embed("Anti-Raid-Status", _settings_text(interaction.guild, settings)), ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        settings = await _get_settings(self.bot, guild.id)
        if not settings.get("enabled"):
            return

        window = int(settings["window_seconds"])
        threshold = int(settings["join_threshold"])
        now = time.monotonic()

        joins = _recent_joins.setdefault(guild.id, deque())
        joins.append(now)
        while joins and now - joins[0] > window:
            joins.popleft()

        if len(joins) < threshold:
            return

        # Raid-Verdacht: reagiere nur auf frische Accounts, nicht auf jeden Beitritt.
        min_age_hours = float(settings["min_account_age_hours"])
        if _account_age_hours(member) >= min_age_hours:
            await _alert(self.bot, guild, settings, joins_in_window=len(joins), member=member, action_taken="beobachtet (Account nicht neu genug)")
            return

        action = str(settings["action"])
        action_taken = "nur beobachtet"
        try:
            if action == "kick":
                await member.kick(reason="Anti-Raid: verdächtiger Beitritt (neuer Account während Massen-Beitritt)")
                action_taken = "gekickt"
            elif action == "ban":
                await member.ban(reason="Anti-Raid: verdächtiger Beitritt (neuer Account während Massen-Beitritt)", delete_message_seconds=0)
                action_taken = "gebannt"
        except discord.HTTPException:
            action_taken = f"{action} fehlgeschlagen (fehlende Rechte?)"

        await _alert(self.bot, guild, settings, joins_in_window=len(joins), member=member, action_taken=action_taken)


def _settings_text(guild: discord.Guild, settings: dict) -> str:
    channel_id = settings.get("alert_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    action_labels = {"kick": "Kicken", "ban": "Bannen", "nur_alarm": "Nur Alarm"}
    return (
        f"Status: {'✅ an' if settings.get('enabled') else '❌ aus'}\n"
        f"Schwelle: **{settings['join_threshold']}** Beitritte / **{settings['window_seconds']}s**\n"
        f"Reagiert auf Accounts jünger als **{settings['min_account_age_hours']}h**\n"
        f"Aktion: **{action_labels.get(settings['action'], settings['action'])}**\n"
        f"Alarm-Channel: {channel.mention if channel else '_keiner gesetzt_'}"
    )


async def setup(bot: "ShopBot") -> None:
    await _ensure_table(bot)
    await bot.add_cog(AntiRaidCog(bot))
