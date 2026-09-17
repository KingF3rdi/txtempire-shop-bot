"""
antiraid.py
============

Anti-Raid- & Anti-Nuke-Schutz, ein gemeinsamer Schalter (/antiraid protect
und /antiraid off) für beides:

- Raid: erkennt ungewöhnlich viele Server-Beitritte in kurzer Zeit und
  reagiert automatisch auf frisch erstellte Accounts, die währenddessen
  beitreten.
- Nuke: erkennt, wenn ein einzelnes Mitglied in kurzer Zeit mehrere
  destruktive Aktionen ausführt (Channel löschen, Rolle löschen, Mitglied
  bannen) — typisches Muster eines kompromittierten/böswilligen
  Staff-Accounts oder eines gekaperten Bots. Der Übeltäter wird sofort alle
  Rollen entzogen und danach je nach "aktion" gekickt/gebannt.

Braucht die Berechtigung "Audit-Log anzeigen" für den Bot, um den Verursacher
einer Löschung/Bann zu ermitteln. Keine Änderung an db/database.py nötig
(eigene Tabelle antiraid_settings).

Erkennung basiert auf einem gleitenden Zeitfenster pro Server (Raid) bzw. pro
Server+Verursacher (Nuke), nur im Arbeitsspeicher — ein Neustart "vergisst"
laufende Vorfälle harmlos.
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

NUKE_THRESHOLD = 3
NUKE_WINDOW_SECONDS = 10.0
NUKE_PUNISH_COOLDOWN_SECONDS = 60.0

# Nur Laufzeit-Zustand (kein Verlauf nötig, ein Neustart "vergisst" laufende Vorfälle harmlos).
_recent_joins: dict[int, deque[float]] = {}
_recent_actions: dict[tuple[int, int], deque[float]] = {}  # (guild_id, actor_id) -> Zeitstempel
_last_punished: dict[tuple[int, int], float] = {}
_last_alert: dict[int, float] = {}


def _hit_threshold(bucket: "deque[float]", now: float, window: float, threshold: int) -> bool:
    """Trägt `now` ein, verwirft alles außerhalb von `window`, meldet ob `threshold` erreicht ist."""
    bucket.append(now)
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    return len(bucket) >= threshold


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


async def _send_alert(bot: "ShopBot", guild: discord.Guild, settings: dict, title: str, description: str) -> None:
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
        await channel.send(embed=warn_embed(title, description))
    except discord.HTTPException:
        pass


async def _alert(bot: "ShopBot", guild: discord.Guild, settings: dict, *, joins_in_window: int, member: discord.Member, action_taken: str) -> None:
    await _send_alert(
        bot, guild, settings,
        "🚨 Anti-Raid: ungewöhnlich viele Beitritte",
        f"**{joins_in_window}** Beitritte innerhalb von **{settings['window_seconds']}s** erkannt.\n"
        f"Zuletzt: {member.mention} (`{member}`), Account erstellt vor "
        f"{_account_age_hours(member):.1f}h.\n"
        f"Aktion: **{action_taken}**",
    )


async def _find_actor(guild: discord.Guild, action: discord.AuditLogAction, target_id: int) -> Optional[discord.abc.User]:
    """Sucht im Audit-Log den Verursacher einer frischen (<5s) Aktion gegen target_id."""
    try:
        async for entry in guild.audit_logs(limit=5, action=action):
            if getattr(entry.target, "id", None) != target_id:
                continue
            if (discord.utils.utcnow() - entry.created_at).total_seconds() < 5:
                return entry.user
            return None
    except discord.Forbidden:
        pass
    return None


async def _punish_nuker(bot: "ShopBot", guild: discord.Guild, settings: dict, actor: discord.abc.User, trigger: str) -> None:
    key = (guild.id, actor.id)
    now = time.monotonic()
    if now - _last_punished.get(key, 0.0) < NUKE_PUNISH_COOLDOWN_SECONDS:
        return
    _last_punished[key] = now
    _recent_actions.pop(key, None)

    action_taken = "nur beobachtet"
    member = guild.get_member(actor.id)
    try:
        if member is not None and member.roles[1:]:
            await member.edit(roles=[], reason=f"Anti-Nuke: {trigger}")
        action = str(settings["action"])
        if action == "kick" and member is not None:
            await member.kick(reason=f"Anti-Nuke: {trigger}")
            action_taken = "Rollen entzogen + gekickt"
        elif action == "ban":
            await guild.ban(actor, reason=f"Anti-Nuke: {trigger}", delete_message_seconds=0)
            action_taken = "Rollen entzogen + gebannt"
        elif member is not None:
            action_taken = "Rollen entzogen"
    except discord.HTTPException:
        action_taken = f"{action_taken} (Aktion teilweise fehlgeschlagen, fehlende Rechte?)"

    await _send_alert(
        bot, guild, settings,
        "🚨 Anti-Nuke: verdächtige Serien-Aktion",
        f"{actor.mention} (`{actor}`) hat mehrfach destruktiv gehandelt: **{trigger}**.\n"
        f"Aktion: **{action_taken}**",
    )


async def _handle_nuke_event(bot: "ShopBot", guild: discord.Guild, audit_action: discord.AuditLogAction, target_id: int, trigger: str) -> None:
    settings = await _get_settings(bot, guild.id)
    if not settings.get("enabled"):
        return
    actor = await _find_actor(guild, audit_action, target_id)
    if actor is None or actor.id == bot.user.id or actor.id == guild.owner_id:
        return

    key = (guild.id, actor.id)
    bucket = _recent_actions.setdefault(key, deque())
    if _hit_threshold(bucket, time.monotonic(), NUKE_WINDOW_SECONDS, NUKE_THRESHOLD):
        await _punish_nuker(bot, guild, settings, actor, trigger)


class AntiRaidCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    antiraid_group = app_commands.Group(
        name="antiraid", description="Anti-Raid- & Anti-Nuke-Schutz verwalten (Staff)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @antiraid_group.command(name="protect", description="Anti-Raid- & Anti-Nuke-Schutz aktivieren")
    async def protect(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await _upsert(self.bot, interaction.guild.id, enabled=1)
        settings = await _get_settings(self.bot, interaction.guild.id)
        note = "" if settings.get("alert_channel_id") else "\n⚠️ Kein Alarm-Channel gesetzt — nutze `/antiraid einstellungen`."
        await interaction.response.send_message(
            embed=success_embed("Aktiviert", f"Anti-Raid- & Anti-Nuke-Schutz ist jetzt **an**.{note}"), ephemeral=True,
        )

    @antiraid_group.command(name="off", description="Anti-Raid- & Anti-Nuke-Schutz deaktivieren")
    async def off(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await _upsert(self.bot, interaction.guild.id, enabled=0)
        await interaction.response.send_message(
            embed=success_embed("Deaktiviert", "Anti-Raid- & Anti-Nuke-Schutz ist jetzt **aus**."), ephemeral=True,
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
        joins = _recent_joins.setdefault(guild.id, deque())
        if not _hit_threshold(joins, time.monotonic(), window, threshold):
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

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await _handle_nuke_event(self.bot, channel.guild, discord.AuditLogAction.channel_delete, channel.id, "mehrere Channels gelöscht")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await _handle_nuke_event(self.bot, role.guild, discord.AuditLogAction.role_delete, role.id, "mehrere Rollen gelöscht")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        await _handle_nuke_event(self.bot, guild, discord.AuditLogAction.ban, user.id, "mehrere Mitglieder gebannt")


def _settings_text(guild: discord.Guild, settings: dict) -> str:
    channel_id = settings.get("alert_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    action_labels = {"kick": "Kicken", "ban": "Bannen", "nur_alarm": "Nur Alarm"}
    return (
        f"Status: {'✅ an (Raid- & Nuke-Schutz)' if settings.get('enabled') else '❌ aus'}\n"
        f"Raid-Schwelle: **{settings['join_threshold']}** Beitritte / **{settings['window_seconds']}s**\n"
        f"Reagiert auf Accounts jünger als **{settings['min_account_age_hours']}h**\n"
        f"Nuke-Schwelle: **{NUKE_THRESHOLD}** destruktive Aktionen / **{int(NUKE_WINDOW_SECONDS)}s** (Channel/Rolle löschen, bannen)\n"
        f"Aktion: **{action_labels.get(settings['action'], settings['action'])}**\n"
        f"Alarm-Channel: {channel.mention if channel else '_keiner gesetzt_'}"
    )


async def setup(bot: "ShopBot") -> None:
    await _ensure_table(bot)
    await bot.add_cog(AntiRaidCog(bot))


def _self_check() -> None:
    """ponytail: kleiner Nachweis, dass das Sliding-Window korrekt zählt und verwirft."""
    bucket: deque[float] = deque()
    assert not _hit_threshold(bucket, 0.0, window=10, threshold=3)
    assert not _hit_threshold(bucket, 1.0, window=10, threshold=3)
    assert _hit_threshold(bucket, 2.0, window=10, threshold=3)
    assert not _hit_threshold(bucket, 20.0, window=10, threshold=3), "alte Einträge müssen aus dem Fenster fallen"


if __name__ == "__main__":
    _self_check()
    print("OK")
