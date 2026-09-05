from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.credits import currency_to_credits, format_credits
from utils.embeds import base_embed, error_embed, format_price, success_embed, warn_embed
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


def _reward_table_text() -> str:
    lines = []
    for invites, currency in config.INVITE_REWARDS:
        cred = currency_to_credits(currency)
        lines.append(
            f"• **{invites} Invites** → {format_price(currency)} "
            f"({format_credits(cred)} Credits)"
        )
    return "\n".join(lines)


def _parse_ends_at(raw: str) -> datetime | None:
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


async def grant_due_invite_rewards(
    bot: ShopBot, guild_id: int, inviter_id: int
) -> list[dict]:
    """Vergibt alle fälligen Meilenstein-Rewards. Liste der neuen Claims."""
    count = await bot.db.count_invites(guild_id, inviter_id)
    claimed = await bot.db.list_claimed_invite_milestones(guild_id, inviter_id)
    granted: list[dict] = []
    for milestone, currency in config.INVITE_REWARDS:
        if count < milestone or milestone in claimed:
            continue
        credits = round(currency_to_credits(currency), 2)
        ok = await bot.db.claim_invite_milestone(
            guild_id,
            inviter_id,
            milestone,
            currency_amount=float(currency),
            credits_amount=credits,
        )
        if ok:
            granted.append(
                {
                    "milestone": milestone,
                    "currency": float(currency),
                    "credits": credits,
                }
            )
    return granted


async def end_and_merge_competition(
    bot: ShopBot, competition: dict, *, reason: str = "ended"
) -> dict:
    """Beendet Competition und zählt Invites auf den normalen Stand."""
    cid = int(competition["id"])
    guild_id = int(competition["guild_id"])
    if str(competition.get("status")) == "active":
        await bot.db.update_invite_competition(cid, status="ended")
    result = await bot.db.merge_competition_invites(cid)
    # Rewards für alle Inviter der Competition nachziehen
    joins = await bot.db.list_competition_joins(cid)
    inviters = {int(r["inviter_id"]) for r in joins}
    reward_hits = 0
    for uid in inviters:
        granted = await grant_due_invite_rewards(bot, guild_id, uid)
        reward_hits += len(granted)
    return {
        "added": result["added"],
        "inviters": result["inviters_touched"],
        "rewards": reward_hits,
        "reason": reason,
        "competition_id": cid,
        "title": competition.get("title") or "Invite Competition",
    }


def _format_leaderboard_lines(
    rows: list[dict], guild: discord.Guild
) -> str:
    if not rows:
        return "_Noch keine Invites._"
    lines: list[str] = []
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        uid = int(row["inviter_id"])
        cnt = int(row["cnt"])
        member = guild.get_member(uid)
        name = member.mention if member else f"`{uid}`"
        prefix = medals[i] if i < 3 else f"**{i + 1}.**"
        lines.append(f"{prefix} {name} — **{cnt}** Invites")
    return "\n".join(lines)


class InvitesCog(commands.Cog):
    """Invite-Tracking, Leaderboard, Competitions + Credit-Rewards."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot
        self._invite_cache: dict[int, dict[str, int]] = {}
        self.competition_expire_loop.start()

    def cog_unload(self) -> None:
        self.competition_expire_loop.cancel()

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            for guild in self.bot.guilds:
                await self._refresh_invites(guild)

    @tasks.loop(minutes=1)
    async def competition_expire_loop(self) -> None:
        try:
            expired = await self.bot.db.list_expired_active_competitions()
            for comp in expired:
                info = await end_and_merge_competition(
                    self.bot, comp, reason="timer"
                )
                print(
                    f"[Invites] Competition #{info['competition_id']} beendet — "
                    f"+{info['added']} Invites gemerged"
                )
                guild = self.bot.get_guild(int(comp["guild_id"]))
                ch_id = comp.get("channel_id")
                if guild and ch_id:
                    channel = guild.get_channel(int(ch_id))
                    if isinstance(channel, discord.TextChannel):
                        try:
                            await channel.send(
                                embed=success_embed(
                                    "Invite-Competition beendet",
                                    f"**{info['title']}** ist vorbei.\n"
                                    f"Competition-Invites wurden auf den "
                                    f"normalen Stand **angerechnet** "
                                    f"(+{info['added']} neu).\n"
                                    f"Leaderboard: `/invite leaderboard`",
                                )
                            )
                        except discord.HTTPException:
                            pass
        except Exception as e:
            print(f"[Invites] Competition-Expire fehlgeschlagen: {e!r}")

    @competition_expire_loop.before_loop
    async def before_competition_expire(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._refresh_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._refresh_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        cache = self._invite_cache.setdefault(invite.guild.id, {})
        cache[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        cache = self._invite_cache.get(invite.guild.id)
        if cache is not None:
            cache.pop(invite.code, None)

    async def _refresh_invites(self, guild: discord.Guild) -> None:
        try:
            invites = await guild.invites()
        except discord.Forbidden:
            print(
                f"[Invites] Keine Berechtigung „Einladungen verwalten“ "
                f"auf {guild.name} — Invite-Rewards deaktiviert."
            )
            self._invite_cache[guild.id] = {}
            return
        except discord.HTTPException as e:
            print(f"[Invites] Cache fehlgeschlagen ({guild.name}): {e}")
            return
        self._invite_cache[guild.id] = {
            inv.code: (inv.uses or 0) for inv in invites
        }

    async def _find_used_invite(
        self, guild: discord.Guild
    ) -> discord.Invite | None:
        before = self._invite_cache.get(guild.id, {})
        try:
            after_list = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None
        after = {inv.code: inv for inv in after_list}
        self._invite_cache[guild.id] = {
            code: (inv.uses or 0) for code, inv in after.items()
        }
        used: discord.Invite | None = None
        for code, inv in after.items():
            prev = before.get(code, 0)
            cur = inv.uses or 0
            if cur > prev:
                if used is None or cur - prev > (used.uses or 0) - before.get(
                    used.code, 0
                ):
                    used = inv
        return used

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild = member.guild
        used = await self._find_used_invite(guild)
        if used is None or used.inviter is None:
            return
        inviter = used.inviter
        if inviter.id == member.id or inviter.bot:
            return

        comp = await self.bot.db.get_active_invite_competition(guild.id)
        # Abgelaufene active Competition sofort mergen
        if comp:
            ends = _parse_ends_at(str(comp.get("ends_at") or ""))
            if ends and datetime.now(timezone.utc) >= ends:
                await end_and_merge_competition(self.bot, comp, reason="late")
                comp = None

        if comp:
            new = await self.bot.db.record_competition_invite_join(
                int(comp["id"]),
                guild.id,
                member.id,
                inviter.id,
                used.code,
            )
            if not new:
                return
            ccount = await self.bot.db.count_competition_invites(
                int(comp["id"]), inviter.id
            )
            try:
                await inviter.send(
                    embed=success_embed(
                        "Competition-Invite!",
                        f"**{guild.name}** — {member.mention} zählt für "
                        f"**{comp.get('title') or 'Competition'}**.\n"
                        f"**Competition-Stand:** {ccount} Invites\n"
                        f"(Normal-Invites pausiert bis Ende — danach "
                        f"werden Competition-Invites draufgezählt.)\n"
                        f"`/invite leaderboard`",
                    )
                )
            except discord.HTTPException:
                pass
            return

        # Normalbetrieb
        new = await self.bot.db.record_invite_join(
            guild.id, member.id, inviter.id, used.code
        )
        if not new:
            return

        granted = await grant_due_invite_rewards(self.bot, guild.id, inviter.id)
        count = await self.bot.db.count_invites(guild.id, inviter.id)
        if granted:
            bal = await self.bot.db.get_credits(guild.id, inviter.id)
            parts = [
                f"**{g['milestone']} Invites** → "
                f"{format_price(g['currency'])} "
                f"({format_credits(g['credits'])} Credits)"
                for g in granted
            ]
            try:
                await inviter.send(
                    embed=success_embed(
                        "Invite-Reward!",
                        f"Auf **{guild.name}**: {member.mention} ist über "
                        f"deinen Invite gekommen.\n"
                        f"**Stand:** {count} Invites\n\n"
                        + "\n".join(parts)
                        + f"\n\nCredits jetzt: **{format_credits(bal)}**",
                    )
                )
            except discord.HTTPException:
                pass

    # ── /invites ────────────────────────────────────────────────────

    @app_commands.command(
        name="invites",
        description="Deine Invite-Stats (normal + Competition)",
    )
    @app_commands.describe(user="Optional: anderen User anzeigen (Staff)")
    async def invites_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        target = user or interaction.user
        if user is not None and user.id != interaction.user.id:
            if not await is_staff(self.bot, interaction):
                await interaction.response.send_message(
                    embed=error_embed("Nur Staff darf fremde Invites sehen"),
                    ephemeral=True,
                )
                return

        await grant_due_invite_rewards(
            self.bot, interaction.guild.id, target.id
        )

        count = await self.bot.db.count_invites(interaction.guild.id, target.id)
        claimed = await self.bot.db.list_claimed_invite_milestones(
            interaction.guild.id, target.id
        )
        next_m = None
        for milestone, currency in config.INVITE_REWARDS:
            if milestone not in claimed:
                next_m = (milestone, currency)
                break

        lines = [
            f"**Normale Invites:** {count}",
            "",
            "**Rewards:**",
            _reward_table_text(),
        ]
        if claimed:
            lines.append(
                "\n**Bereits erhalten:** "
                + ", ".join(str(m) for m in sorted(claimed))
            )
        if next_m:
            need = max(0, next_m[0] - count)
            lines.append(
                f"\n**Nächstes Ziel:** {next_m[0]} "
                f"({format_price(next_m[1])}) — noch **{need}**"
            )
        else:
            lines.append("\nAlle Meilensteine erreicht.")

        comp = await self.bot.db.get_active_invite_competition(
            interaction.guild.id
        )
        if comp:
            ccount = await self.bot.db.count_competition_invites(
                int(comp["id"]), target.id
            )
            lines.append(
                f"\n🏁 **Competition aktiv:** {comp.get('title')}\n"
                f"Dein Competition-Stand: **{ccount}** "
                f"(bis `{comp.get('ends_at')}`)\n"
                f"Normale Zählung pausiert — danach werden Competition-"
                f"Invites draufgezählt."
            )

        await interaction.response.send_message(
            embed=success_embed(
                f"Invites — {getattr(target, 'display_name', target.name)}",
                "\n".join(lines),
            ),
        )

    @app_commands.command(
        name="inviterewards",
        description="Invite-Reward-Tabelle anzeigen",
    )
    async def inviterewards(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=success_embed(
                "Invite Rewards",
                "Lade Freunde ein — Rewards werden **automatisch als Credits** "
                "gutgeschrieben:\n\n"
                + _reward_table_text()
                + "\n\n`/invites` · `/invite leaderboard`",
            ),
        )

    # ── /invite … ───────────────────────────────────────────────────

    invite = app_commands.Group(
        name="invite",
        description="Invite-Leaderboard und Competitions",
    )

    @invite.command(
        name="leaderboard",
        description="Invite-Leaderboard (normal oder Competition)",
    )
    @app_commands.describe(
        mode="normal = Gesamt · competition = laufende Competition",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Normal (Gesamt)", value="normal"),
            app_commands.Choice(name="Competition", value="competition"),
            app_commands.Choice(name="Auto", value="auto"),
        ]
    )
    async def invite_leaderboard(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str] | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return

        mode_v = mode.value if mode else "auto"
        comp = await self.bot.db.get_active_invite_competition(
            interaction.guild.id
        )
        use_comp = mode_v == "competition" or (mode_v == "auto" and comp)

        if use_comp:
            if not comp:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Keine Competition",
                        "Aktuell läuft keine Invite-Competition.\n"
                        "Staff: `/invite competition-start`",
                    ),
                    ephemeral=True,
                )
                return
            rows = await self.bot.db.competition_invite_leaderboard(
                int(comp["id"]), limit=15
            )
            body = (
                f"**{comp.get('title')}**\n"
                f"Ende: `{comp.get('ends_at')}` (UTC)\n"
                f"_Während der Competition zählen Invites bei 0 neu._\n\n"
                + _format_leaderboard_lines(rows, interaction.guild)
            )
            title = "🏁 Invite Competition Leaderboard"
        else:
            rows = await self.bot.db.invite_leaderboard(
                interaction.guild.id, limit=15
            )
            body = (
                "**Gesamt-Invites** (inkl. bereits gemergter Competitions)\n\n"
                + _format_leaderboard_lines(rows, interaction.guild)
            )
            if comp:
                body += (
                    f"\n\n_Competition **{comp.get('title')}** läuft — "
                    f"`/invite leaderboard mode:Competition`_"
                )
            title = "📊 Invite Leaderboard"

        await interaction.response.send_message(
            embed=base_embed(title, body[:3900]),
        )

    @invite.command(
        name="competition-start",
        description="Invite-Competition starten (Staff) — Zähler reset für die Dauer",
    )
    @app_commands.describe(
        hours="Dauer in Stunden",
        title="Optionaler Titel",
        channel="Ankündigungs-Channel (Standard: hier)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def competition_start(
        self,
        interaction: discord.Interaction,
        hours: app_commands.Range[float, 1.0, 720.0],
        title: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return

        existing = await self.bot.db.get_active_invite_competition(
            interaction.guild.id
        )
        if existing:
            await interaction.response.send_message(
                embed=error_embed(
                    "Schon aktiv",
                    f"**{existing.get('title')}** läuft noch bis "
                    f"`{existing.get('ends_at')}`.\n"
                    f"Zuerst `/invite competition-end`.",
                ),
                ephemeral=True,
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

        now = datetime.now(timezone.utc)
        ends = now + timedelta(hours=float(hours))
        starts_s = now.strftime("%Y-%m-%d %H:%M:%S")
        ends_s = ends.strftime("%Y-%m-%d %H:%M:%S")
        comp_title = (title or "Invite Competition").strip()[:100]

        await interaction.response.defer(ephemeral=True)
        cid = await self.bot.db.create_invite_competition(
            interaction.guild.id,
            title=comp_title,
            starts_at=starts_s,
            ends_at=ends_s,
            created_by=interaction.user.id,
            channel_id=target.id,
        )

        announce = base_embed(
            f"🏁 {comp_title}",
            f"**Invite-Competition gestartet!**\n\n"
            f"• Dauer: **{hours:g} Stunden** (bis `{ends_s}` UTC)\n"
            f"• Competition-Invites starten bei **0**\n"
            f"• Normale Invite-Zählung ist währenddessen pausiert\n"
            f"• Nach Ende werden Competition-Invites auf den normalen "
            f"Stand **draufgezählt** (+ Rewards)\n\n"
            f"Stats: `/invites` · Board: `/invite leaderboard`",
        )
        try:
            msg = await target.send(embed=announce)
            await self.bot.db.update_invite_competition(
                cid, message_id=msg.id
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=warn_embed(
                    "Competition aktiv, Ankündigung fehlgeschlagen",
                    f"ID `{cid}` · {e}",
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=success_embed(
                "Competition gestartet",
                f"**{comp_title}** (#{cid}) bis `{ends_s}`\n"
                f"Ankündigung: {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @invite.command(
        name="competition-end",
        description="Laufende Invite-Competition beenden und Invites mergen (Staff)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def competition_end(
        self, interaction: discord.Interaction
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        comp = await self.bot.db.get_active_invite_competition(
            interaction.guild.id
        )
        if not comp:
            await interaction.response.send_message(
                embed=error_embed("Keine aktive Competition"), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        info = await end_and_merge_competition(
            self.bot, comp, reason="manual"
        )
        rows = await self.bot.db.competition_invite_leaderboard(
            int(comp["id"]), limit=10
        )
        board = _format_leaderboard_lines(rows, interaction.guild)

        await interaction.followup.send(
            embed=success_embed(
                "Competition beendet",
                f"**{info['title']}**\n"
                f"Competition-Invites auf Normalstand: **+{info['added']}** neu\n"
                f"Inviter: {info['inviters']} · neue Rewards: {info['rewards']}\n\n"
                f"**Final Board:**\n{board}",
            ),
            ephemeral=True,
        )

        ch_id = comp.get("channel_id")
        if ch_id:
            channel = interaction.guild.get_channel(int(ch_id))
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(
                        embed=success_embed(
                            "Invite-Competition beendet",
                            f"**{info['title']}** ist vorbei.\n"
                            f"Invites wurden auf den normalen Stand "
                            f"angerechnet (+{info['added']}).\n\n"
                            f"{board}\n\n"
                            f"`/invite leaderboard` · `/invites`",
                        )
                    )
                except discord.HTTPException:
                    pass

    @invite.command(
        name="competition-status",
        description="Status der Invite-Competition",
    )
    async def competition_status(
        self, interaction: discord.Interaction
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        comp = await self.bot.db.get_active_invite_competition(
            interaction.guild.id
        )
        if not comp:
            await interaction.response.send_message(
                embed=success_embed(
                    "Competition",
                    "Keine aktive Competition.\n"
                    "Staff: `/invite competition-start`",
                ),
                ephemeral=True,
            )
            return
        rows = await self.bot.db.competition_invite_leaderboard(
            int(comp["id"]), limit=10
        )
        await interaction.response.send_message(
            embed=base_embed(
                f"🏁 {comp.get('title')}",
                f"**Ende:** `{comp.get('ends_at')}` UTC\n"
                f"**Start:** `{comp.get('starts_at')}`\n\n"
                + _format_leaderboard_lines(rows, interaction.guild)
                + "\n\nNach Ende: Competition-Invites → normaler Stand.",
            ),
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(InvitesCog(bot))
