from __future__ import annotations

import re
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.embeds import error_embed, success_embed
from views.boost_packs import open_boost_pack_picker, send_boost_thanks_dm
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot

_SECOND_BOOST_RE = re.compile(
    r"(2\.?\s*(mal|x|times?)|zweite[nr]?|twice|×\s*2|x2|for the 2)",
    re.IGNORECASE,
)


class BoostCog(commands.Cog):
    """Server-Boost → Pack-Auswahl (1× = 5, 2× = 15) + Dankes-DM."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    async def _apply_boost_tier(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        tier: int,
    ) -> None:
        tier = 2 if tier >= 2 else 1
        packs = (
            config.BOOST_PACKS_TIER2 if tier >= 2 else config.BOOST_PACKS_TIER1
        )
        before = await self.bot.db.get_boost_claim(guild.id, member.id)
        prev_allowed = int(before["packs_allowed"]) if before else 0
        upgrade = tier >= 2 and prev_allowed < packs

        row = await self.bot.db.upsert_boost_claim(
            guild.id,
            member.id,
            boost_count=tier,
            packs_allowed=packs,
            thanks=True,
        )
        # DM nur bei neuem Claim oder Upgrade auf Tier 2
        should_dm = before is None or upgrade
        if should_dm:
            await send_boost_thanks_dm(
                self.bot,
                member,
                packs_allowed=int(row["packs_allowed"]),
                tier=tier,
                upgrade=upgrade and before is not None,
            )

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if before.premium_since is None and after.premium_since is not None:
            await self._apply_boost_tier(after.guild, after, tier=1)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if message.type != discord.MessageType.premium_guild_subscription:
            return
        member = message.author
        if not isinstance(member, discord.Member):
            return
        content = message.system_content or message.content or ""
        existing = await self.bot.db.get_boost_claim(
            message.guild.id, member.id
        )
        if existing is None:
            tier = 2 if _SECOND_BOOST_RE.search(content) else 1
            await self._apply_boost_tier(message.guild, member, tier=tier)
            return

        if int(existing["boost_count"]) >= 2:
            return

        # Doppel-Event vom 1. Boost (member_update + System) ignorieren
        if not _SECOND_BOOST_RE.search(content):
            thanks = existing.get("last_thanks_at")
            if thanks:
                from datetime import datetime, timezone

                try:
                    ts = datetime.strptime(
                        str(thanks), "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    if age < 120:
                        return
                except ValueError:
                    pass
        await self._apply_boost_tier(message.guild, member, tier=2)


    @app_commands.command(
        name="boostpacks",
        description="Boost-Belohnung: freie Packs auswählen",
    )
    async def boostpacks(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        member = interaction.user
        if isinstance(member, discord.Member) and member.premium_since is None:
            claim = await self.bot.db.get_boost_claim(
                interaction.guild.id, member.id
            )
            if not claim:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Kein Boost",
                        "Booste den Server, um Packs freizuschalten.",
                    ),
                    ephemeral=True,
                )
                return
        await open_boost_pack_picker(
            self.bot, interaction, interaction.guild.id
        )

    @app_commands.command(
        name="booststatus",
        description="Dein Boost-Pack-Kontingent anzeigen",
    )
    async def booststatus(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        claim = await self.bot.db.get_boost_claim(
            interaction.guild.id, interaction.user.id
        )
        boosting = False
        if isinstance(interaction.user, discord.Member):
            boosting = interaction.user.premium_since is not None
        if not claim:
            await interaction.response.send_message(
                embed=success_embed(
                    "Boost-Status",
                    f"Server-Boost aktiv: **{'ja' if boosting else 'nein'}**\n"
                    "Noch kein Claim — nach dem Boost kommt eine DM.",
                ),
                ephemeral=True,
            )
            return
        import json

        try:
            claimed = json.loads(claim.get("claimed_item_ids") or "[]")
        except json.JSONDecodeError:
            claimed = []
        left = max(0, int(claim["packs_allowed"]) - len(claimed))
        await interaction.response.send_message(
            embed=success_embed(
                "Boost-Status",
                f"**Tier:** {claim['boost_count']}\n"
                f"**Packs erlaubt:** {claim['packs_allowed']}\n"
                f"**Bereits gewählt:** {len(claimed)}\n"
                f"**Noch offen:** {left}\n"
                f"Server-Boost aktiv: **{'ja' if boosting else 'nein'}**",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="boostgrant",
        description="Boost-Pack-Kontingent manuell setzen (Staff)",
    )
    @app_commands.describe(
        user="Booster",
        tier="1 = 5 Packs, 2 = 15 Packs",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def boostgrant(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        tier: app_commands.Range[int, 1, 2] = 1,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        await self._apply_boost_tier(interaction.guild, user, tier=int(tier))
        packs = (
            config.BOOST_PACKS_TIER2
            if int(tier) >= 2
            else config.BOOST_PACKS_TIER1
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Boost gesetzt",
                f"{user.mention}: Tier **{tier}** → **{packs}** Packs "
                f"(+ Dankes-DM wenn neu/Upgrade).",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(BoostCog(bot))
