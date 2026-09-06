from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.credits import format_credits
from utils.embeds import error_embed, format_price, success_embed
from utils.snipe_limits import get_snipe_quota
from utils.snipe_prices import (
    SNIPE_PLAN_LIFETIME,
    get_snipe_prices,
    normalize_snipe_plan,
    premium_snipe_label,
    snipe_plan_title,
    snipe_price_for_plan,
)
from utils.username_sniper import MAX_LENGTH_NAMES, PLATFORMS
from views.snipe_panel import (
    SnipePremiumPanelBuyView,
    post_or_refresh_snipe_panel,
    run_snipe_check,
    run_snipe_length,
    _snipe_status_body,
)
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


PlatformChoice = app_commands.Choice[str]


def _platform_choices() -> list[PlatformChoice]:
    return [
        app_commands.Choice(name="Minecraft", value="minecraft"),
        app_commands.Choice(name="Roblox", value="roblox"),
        app_commands.Choice(name="Discord", value="discord"),
    ]


async def open_snipe_premium_ticket(
    bot: ShopBot, interaction: discord.Interaction, *, plan: int
) -> None:
    from cogs.tickets import create_order_ticket

    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return

    plan = normalize_snipe_plan(plan)
    prices = await get_snipe_prices(bot, interaction.guild.id)
    price = snipe_price_for_plan(prices, plan)
    title = f"Snipe Premium {snipe_plan_title(plan)}"
    cart_rows = [
        {
            "item_id": None,
            "category_id": None,
            "name": title,
            "price": float(price),
            "qty": 1,
            "pack_dm_text": "",
            "pack_link": "",
            "pack_file": "",
            "item_role_id": None,
            "category_role_id": None,
        }
    ]

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    try:
        channel = await create_order_ticket(
            bot,
            interaction,
            cart_rows=cart_rows,
            clear_cart=False,
            credits_enabled=False,
            order_kind="snipe_premium",
            credits_amount=float(plan),
        )
    except ValueError as e:
        await interaction.followup.send(
            embed=error_embed("Premium-Kauf fehlgeschlagen", str(e)[:1500]),
            ephemeral=True,
        )
        return
    except Exception as e:
        await interaction.followup.send(
            embed=error_embed(
                "Premium-Kauf fehlgeschlagen",
                f"`{type(e).__name__}: {e}`",
            ),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=success_embed(
            "Snipe-Premium Ticket",
            f"**{snipe_plan_title(plan)}** Premium (= {format_price(price)})\n"
            f"→ **{premium_snipe_label(plan=plan)}**\n"
            f"Ticket: {channel.mention}\n\n"
            "Zahle wie gewohnt — nach Staff-Bestätigung ist Premium aktiv.",
        ),
        ephemeral=True,
    )


class UsernameSniperCog(commands.Cog):
    """Minecraft / Discord / Roblox Username-Verfügbarkeit."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    snipe = app_commands.Group(
        name="snipe",
        description="Username Sniper (Minecraft / Discord / Roblox)",
    )

    @app_commands.command(
        name="snipepanel",
        description="Username-Sniper Panel posten oder aktualisieren (Staff)",
    )
    @app_commands.describe(channel="Ziel-Channel (Standard: aktuell)")
    @app_commands.default_permissions(manage_guild=True)
    async def snipepanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed("Kein Channel", "Bitte einen Text-Channel wählen."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        msg = await post_or_refresh_snipe_panel(
            self.bot, interaction.guild, target
        )
        await interaction.followup.send(
            embed=success_embed(
                "Sniper-Panel",
                f"Panel in {target.mention}: {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @snipe.command(
        name="check",
        description="Bestimmte Usernames prüfen — zeigt nur bestätigte freie",
    )
    @app_commands.describe(
        platform="Plattform",
        usernames="Ein oder mehrere Names (Leerzeichen/Komma getrennt)",
        details="Auch vergebene/unklare Treffer anzeigen",
    )
    @app_commands.choices(platform=_platform_choices())
    async def snipe_check(
        self,
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        usernames: str,
        details: bool = False,
    ) -> None:
        parts = [
            p.strip()
            for chunk in usernames.replace(",", " ").split()
            for p in [chunk]
            if p.strip()
        ]
        if not parts:
            await interaction.response.send_message(
                embed=error_embed("Keine Names", "Bitte mindestens einen Username."),
                ephemeral=True,
            )
            return
        await run_snipe_check(
            self.bot, interaction, platform.value, parts, details=details
        )

    @snipe.command(
        name="length",
        description="Zufällige Names einer Länge — so viele freie wie angegeben",
    )
    @app_commands.describe(
        platform="Plattform",
        length="Exakte Username-Länge",
        count="Wie viele freie Names finden",
        prefix="Optionaler Prefix",
        suffix="Optionaler Suffix",
        details="Auch vergebene/unklare Treffer anzeigen",
    )
    @app_commands.choices(platform=_platform_choices())
    async def snipe_length(
        self,
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        length: app_commands.Range[int, 2, 32],
        count: app_commands.Range[int, 1, 50] = 10,
        prefix: str = "",
        suffix: str = "",
        details: bool = False,
    ) -> None:
        if int(count) > MAX_LENGTH_NAMES:
            await interaction.response.send_message(
                embed=error_embed(
                    "Zu viele Names",
                    f"Max. **{MAX_LENGTH_NAMES}** freie Names pro Lauf.",
                ),
                ephemeral=True,
            )
            return
        plat = platform.value
        spec = PLATFORMS[plat]
        if not (spec.min_len <= int(length) <= spec.max_len):
            await interaction.response.send_message(
                embed=error_embed(
                    "Ungültige Länge",
                    f"{spec.label}: {spec.min_len}–{spec.max_len}",
                ),
                ephemeral=True,
            )
            return
        await run_snipe_length(
            self.bot,
            interaction,
            plat,
            length=int(length),
            count=int(count),
            prefix=(prefix or "").strip(),
            suffix=(suffix or "").strip(),
            details=details,
        )

    @snipe.command(
        name="status",
        description="Dein Snipe-Kontingent und Premium-Status",
    )
    async def snipe_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        staff = await is_staff(self.bot, interaction)
        quota = await get_snipe_quota(
            self.bot,
            interaction.guild.id,
            interaction.user.id,
            is_staff=staff,
        )
        extra = ""
        if not quota.get("premium") and not staff:
            prices = await get_snipe_prices(self.bot, interaction.guild.id)
            extra = (
                f"\n\nPremium: `/snipepremium` —\n"
                f"• 14 Tage ({format_price(prices['price_14'])}) → "
                f"**{config.SNIPE_PREMIUM_14_DAILY}/Tag**\n"
                f"• 30 Tage ({format_price(prices['price_30'])}) → "
                f"**unbegrenzt**\n"
                f"• Lifetime ({format_price(prices['price_lifetime'])}) → "
                f"**unbegrenzt**"
            )
        await interaction.response.send_message(
            embed=success_embed("Snipe-Status", _snipe_status_body(quota) + extra),
            ephemeral=True,
        )

    @snipe.command(
        name="stats",
        description="Sniper-Statistik: freie Usernames gesamt",
    )
    async def snipe_stats(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        stats = await self.bot.db.get_snipe_stats(interaction.guild.id)
        plat_lines = (
            "\n".join(
                f"• **{PLATFORMS[key].emoji} {PLATFORMS[key].label}:** {cnt}"
                if key in PLATFORMS
                else f"• `{key}`: {cnt}"
                for key, cnt in stats["by_platform"]
            )
            or "_Noch keine Treffer_"
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Snipe-Statistik",
                f"**Free Usernames gefunden (gesamt):** **{stats['free_total']}**\n"
                f"**Einzigartige Names:** {stats['free_unique']}\n"
                f"**User mit Treffern:** {stats['finders']}\n"
                f"**Quota-Nutzung:** {stats['usage_total']}\n\n"
                f"**Nach Plattform:**\n{plat_lines}\n\n"
                f"**Premium-Käufe:** {stats['premium_purchases']}\n"
                f"**Unique Käufer:** {stats['premium_buyers']}\n"
                f"**Aktives Premium:** {stats['premium_active']}",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="snipepremium",
        description="Snipe Premium kaufen (14 Tage / 30 Tage / Lifetime)",
    )
    async def snipepremium(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        prices = await get_snipe_prices(self.bot, interaction.guild.id)
        quota = await get_snipe_quota(
            self.bot, interaction.guild.id, interaction.user.id
        )
        extra = ""
        if quota["premium"]:
            extra = (
                f"\n\nDein Premium: `{quota['expires_at']}` "
                f"({premium_snipe_label(plan=30 if quota.get('unlimited') else 14)}; "
                f"Kauf verlängert)."
            )
            if quota.get("lifetime"):
                extra = "\n\nDu hast bereits **Lifetime**. Ein Kauf bleibt Lifetime."
        await interaction.response.send_message(
            embed=success_embed(
                "Snipe Premium",
                f"Free: **{config.SNIPE_FREE_DAILY}/Tag**\n"
                f"• **14 Tage** — {format_price(prices['price_14'])} "
                f"oder **{format_credits(prices['credits_14'])} Credits** "
                f"→ **{config.SNIPE_PREMIUM_14_DAILY}/Tag**\n"
                f"• **30 Tage** — {format_price(prices['price_30'])} "
                f"oder **{format_credits(prices['credits_30'])} Credits** "
                f"→ **unbegrenzte Names**\n"
                f"• **Lifetime** — {format_price(prices['price_lifetime'])} "
                f"oder **{format_credits(prices['credits_lifetime'])} Credits** "
                f"→ **unbegrenzte Names**"
                f"{extra}",
            ),
            view=SnipePremiumPanelBuyView(self.bot),
            ephemeral=True,
        )

    @app_commands.command(
        name="snipegrant",
        description="Snipe Premium manuell vergeben (Staff)",
    )
    @app_commands.describe(
        user="User",
        plan="14 Tage (30/Tag), 30 Tage (unbegrenzt) oder Lifetime",
    )
    @app_commands.choices(
        plan=[
            app_commands.Choice(name="14 Tage (30/Tag)", value=14),
            app_commands.Choice(name="30 Tage (unbegrenzt)", value=30),
            app_commands.Choice(name="Lifetime (unbegrenzt)", value=36500),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def snipegrant(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        plan: app_commands.Choice[int],
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        resolved = normalize_snipe_plan(int(plan.value))
        expires = await self.bot.db.extend_snipe_premium(
            interaction.guild.id, user.id, resolved
        )
        until = "Lifetime" if resolved == SNIPE_PLAN_LIFETIME else expires
        await interaction.response.send_message(
            embed=success_embed(
                "Premium vergeben",
                f"{user.mention}: **{snipe_plan_title(resolved)}** "
                f"({premium_snipe_label(plan=resolved)})\n"
                f"Aktiv bis `{until}`",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="snipeprices",
        description="Snipe-Premium Preise anzeigen oder setzen (Staff)",
    )
    @app_commands.describe(
        price_14="14-Tage Preis in Shop-Währung (z.B. 500000)",
        price_30="30-Tage Preis in Shop-Währung (z.B. 1000000)",
        price_lifetime="Lifetime-Preis in Shop-Währung (z.B. 6000000)",
        credits_14="14-Tage Preis in Credits (optional)",
        credits_30="30-Tage Preis in Credits (optional)",
        credits_lifetime="Lifetime-Preis in Credits (optional)",
        reset="Auf .env/Config-Defaults zurücksetzen",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def snipeprices(
        self,
        interaction: discord.Interaction,
        price_14: float | None = None,
        price_30: float | None = None,
        price_lifetime: float | None = None,
        credits_14: float | None = None,
        credits_30: float | None = None,
        credits_lifetime: float | None = None,
        reset: bool = False,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return

        if reset:
            await self.bot.db.update_guild_settings(
                interaction.guild.id,
                snipe_price_14=None,
                snipe_price_30=None,
                snipe_price_lifetime=None,
                snipe_credits_14=None,
                snipe_credits_30=None,
                snipe_credits_lifetime=None,
            )
        else:
            fields: dict = {}
            pairs = (
                ("snipe_price_14", price_14, "Preis"),
                ("snipe_price_30", price_30, "Preis"),
                ("snipe_price_lifetime", price_lifetime, "Preis"),
                ("snipe_credits_14", credits_14, "Credits"),
                ("snipe_credits_30", credits_30, "Credits"),
                ("snipe_credits_lifetime", credits_lifetime, "Credits"),
            )
            for key, value, kind in pairs:
                if value is None:
                    continue
                if value < 0:
                    await interaction.response.send_message(
                        embed=error_embed(f"{kind} muss ≥ 0 sein"),
                        ephemeral=True,
                    )
                    return
                fields[key] = (
                    round(float(value), 2) if "credits" in key else float(value)
                )
            if fields:
                await self.bot.db.update_guild_settings(
                    interaction.guild.id, **fields
                )

        prices = await get_snipe_prices(self.bot, interaction.guild.id)
        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        src = (
            "Server-Preise"
            if any(
                settings.get(k) is not None
                for k in (
                    "snipe_price_14",
                    "snipe_price_30",
                    "snipe_price_lifetime",
                    "snipe_credits_14",
                    "snipe_credits_30",
                    "snipe_credits_lifetime",
                )
            )
            else "Config/.env Defaults"
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Snipe-Premium Preise",
                f"**Quelle:** {src}\n\n"
                f"• **14 Tage** — {format_price(prices['price_14'])} "
                f"/ **{format_credits(prices['credits_14'])} Credits** "
                f"→ {config.SNIPE_PREMIUM_14_DAILY}/Tag\n"
                f"• **30 Tage** — {format_price(prices['price_30'])} "
                f"/ **{format_credits(prices['credits_30'])} Credits** "
                f"→ unbegrenzt\n"
                f"• **Lifetime** — {format_price(prices['price_lifetime'])} "
                f"/ **{format_credits(prices['credits_lifetime'])} Credits** "
                f"→ unbegrenzt\n\n"
                "Danach `/snipepanel` neu posten, damit das Panel die Preise zeigt.",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(UsernameSniperCog(bot))
