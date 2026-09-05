"""Vouch-Anfrage per DM nach abgeschlossenem Kauf (inkl. Sterne-Buttons)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from integrations.shop_api import shop_api
from utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot import ShopBot


class VouchMessageModal(discord.ui.Modal, title="Vouch schreiben"):
    message = discord.ui.TextInput(
        label="Dein Feedback",
        style=discord.TextStyle.paragraph,
        placeholder="Wie war der Kauf / das Pack?",
        max_length=1000,
        required=True,
    )

    def __init__(self, bot: ShopBot, rating: int) -> None:
        super().__init__()
        self.bot = bot
        self.rating = rating

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from cogs.vouch import _submit_local_vouch, _submit_website_vouch

        text = str(self.message.value).strip()
        if not text:
            await interaction.response.send_message(
                embed=error_embed("Leerer Text"), ephemeral=True
            )
            return
        if await _submit_local_vouch(self.bot, interaction, self.rating, text):
            return
        if shop_api.enabled:
            result = await _submit_website_vouch(interaction, self.rating, text)
            if result:
                await interaction.response.send_message(
                    embed=success_embed(
                        "Vouch gesendet",
                        "Danke für deine Bewertung!",
                    ),
                    ephemeral=True,
                )
                return
        await interaction.response.send_message(
            embed=error_embed(
                "Kein offener Kauf",
                "Es gibt keine Bestellung mehr für einen Vouch, "
                "oder er wurde schon abgegeben.",
            ),
            ephemeral=True,
        )


class VouchRatingView(discord.ui.View):
    """Persistente Sterne-Buttons für Pack-DM / Vouch-Anfrage."""

    def __init__(self, bot: ShopBot | None = None) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _pick(
        self, interaction: discord.Interaction, rating: int
    ) -> None:
        bot = self.bot or interaction.client  # type: ignore[assignment]
        await interaction.response.send_modal(VouchMessageModal(bot, rating))

    @discord.ui.button(
        label="1 ★",
        style=discord.ButtonStyle.secondary,
        custom_id="vouchrate:1",
        row=0,
    )
    async def star1(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._pick(interaction, 1)

    @discord.ui.button(
        label="2 ★",
        style=discord.ButtonStyle.secondary,
        custom_id="vouchrate:2",
        row=0,
    )
    async def star2(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._pick(interaction, 2)

    @discord.ui.button(
        label="3 ★",
        style=discord.ButtonStyle.primary,
        custom_id="vouchrate:3",
        row=0,
    )
    async def star3(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._pick(interaction, 3)

    @discord.ui.button(
        label="4 ★",
        style=discord.ButtonStyle.success,
        custom_id="vouchrate:4",
        row=0,
    )
    async def star4(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._pick(interaction, 4)

    @discord.ui.button(
        label="5 ★",
        style=discord.ButtonStyle.success,
        custom_id="vouchrate:5",
        row=0,
    )
    async def star5(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._pick(interaction, 5)


async def send_vouch_request_dm(
    bot: discord.Client,
    user: discord.abc.User,
    *,
    order_ref_text: str,
    product_hint: str = "dein Kauf",
) -> bool:
    embed = discord.Embed(
        title="⭐ Bewertung abgeben",
        description=(
            f"Danke für **{product_hint}** ({order_ref_text})!\n\n"
            "Tippe auf die Sterne unten und schreib kurz dein Feedback — "
            "oder nutze `/vouch`."
        ),
        color=config.EMBED_COLOR,
    )
    if shop_api.enabled:
        frontend = (config.FRONTEND_URL or "http://localhost:3000").rstrip("/")
        embed.add_field(
            name="Auf der Website",
            value=f"[Profil öffnen]({frontend}/account)",
            inline=False,
        )
    embed.set_footer(text="Einmalig pro Kauf · TxTEmpire Shop")

    view = VouchRatingView(bot)  # type: ignore[arg-type]
    try:
        await user.send(
            content="🙏 **Wie war dein Einkauf?**",
            embed=embed,
            view=view,
        )
    except discord.HTTPException:
        return False
    return True
