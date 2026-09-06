from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.credits import format_credits
from utils.embeds import base_embed, error_embed, format_price, success_embed, warn_embed
from utils.snipe_limits import (
    format_snipe_quota_line,
    get_snipe_quota,
    reserve_snipe_quota,
)
from utils.snipe_prices import (
    SNIPE_PLAN_14,
    SNIPE_PLAN_30,
    SNIPE_PLAN_LIFETIME,
    get_snipe_prices,
    premium_snipe_label,
    snipe_credits_for_plan,
    snipe_plan_title,
)
from utils.username_sniper import (
    MAX_LENGTH_NAMES,
    MAX_NAMES_PER_RUN,
    PLATFORMS,
    check_many,
    find_available_names,
    finish_snipe,
    format_results_embed_body,
    generate_candidates,
    start_snipe,
)
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


async def build_snipe_panel_embed(bot: ShopBot, guild_id: int) -> discord.Embed:
    prices = await get_snipe_prices(bot, guild_id)
    free_total = await bot.db.get_snipe_free_total(guild_id)
    embed = base_embed(
        "🎯 Username Sniper",
        "Finde **bestätigt freie** Usernames auf:\n"
        "🟩 Minecraft · 🟥 Roblox · 🟦 Discord\n\n"
        f"**Free Usernames gefunden (gesamt):** **{free_total}**\n\n"
        "_Kontingent gilt pro Kategorie (Minecraft/Roblox/Discord getrennt)._\n"
        f"• Free: **{config.SNIPE_FREE_DAILY} Names/Tag**\n"
        f"• 14 Tage Premium: **{config.SNIPE_PREMIUM_14_DAILY} Names/Tag**\n"
        f"• 30 Tage Premium: **{config.SNIPE_PREMIUM_30_DAILY} Names/Tag**\n"
        f"• Lifetime: **{config.SNIPE_PREMIUM_LIFETIME_DAILY} Names/Tag**\n\n"
        "**Name prüfen** — bestimmte Usernames checken\n"
        "**Nach Länge** — so viele **freie** Names finden, wie du angibst\n\n"
        "Ergebnis zeigt **nur API-bestätigte verfügbare** Names "
        "(Unklar/Rate-Limit zählt nicht als frei).\n\n"
        "Auch: `/snipe check` · `/snipe length` · `/snipe status` · `/snipe stats`",
    )
    embed.add_field(
        name="Premium",
        value=(
            f"14 Tage — {format_price(prices['price_14'])} "
            f"oder **{format_credits(prices['credits_14'])} Credits** "
            f"({config.SNIPE_PREMIUM_14_DAILY}/Tag je Kategorie)\n"
            f"30 Tage — {format_price(prices['price_30'])} "
            f"oder **{format_credits(prices['credits_30'])} Credits** "
            f"({config.SNIPE_PREMIUM_30_DAILY}/Tag je Kategorie)\n"
            f"Lifetime — {format_price(prices['price_lifetime'])} "
            f"oder **{format_credits(prices['credits_lifetime'])} Credits** "
            f"({config.SNIPE_PREMIUM_LIFETIME_DAILY}/Tag je Kategorie)"
        ),
        inline=False,
    )
    embed.set_footer(text="TxtEmpire Sniper · Nur bestätigte FREE-Treffer")
    return embed


class SnipeCheckModal(discord.ui.Modal, title="Username prüfen"):
    usernames = discord.ui.TextInput(
        label="Username(s)",
        placeholder="name1, name2, name3",
        style=discord.TextStyle.paragraph,
        max_length=400,
        required=True,
    )

    def __init__(self, bot: ShopBot, platform: str) -> None:
        super().__init__()
        self.bot = bot
        self.platform = platform

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parts = [
            p.strip()
            for chunk in str(self.usernames.value).replace(",", " ").split()
            for p in [chunk]
            if p.strip()
        ]
        if not parts:
            await interaction.response.send_message(
                embed=error_embed("Keine Names"), ephemeral=True
            )
            return
        await run_snipe_check(self.bot, interaction, self.platform, parts)


class SnipeLengthModal(discord.ui.Modal, title="Nach Länge snipen"):
    length = discord.ui.TextInput(
        label="Länge",
        placeholder="z.B. 4",
        max_length=2,
        required=True,
    )
    count = discord.ui.TextInput(
        label="Anzahl freie Names",
        placeholder=f"1–{MAX_LENGTH_NAMES} (Standard 10)",
        max_length=2,
        required=False,
    )
    prefix = discord.ui.TextInput(
        label="Prefix (optional)",
        required=False,
        max_length=16,
    )
    suffix = discord.ui.TextInput(
        label="Suffix (optional)",
        required=False,
        max_length=16,
    )
    clean = discord.ui.TextInput(
        label="Nur Buchstaben? (ja/nein)",
        placeholder="ja = clean Username, ohne Zahlen/_ (Standard: nein)",
        required=False,
        max_length=4,
    )

    def __init__(self, bot: ShopBot, platform: str) -> None:
        super().__init__()
        self.bot = bot
        self.platform = platform

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            length = int(str(self.length.value).strip())
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Länge muss eine Zahl sein"), ephemeral=True
            )
            return
        count_raw = str(self.count.value or "").strip()
        try:
            count = int(count_raw) if count_raw else 10
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Anzahl muss eine Zahl sein"), ephemeral=True
            )
            return
        count = max(1, min(count, MAX_LENGTH_NAMES))
        prefix = str(self.prefix.value or "").strip()
        suffix = str(self.suffix.value or "").strip()
        clean = str(self.clean.value or "").strip().lower() in (
            "ja",
            "j",
            "yes",
            "y",
            "true",
            "1",
        )
        await run_snipe_length(
            self.bot,
            interaction,
            self.platform,
            length=length,
            count=count,
            prefix=prefix,
            suffix=suffix,
            clean=clean,
        )


async def run_snipe_check(
    bot: ShopBot,
    interaction: discord.Interaction,
    platform: str,
    parts: list[str],
    *,
    details: bool = False,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return
    if len(parts) > MAX_NAMES_PER_RUN:
        await interaction.response.send_message(
            embed=error_embed(
                "Zu viele Names",
                f"Max. **{MAX_NAMES_PER_RUN}** pro Lauf.",
            ),
            ephemeral=True,
        )
        return

    if not start_snipe(interaction.guild.id, interaction.user.id):
        await interaction.response.send_message(
            embed=error_embed(
                "Suche läuft bereits",
                "Du hast schon eine laufende Sniper-Suche. Bitte warte, bis sie fertig ist.",
            ),
            ephemeral=True,
        )
        return
    try:
        staff = await is_staff(bot, interaction)
        try:
            allowed, quota = await reserve_snipe_quota(
                bot,
                interaction.guild.id,
                interaction.user.id,
                platform,
                len(parts),
                is_staff=staff,
            )
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Tageslimit", str(e)), ephemeral=True
            )
            return

        capped = parts[:allowed]
        cap_note = ""
        if allowed < len(parts):
            cap_note = (
                f"\n_Nur **{allowed}** von {len(parts)} Names geprüft "
                f"(Tageslimit)._"
            )

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        spec = PLATFORMS[platform]
        status = await interaction.followup.send(
            embed=warn_embed(
                "🎯 Sniper läuft…",
                f"{spec.emoji} **{spec.label}** — {len(capped)} Name(n)…\n"
                f"{format_snipe_quota_line(quota)}",
            ),
            ephemeral=True,
        )
        results = await check_many(platform, capped, limit=allowed)
        body, free = format_results_embed_body(platform, results, show_all=details)
        if free:
            await bot.db.record_snipe_finds(
                interaction.guild.id, interaction.user.id, platform, free
            )
        body = f"{body}{cap_note}\n{format_snipe_quota_line(quota)}"
        embed = (
            success_embed(f"✅ {len(free)} verfügbar — {spec.label}", body)
            if free
            else warn_embed(f"Keine freien Treffer — {spec.label}", body)
        )
        await status.edit(embed=embed)
    finally:
        finish_snipe(interaction.guild.id, interaction.user.id)


async def run_snipe_length(
    bot: ShopBot,
    interaction: discord.Interaction,
    platform: str,
    *,
    length: int,
    count: int,
    prefix: str = "",
    suffix: str = "",
    clean: bool = False,
    details: bool = False,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return

    spec = PLATFORMS[platform]
    if not (spec.min_len <= length <= spec.max_len):
        send = (
            interaction.response.send_message
            if not interaction.response.is_done()
            else interaction.followup.send
        )
        await send(
            embed=error_embed(
                "Ungültige Länge",
                f"{spec.label}: {spec.min_len}–{spec.max_len}",
            ),
            ephemeral=True,
        )
        return
    try:
        probe = generate_candidates(
            platform,
            length,
            count=1,
            prefix=prefix,
            suffix=suffix,
            clean=clean,
        )
    except ValueError as e:
        send = (
            interaction.response.send_message
            if not interaction.response.is_done()
            else interaction.followup.send
        )
        await send(embed=error_embed("Ungültig", str(e)), ephemeral=True)
        return
    if not probe:
        send = (
            interaction.response.send_message
            if not interaction.response.is_done()
            else interaction.followup.send
        )
        await send(
            embed=error_embed("Keine gültigen Names"),
            ephemeral=True,
        )
        return

    if not start_snipe(interaction.guild.id, interaction.user.id):
        await interaction.response.send_message(
            embed=error_embed(
                "Suche läuft bereits",
                "Du hast schon eine laufende Sniper-Suche. Bitte warte, bis sie fertig ist.",
            ),
            ephemeral=True,
        )
        return
    try:
        staff = await is_staff(bot, interaction)
        try:
            allowed, quota = await reserve_snipe_quota(
                bot,
                interaction.guild.id,
                interaction.user.id,
                platform,
                count,
                is_staff=staff,
            )
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Tageslimit", str(e)), ephemeral=True
            )
            return

        cap_note = ""
        if allowed < count:
            cap_note = (
                f"\n_Anfrage auf **{allowed}** freie Names gekürzt (Tageslimit)._"
            )

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        clean_note = " · ✨ clean (nur Buchstaben)" if clean else ""
        status = await interaction.followup.send(
            embed=warn_embed(
                "🎯 Length-Sniper läuft…",
                f"{spec.emoji} **{spec.label}** · len **{length}**{clean_note} · "
                f"sucht **{allowed}** freie Names…\n"
                f"{format_snipe_quota_line(quota)}",
            ),
            ephemeral=True,
        )
        try:
            free_results, checks = await find_available_names(
                platform,
                length,
                allowed,
                prefix=prefix,
                suffix=suffix,
                clean=clean,
            )
        except ValueError as e:
            await status.edit(embed=error_embed("Ungültig", str(e)))
            return

        body, free = format_results_embed_body(
            platform, free_results, show_all=details
        )
        if free:
            await bot.db.record_snipe_finds(
                interaction.guild.id, interaction.user.id, platform, free
            )
        found_note = (
            f"\nGesucht: **{allowed}** freie Names · geprüft: **{checks}**"
            f"{cap_note}"
        )
        if len(free) < allowed:
            found_note += (
                f"\n_Nur **{len(free)}** von {allowed} freien Names gefunden._"
            )
        body = f"{body}{found_note}\n{format_snipe_quota_line(quota)}"
        title_suffix = f"(len {length}{', clean' if clean else ''})"
        embed = (
            success_embed(
                f"✅ {len(free)} verfügbar — {spec.label} {title_suffix}",
                body,
            )
            if free
            else warn_embed(
                f"Keine freien Treffer — {spec.label} {title_suffix}",
                body,
            )
        )
        await status.edit(embed=embed)
    finally:
        finish_snipe(interaction.guild.id, interaction.user.id)


class PlatformPickView(discord.ui.View):
    """Kurzlebige Plattform-Auswahl vor Modal."""

    def __init__(self, bot: ShopBot, *, mode: str) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.mode = mode  # check | length

    async def _open(
        self, interaction: discord.Interaction, platform: str
    ) -> None:
        if self.mode == "check":
            await interaction.response.send_modal(
                SnipeCheckModal(self.bot, platform)
            )
        else:
            await interaction.response.send_modal(
                SnipeLengthModal(self.bot, platform)
            )

    @discord.ui.button(label="Minecraft", style=discord.ButtonStyle.success, emoji="🟩")
    async def mc(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, "minecraft")

    @discord.ui.button(label="Roblox", style=discord.ButtonStyle.danger, emoji="🟥")
    async def rbx(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, "roblox")

    @discord.ui.button(label="Discord", style=discord.ButtonStyle.primary, emoji="🟦")
    async def dc(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, "discord")


class SnipePanelView(discord.ui.View):
    """Persistentes Sniper-Panel."""

    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Name prüfen",
        style=discord.ButtonStyle.success,
        custom_id="snipepanel:check",
        emoji="🔎",
        row=0,
    )
    async def check_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            embed=base_embed(
                "Plattform wählen",
                "Für welchen Dienst soll geprüft werden?",
            ),
            view=PlatformPickView(self.bot, mode="check"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Nach Länge",
        style=discord.ButtonStyle.primary,
        custom_id="snipepanel:length",
        emoji="📏",
        row=0,
    )
    async def length_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            embed=base_embed(
                "Plattform wählen",
                "Zufällige Names welcher Plattform snipen?",
            ),
            view=PlatformPickView(self.bot, mode="length"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Mein Status",
        style=discord.ButtonStyle.secondary,
        custom_id="snipepanel:status",
        emoji="📊",
        row=0,
    )
    async def status_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        staff = await is_staff(self.bot, interaction)
        quotas = {
            p: await get_snipe_quota(
                self.bot,
                interaction.guild.id,
                interaction.user.id,
                p,
                is_staff=staff,
            )
            for p in PLATFORMS
        }
        await interaction.response.send_message(
            embed=success_embed("Snipe-Status", _snipe_status_body(quotas)),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Premium kaufen",
        style=discord.ButtonStyle.primary,
        custom_id="snipepanel:premium",
        emoji="⭐",
        row=1,
    )
    async def premium(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        prices = await get_snipe_prices(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed(
                "Snipe Premium",
                f"• **14 Tage** — {format_price(prices['price_14'])} / "
                f"{format_credits(prices['credits_14'])} Credits → "
                f"**{config.SNIPE_PREMIUM_14_DAILY}/Tag je Kategorie**\n"
                f"• **30 Tage** — {format_price(prices['price_30'])} / "
                f"{format_credits(prices['credits_30'])} Credits → "
                f"**{config.SNIPE_PREMIUM_30_DAILY}/Tag je Kategorie**\n"
                f"• **Lifetime** — {format_price(prices['price_lifetime'])} / "
                f"{format_credits(prices['credits_lifetime'])} Credits → "
                f"**{config.SNIPE_PREMIUM_LIFETIME_DAILY}/Tag je Kategorie**\n\n"
                "_Minecraft/Roblox/Discord haben je ein eigenes Kontingent._\n\n"
                "Wähle Dauer und Zahlungsart:",
            ),
            view=SnipePremiumPanelBuyView(self.bot),
            ephemeral=True,
        )


def _snipe_status_body(quotas: dict[str, dict]) -> str:
    """quotas: {platform: quota_dict} — ein Kontingent je Kategorie."""
    any_quota = next(iter(quotas.values()))

    if any_quota.get("staff"):
        header = "**Staff** — kein Limit."
    elif any_quota.get("lifetime"):
        header = "**Lifetime Premium**"
    elif any_quota.get("premium") and any_quota.get("unlimited"):
        header = f"**Premium 30 Tage** bis `{any_quota['expires_at']}`"
    elif any_quota.get("premium"):
        header = f"**Premium 14 Tage** bis `{any_quota['expires_at']}`"
    else:
        header = "**Free**"

    lines = "\n".join(
        f"{PLATFORMS[p].emoji} **{PLATFORMS[p].label}** — "
        f"**{q['used']}/{q['limit']}** (noch {q['remaining']})"
        for p, q in quotas.items()
    )
    return f"{header}\n{lines}"


class SnipePremiumPanelBuyView(discord.ui.View):
    """14 / 30 / Lifetime — Zahlung (Ticket) oder Credits."""

    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(
        label="14 Tage · Zahlen",
        style=discord.ButtonStyle.primary,
        emoji="💳",
        row=0,
    )
    async def pay_14(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from cogs.username_sniper import open_snipe_premium_ticket

        await open_snipe_premium_ticket(self.bot, interaction, plan=SNIPE_PLAN_14)

    @discord.ui.button(
        label="14 Tage · Credits",
        style=discord.ButtonStyle.success,
        emoji="🪙",
        row=0,
    )
    async def credits_14(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await buy_snipe_premium_with_credits(
            self.bot, interaction, plan=SNIPE_PLAN_14
        )

    @discord.ui.button(
        label="30 Tage · Zahlen",
        style=discord.ButtonStyle.primary,
        emoji="💳",
        row=1,
    )
    async def pay_30(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from cogs.username_sniper import open_snipe_premium_ticket

        await open_snipe_premium_ticket(self.bot, interaction, plan=SNIPE_PLAN_30)

    @discord.ui.button(
        label="30 Tage · Credits",
        style=discord.ButtonStyle.success,
        emoji="🪙",
        row=1,
    )
    async def credits_30(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await buy_snipe_premium_with_credits(
            self.bot, interaction, plan=SNIPE_PLAN_30
        )

    @discord.ui.button(
        label="Lifetime · Zahlen",
        style=discord.ButtonStyle.primary,
        emoji="💳",
        row=2,
    )
    async def pay_life(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from cogs.username_sniper import open_snipe_premium_ticket

        await open_snipe_premium_ticket(
            self.bot, interaction, plan=SNIPE_PLAN_LIFETIME
        )

    @discord.ui.button(
        label="Lifetime · Credits",
        style=discord.ButtonStyle.success,
        emoji="🪙",
        row=2,
    )
    async def credits_life(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await buy_snipe_premium_with_credits(
            self.bot, interaction, plan=SNIPE_PLAN_LIFETIME
        )


async def buy_snipe_premium_with_credits(
    bot: ShopBot, interaction: discord.Interaction, *, plan: int
) -> None:
    from utils.snipe_prices import normalize_snipe_plan

    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return
    plan = normalize_snipe_plan(plan)
    prices = await get_snipe_prices(bot, interaction.guild.id)
    need = round(float(snipe_credits_for_plan(prices, plan)), 2)
    bal = await bot.db.get_credits(interaction.guild.id, interaction.user.id)
    if bal < need:
        msg = error_embed(
            "Zu wenig Credits",
            f"Benötigt: **{format_credits(need)}** · "
            f"Guthaben: **{format_credits(bal)}**\n"
            "Credits am Buy-Panel kaufen oder **Zahlen** wählen.",
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=msg, ephemeral=True)
        else:
            await interaction.followup.send(embed=msg, ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    ok = await bot.db.try_deduct_credits(
        interaction.guild.id, interaction.user.id, need
    )
    if not ok:
        await interaction.followup.send(
            embed=error_embed("Zu wenig Credits", "Abzug fehlgeschlagen."),
            ephemeral=True,
        )
        return

    expires = await bot.db.extend_snipe_premium(
        interaction.guild.id, interaction.user.id, plan
    )
    until = "Lifetime" if plan == SNIPE_PLAN_LIFETIME else expires
    new_bal = await bot.db.get_credits(interaction.guild.id, interaction.user.id)
    await interaction.followup.send(
        embed=success_embed(
            "Premium aktiviert",
            f"**{snipe_plan_title(plan)}** Snipe Premium (per Credits)\n"
            f"−**{format_credits(need)}** Credits · Rest: **{format_credits(new_bal)}**\n"
            f"Aktiv bis `{until}` · **{premium_snipe_label(plan=plan)}**",
        ),
        ephemeral=True,
    )


async def post_or_refresh_snipe_panel(
    bot: ShopBot,
    guild: discord.Guild,
    channel: discord.TextChannel,
    *,
    force_new: bool = False,
) -> discord.Message:
    embed = await build_snipe_panel_embed(bot, guild.id)
    view = SnipePanelView(bot)
    row = await bot.db.get_snipe_panel(guild.id)

    if (
        not force_new
        and row
        and row.get("channel_id")
        and row.get("message_id")
        and int(row["channel_id"]) == channel.id
    ):
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            await msg.edit(embed=embed, view=view)
            return msg
        except (discord.NotFound, discord.HTTPException):
            pass

    msg = await channel.send(embed=embed, view=view)
    await bot.db.set_snipe_panel(
        guild.id, channel_id=channel.id, message_id=msg.id
    )
    return msg
