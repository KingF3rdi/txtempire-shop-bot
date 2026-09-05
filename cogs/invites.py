from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.credits import currency_to_credits, format_credits
from utils.embeds import error_embed, format_price, success_embed

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


class InvitesCog(commands.Cog):
    """Invite-Tracking + automatische Credit-Rewards."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot
        # guild_id → {code: uses}
        self._invite_cache: dict[int, dict[str, int]] = {}

    async def cog_load(self) -> None:
        # Cache nach Ready füllen (falls Cog nach on_ready geladen)
        if self.bot.is_ready():
            for guild in self.bot.guilds:
                await self._refresh_invites(guild)

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
        gid = invite.guild.id
        cache = self._invite_cache.setdefault(gid, {})
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
        # Gelöschte Invites die benutzt wurden: nicht zuverlässig — skip
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

        new = await self.bot.db.record_invite_join(
            guild.id, member.id, inviter.id, used.code
        )
        if not new:
            return

        granted = await grant_due_invite_rewards(self.bot, guild.id, inviter.id)
        count = await self.bot.db.count_invites(guild.id, inviter.id)

        # Optional kurze Bestätigung an Inviter (DM)
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

    @app_commands.command(
        name="invites",
        description="Deine Invite-Anzahl und Rewards",
    )
    @app_commands.describe(user="Optional: anderen User anzeigen (Staff)")
    async def invites(
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
            from views.ticket_views import is_staff

            if not await is_staff(self.bot, interaction):
                await interaction.response.send_message(
                    embed=error_embed("Nur Staff darf fremde Invites sehen"),
                    ephemeral=True,
                )
                return

        # Offene Rewards nachziehen (z.B. nach Bot-Update)
        if isinstance(target, discord.Member) or target.id == interaction.user.id:
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
            f"**Invites:** {count}",
            "",
            "**Rewards:**",
            _reward_table_text(),
        ]
        if claimed:
            lines.append(
                "\n**Bereits erhalten:** "
                + ", ".join(f"{m}" for m in sorted(claimed))
            )
        if next_m:
            need = max(0, next_m[0] - count)
            lines.append(
                f"\n**Nächstes Ziel:** {next_m[0]} Invites "
                f"({format_price(next_m[1])}) — noch **{need}**"
            )
        else:
            lines.append("\nAlle Meilensteine erreicht.")

        await interaction.response.send_message(
            embed=success_embed(
                f"Invites — {target.display_name}",
                "\n".join(lines),
            ),
            ephemeral=True,
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
                + "\n\nFortschritt: `/invites`",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(InvitesCog(bot))
