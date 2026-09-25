"""
macro_panel.py
==============

Ein gemeinsames Kauf-Panel für die Minecraft-Macro-Produkte (HugoSMP Macro +
Quick Invsee) statt zwei getrennter Panels — gleiches Prinzip wie
cogs/tweak_panel.py. Preise werden live aus den jeweiligen Settings-Tabellen
gelesen (weiterhin per `/hugomacro setup` bzw. `/qikey setup` änderbar).
Die Buttons rufen exakt dieselben Handler auf wie die einzelnen Panels
(cogs/hugomacro_keys.py, cogs/quickinvsee_keys.py) — keine zweite
Preis-/Key-Logik.

Quick Invsee lässt sich pro Server ein-/ausblenden (z.B. wenn ein Server nur
HugoSMP Macro verkaufen will): `/macropanel quickinvsee:<An/Aus>` setzt und
merkt sich das für den nächsten Post; ohne den Parameter bleibt der zuletzt
gesetzte Wert (Standard: an).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs import hugomacro_keys as hm
from cogs import quickinvsee_keys as qi
from utils.embeds import base_embed, error_embed, format_price, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


async def _ensure_tables(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS macro_panel_settings (
            guild_id INTEGER PRIMARY KEY,
            show_quickinvsee INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    await bot.db.db.commit()


async def _get_show_qi(bot: "ShopBot", guild_id: int) -> bool:
    row = await bot.db.fetchone(
        "SELECT show_quickinvsee FROM macro_panel_settings WHERE guild_id = ?", (guild_id,)
    )
    return bool(row["show_quickinvsee"]) if row else True


async def _set_show_qi(bot: "ShopBot", guild_id: int, value: bool) -> None:
    await bot.db.db.execute(
        """
        INSERT INTO macro_panel_settings (guild_id, show_quickinvsee) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET show_quickinvsee = excluded.show_quickinvsee
        """,
        (guild_id, int(value)),
    )
    await bot.db.db.commit()


def _lines(settings: dict, tiers, labels: dict, price_for, fmt) -> str:
    out = []
    for tier in tiers:
        price = price_for(settings, tier)
        price_txt = fmt(price) if price > 0 else "Preis auf Anfrage"
        out.append(f"**{labels[tier]}** — {price_txt}")
    return "\n".join(out)


async def _combined_panel_embed(bot: "ShopBot", guild_id: int, show_qi: bool) -> discord.Embed:
    hm_settings = await hm._get_settings(bot, guild_id)
    embed = base_embed(
        "⛏️ Minecraft Macros — Lizenzkeys",
        "Wähle unten dein Produkt und die Laufzeit. Danach wird ein privates "
        "Ticket erstellt — keine Hardware-ID nötig, der Key bindet sich "
        "automatisch an dein Gerät, sobald du ihn ingame einträgst.",
    )
    embed.add_field(
        name="💰 HugoSMP Macro",
        value="Sell-Macro (läuft bis Stopp) & Spawner-Macro (Drop per Button) · "
        f"Zahlung ingame an `{hm._pay_ign()}`\n"
        + _lines(hm_settings, hm.TIER_ORDER, hm.TIER_LABELS, hm._price_for, hm._ingame),
        inline=True,
    )
    if show_qi:
        qi_settings = await qi._get_settings(bot, guild_id)
        embed.add_field(
            name="🔍 Quick Invsee",
            value="/invsee per Hotkey auf den anvisierten Spieler, Gegner-Anzeige, "
            "Auto-Invsee bei /rtpqueue\n"
            + _lines(qi_settings, qi.TIER_ORDER, qi.qilic.TIER_LABELS, qi._price_for, format_price),
            inline=True,
        )
    return embed


class MacroShopPanelView(discord.ui.View):
    def __init__(self, bot: "ShopBot", show_qi: bool = True) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        if not show_qi:
            self.remove_item(self.buy_qi)
            self.remove_item(self.reset_qi)

    @discord.ui.button(
        label="HugoSMP Macro kaufen",
        style=discord.ButtonStyle.success,
        custom_id="macropanel:buy_hm",
        emoji="💰",
        row=0,
    )
    async def buy_hm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await hm.handle_buy_hugomacro(self.bot, interaction)

    @discord.ui.button(
        label="Quick Invsee kaufen",
        style=discord.ButtonStyle.success,
        custom_id="macropanel:buy_qi",
        emoji="🔍",
        row=0,
    )
    async def buy_qi(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await qi.handle_buy_key(self.bot, interaction)

    @discord.ui.button(
        label="Quick Invsee HWID Reset",
        style=discord.ButtonStyle.secondary,
        custom_id="macropanel:reset_qi",
        emoji="🔄",
        row=1,
    )
    async def reset_qi(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await qi.handle_reset_hwid(self.bot, interaction)


class MacroPanelCog(commands.Cog):
    def __init__(self, bot: "ShopBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="macropanel",
        description="Gemeinsames Kauf-Panel für HugoSMP Macro (+ Quick Invsee) posten (Staff)",
    )
    @app_commands.describe(
        channel="Ziel-Channel (Standard: aktuell)",
        quickinvsee="Quick Invsee im Panel zeigen (Standard: zuletzt gesetzter Wert, sonst an)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def macropanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        quickinvsee: Optional[bool] = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(embed=error_embed("Kein Channel"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if quickinvsee is not None:
            await _set_show_qi(self.bot, interaction.guild.id, quickinvsee)
        show_qi = await _get_show_qi(self.bot, interaction.guild.id)
        embed = await _combined_panel_embed(self.bot, interaction.guild.id, show_qi)
        msg = await target.send(embed=embed, view=MacroShopPanelView(self.bot, show_qi))
        await interaction.followup.send(
            embed=success_embed(
                "Panel gepostet",
                f"In {target.mention}: {msg.jump_url}\nQuick Invsee: {'✅ sichtbar' if show_qi else '⛔ ausgeblendet'}",
            ),
            ephemeral=True,
        )


async def setup(bot: "ShopBot") -> None:
    await _ensure_tables(bot)
    await bot.add_cog(MacroPanelCog(bot))
