"""
tweak_panel.py
================

Ein gemeinsames Kauf-Panel für BEIDE Tweak-Produkte (Ferdi Mousetweaks +
y3zz GPU Tweaks) statt zwei getrennter Panels. Preise werden live aus den
jeweiligen Settings-Tabellen gelesen - also weiterhin ganz normal per
`/keysetup` (Mousetweaks) bzw. `/gtkeysetup` (GPU Tweaks) änderbar, hier
nur zusammen dargestellt. Die Buttons rufen exakt dieselben Handler auf
wie die einzelnen Panels (cogs/mousetweaks_keys.py, cogs/gputweaks_keys.py) -
kein Code doppelt, keine zweite Preis-/Key-Logik.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from cogs import gputweaks_keys as gt
from cogs import mousetweaks_keys as mt
from utils.embeds import base_embed, error_embed, format_price, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


async def _combined_panel_embed(bot: "ShopBot", guild_id: int) -> discord.Embed:
    mt_settings = await mt._get_settings(bot, guild_id)
    gt_settings = await gt._get_settings(bot, guild_id)

    def lines_for(settings: dict, lic_module, price_for) -> str:
        out = []
        for tier in (lic_module.TIER_14D, lic_module.TIER_30D, lic_module.TIER_LIFETIME):
            price = price_for(settings, tier)
            price_txt = format_price(price) if price > 0 else "Preis auf Anfrage"
            out.append(f"**{lic_module.TIER_LABELS[tier]}** — {price_txt}")
        return "\n".join(out)

    embed = base_embed(
        "🔑 Tweaks — Lizenzkeys",
        "Wähle unten dein Produkt und die Laufzeit. Danach wird ein privates "
        "Ticket erstellt — keine Hardware-ID nötig, der Key bindet sich "
        "automatisch an dein Gerät, sobald du ihn zum ersten Mal einträgst.",
    )
    embed.add_field(
        name="🖱️ Ferdi Mousetweaks",
        value="Maus-Tweak-Konfigurator (DPI, Debounce, RGB, Makros, Profile)\n"
        + lines_for(mt_settings, mt.mtlic, mt._price_for),
        inline=True,
    )
    embed.add_field(
        name="🖥️ y3zz GPU Tweaks",
        value="GPU-Optimierer (Deep Scan Auto-Tune, Power/Takt/Lüfter, Farbtiefe)\n"
        + lines_for(gt_settings, gt.gtlic, gt._price_for),
        inline=True,
    )
    return embed


class TweakShopPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Mousetweaks kaufen",
        style=discord.ButtonStyle.success,
        custom_id="tweakpanel:buy_mt",
        emoji="🖱️",
        row=0,
    )
    async def buy_mt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await mt.handle_buy_key(self.bot, interaction)

    @discord.ui.button(
        label="GPU Tweaks kaufen",
        style=discord.ButtonStyle.success,
        custom_id="tweakpanel:buy_gt",
        emoji="🖥️",
        row=0,
    )
    async def buy_gt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await gt.handle_buy_key(self.bot, interaction)

    @discord.ui.button(
        label="Mousetweaks HWID Reset",
        style=discord.ButtonStyle.secondary,
        custom_id="tweakpanel:reset_mt",
        emoji="🔄",
        row=1,
    )
    async def reset_mt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await mt.handle_reset_hwid(self.bot, interaction)

    @discord.ui.button(
        label="GPU Tweaks HWID Reset",
        style=discord.ButtonStyle.secondary,
        custom_id="tweakpanel:reset_gt",
        emoji="🔄",
        row=1,
    )
    async def reset_gt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await gt.handle_reset_hwid(self.bot, interaction)


class TweakPanelCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="tweakpanel",
        description="Gemeinsames Kauf-Panel für Mousetweaks + GPU Tweaks posten (Staff)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def tweakpanel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        embed = await _combined_panel_embed(self.bot, interaction.guild.id)
        msg = await target.send(embed=embed, view=TweakShopPanelView(self.bot))
        await interaction.followup.send(
            embed=success_embed("Panel gepostet", f"In {target.mention}: {msg.jump_url}"), ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await bot.add_cog(TweakPanelCog(bot))
