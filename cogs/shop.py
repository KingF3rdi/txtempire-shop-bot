from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from integrations.catalog_sync import sync_shop_catalog
from integrations.shop_api import shop_api
from utils.embeds import base_embed, success_embed
from utils.panels import (
    PanelFilter,
    apply_panel_filter,
    build_buy_panel_embed,
    ensure_buy_panel_slot_view,
    ensure_buy_panel_view,
    get_panel_filter_for_slot,
    is_valid_buy_panel_message,
    panel_filter_summary,
    refresh_slot_panel,
)
from views.shop_views import BuyPanelView, CartView, ShopPanelView
from config import PAYMENT_NOTICE

if TYPE_CHECKING:
    from bot import ShopBot


async def _resolve_target_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None,
) -> discord.TextChannel | None:
    if channel is not None:
        return channel
    if isinstance(interaction.channel, discord.TextChannel):
        return interaction.channel
    return None


async def _ensure_catalog(bot: ShopBot, guild_id: int) -> dict | None:
    """Synchronisiert Kategorien von der Website, wenn API konfiguriert ist."""
    if not shop_api.enabled:
        return None
    return await sync_shop_catalog(bot, guild_id)


async def _post_slot_panel(
    bot: ShopBot,
    guild: discord.Guild,
    target: discord.TextChannel,
    slot: int,
    *,
    title: str | None = None,
    force_repost: bool = False,
) -> discord.Message:
    cats = await bot.db.list_categories(guild.id)
    settings = await bot.db.ensure_guild(guild.id)
    panel_filter, stored_title = await get_panel_filter_for_slot(bot, guild.id, slot)
    panel_title = title or stored_title
    await ensure_buy_panel_slot_view(bot, slot)
    filtered = apply_panel_filter(cats, panel_filter)
    row = await bot.db.ensure_buy_panel_slot(guild.id, slot)
    credits_on = bool(int(row.get("credits_enabled") or 0))
    embed = build_buy_panel_embed(
        categories=filtered,
        settings=settings,
        title=panel_title,
        panel_filter=panel_filter,
        slot=slot,
        credits_enabled=credits_on,
    )
    view = BuyPanelView(bot, panel_slot=slot, credits_enabled=credits_on)
    channel_id = row.get("channel_id")
    message_id = row.get("message_id")
    if channel_id and message_id and int(channel_id) == target.id:
        try:
            old = await target.fetch_message(int(message_id))
            # Immer editieren statt löschen/neu senden (außer force_repost)
            if not force_repost:
                try:
                    await old.edit(embed=embed, view=view)
                    return old
                except discord.HTTPException:
                    pass
            if force_repost:
                try:
                    await old.delete()
                except discord.HTTPException:
                    pass
        except discord.NotFound:
            pass
    msg = await target.send(embed=embed, view=view)
    await bot.db.update_buy_panel_message(
        guild.id, slot, channel_id=target.id, message_id=msg.id
    )
    return msg


async def _refresh_slot_panel(bot: ShopBot, guild: discord.Guild, slot: int) -> str:
    return await refresh_slot_panel(bot, guild, slot)


class ShopCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="syncshop",
        description="Kategorien und Produkte von der Website übernehmen",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def syncshop(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not shop_api.enabled:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed(
                    "API nicht konfiguriert",
                    "Setze `SHOP_API_URL` und `BOT_API_KEY` in der `.env`.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = await sync_shop_catalog(self.bot, interaction.guild.id)
        if result.get("error"):
            from utils.embeds import error_embed

            await interaction.followup.send(
                embed=error_embed(
                    "Sync fehlgeschlagen",
                    "Katalog konnte nicht von der Website geladen werden.",
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=success_embed(
                "Shop synchronisiert",
                f"**{result.get('categories', 0)}** Kategorien · "
                f"**{result.get('items', 0)}** Produkte übernommen"
                + (
                    f"\nEntfernt: {result.get('removed_categories', 0)} Kategorien, "
                    f"{result.get('removed_items', 0)} Produkte"
                    if result.get("removed_categories") or result.get("removed_items")
                    else ""
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="buypanelconfig",
        description="Kategorien für Buy Panel 1 oder 2 einstellen",
    )
    @app_commands.describe(
        slot="Welches Panel konfiguriert werden soll",
        mode="Welche Kategorien im Panel sichtbar sind",
        title="Optional: eigener Titel auf dem Panel",
        credits="Credits / Quick Buy auf diesem Panel aktivieren",
    )
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="Buy Panel 1", value=1),
            app_commands.Choice(name="Buy Panel 2", value=2),
        ],
        mode=[
            app_commands.Choice(name="Alle Kategorien", value="all"),
            app_commands.Choice(name="Nur diese Kategorien", value="include"),
            app_commands.Choice(name="Alle außer diese", value="exclude"),
        ],
        credits=[
            app_commands.Choice(name="Credits an (Credits-Button + Quick Buy)", value=1),
            app_commands.Choice(name="Credits aus", value=0),
        ],
    )
    @app_commands.default_permissions(manage_guild=True)
    async def buypanelconfig(
        self,
        interaction: discord.Interaction,
        slot: app_commands.Choice[int],
        mode: app_commands.Choice[str],
        title: str | None = None,
        credits: app_commands.Choice[int] | None = None,
    ) -> None:
        assert interaction.guild is not None
        from utils.embeds import error_embed
        from views.panel_config import PanelCategoryConfigView

        panel_slot = slot.value
        filter_mode = mode.value
        credits_enabled = None if credits is None else bool(credits.value)

        if filter_mode in ("include", "exclude"):
            cats = await self.bot.db.list_categories(interaction.guild.id)
            if not cats:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Keine Kategorien",
                        "Erst Kategorien anlegen oder `/syncshop` ausführen.",
                    ),
                    ephemeral=True,
                )
                return

            mode_label = (
                "Nur diese Kategorien"
                if filter_mode == "include"
                else "Alle außer diese"
            )
            action = (
                "Wähle die Kategorien, die **sichtbar** sein sollen."
                if filter_mode == "include"
                else "Wähle die Kategorien, die **ausgeblendet** werden sollen."
            )
            header = (
                f"**{mode_label}** — Buy Panel **{panel_slot}**\n"
                f"{action}\n"
                "Klicke Kategorien zum **An-/Abwählen** (✅ = aktiv). "
                "Danach **Speichern**."
            )
            if title:
                header += f"\n\n_Titel: {title}_"
            if credits_enabled is not None:
                header += f"\n_Credits: {'an' if credits_enabled else 'aus'}_"

            row = await self.bot.db.ensure_buy_panel_slot(
                interaction.guild.id, panel_slot
            )
            pf = PanelFilter.from_slot_row(row)
            initial_ids = (
                set(pf.category_ids)
                if pf.mode == filter_mode and pf.category_ids
                else set()
            )

            async def on_confirm(
                inter: discord.Interaction, selected: list[dict]
            ) -> None:
                ids = [int(c["id"]) for c in selected]
                await self._save_buy_panel_config(
                    inter,
                    panel_slot,
                    filter_mode,
                    ids,
                    title,
                    credits_enabled=credits_enabled,
                    edit=True,
                )

            view = PanelCategoryConfigView(
                cats,
                on_confirm=on_confirm,
                header=header,
                initial_selected_ids=initial_ids,
            )
            msg = await interaction.response.send_message(
                content=view._status_text(),
                view=view,
                ephemeral=True,
            )
            view.message = msg
            return

        await self._save_buy_panel_config(
            interaction,
            panel_slot,
            filter_mode,
            [],
            title,
            credits_enabled=credits_enabled,
        )

    async def _save_buy_panel_config(
        self,
        interaction: discord.Interaction,
        slot: int,
        filter_mode: str,
        category_ids: list[int],
        title: str | None,
        *,
        credits_enabled: bool | None = None,
        edit: bool = False,
    ) -> None:
        assert interaction.guild is not None

        row = await self.bot.db.get_buy_panel_slot(interaction.guild.id, slot)
        needs_refresh = bool(row and row.get("message_id"))
        if needs_refresh and not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        await self.bot.db.set_buy_panel_slot(
            interaction.guild.id,
            slot,
            filter_mode=filter_mode,
            category_ids=category_ids,
            title=title,
            credits_enabled=credits_enabled,
        )
        await ensure_buy_panel_slot_view(self.bot, slot)

        pf = PanelFilter(mode=filter_mode, category_ids=tuple(category_ids))
        cats = await self.bot.db.list_categories(interaction.guild.id)
        filtered = apply_panel_filter(cats, pf)
        names = ", ".join(c["name"] for c in filtered[:10])
        if len(filtered) > 10:
            names += f" … (+{len(filtered) - 10})"

        saved = await self.bot.db.ensure_buy_panel_slot(
            interaction.guild.id, slot
        )
        credits_line = (
            f"**Credits:** {'an ✅' if int(saved.get('credits_enabled') or 0) else 'aus'}\n"
        )

        refresh_note = ""
        if needs_refresh:
            refresh_result = await refresh_slot_panel(
                self.bot, interaction.guild, slot
            )
            refresh_note = f"\n\n**Panel aktualisiert:** {refresh_result}"

        embed = success_embed(
            f"Buy Panel {slot} konfiguriert",
            f"**Filter:** {panel_filter_summary(pf)}\n"
            + credits_line
            + (f"**Titel:** {title}\n" if title else "")
            + (f"**Sichtbar:** {names or '—'}\n\n" if filtered else "")
            + f"Posten: `/buypanel` → **Buy Panel {slot}**"
            + refresh_note,
        )
        if edit:
            if interaction.response.is_done():
                await interaction.edit_original_response(
                    content=None, embed=embed, view=None
                )
            else:
                await interaction.response.edit_message(
                    content=None, embed=embed, view=None
                )
        elif interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="buypanelcredits",
        description="Credits-Button + Quick Buy für Buy Panel 1 oder 2 an/aus",
    )
    @app_commands.describe(
        slot="Welches Panel",
        enabled="Credits aktivieren (Button erscheint auf dem Panel)",
    )
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="Buy Panel 1", value=1),
            app_commands.Choice(name="Buy Panel 2", value=2),
        ],
        enabled=[
            app_commands.Choice(name="An — Credits-Button + Quick Buy", value=1),
            app_commands.Choice(name="Aus", value=0),
        ],
    )
    @app_commands.default_permissions(manage_guild=True)
    async def buypanelcredits(
        self,
        interaction: discord.Interaction,
        slot: app_commands.Choice[int],
        enabled: app_commands.Choice[int],
    ) -> None:
        assert interaction.guild is not None
        panel_slot = slot.value
        on = bool(enabled.value)
        await interaction.response.defer(ephemeral=True)

        await self.bot.db.set_buy_panel_credits(
            interaction.guild.id, panel_slot, on
        )
        await ensure_buy_panel_slot_view(self.bot, panel_slot)

        row = await self.bot.db.ensure_buy_panel_slot(
            interaction.guild.id, panel_slot
        )
        refresh_note = ""
        if row.get("channel_id") and row.get("message_id"):
            refresh_result = await refresh_slot_panel(
                self.bot, interaction.guild, panel_slot
            )
            refresh_note = f"\n{refresh_result}"
        else:
            refresh_note = (
                "\nKein Panel gepostet — danach `/buypanel` oder `/panelsetup`."
            )

        await interaction.followup.send(
            embed=success_embed(
                f"Buy Panel {panel_slot} — Credits",
                (
                    "**An** — Button **Credits** auf dem Panel, "
                    "**Quick Buy** in Produkt-Tickets.\n"
                    "1 Credit = **100k**."
                    if on
                    else "**Aus** — Credits-Button entfernt."
                )
                + refresh_note,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="buypanelstatus",
        description="Zeigt Konfiguration von Buy Panel 1 und 2",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def buypanelstatus(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        from utils.embeds import error_embed

        cats = await self.bot.db.list_categories(interaction.guild.id)
        lines: list[str] = []
        for slot in (1, 2):
            row = await self.bot.db.ensure_buy_panel_slot(
                interaction.guild.id, slot
            )
            pf = PanelFilter.from_slot_row(row)
            filtered = apply_panel_filter(cats, pf)
            names = ", ".join(c["name"] for c in filtered[:8]) or "—"
            if len(filtered) > 8:
                names += f" … (+{len(filtered) - 8})"
            msg_hint = ""
            if row.get("channel_id") and row.get("message_id"):
                ch = interaction.guild.get_channel(int(row["channel_id"]))
                if ch:
                    msg_hint = f"\n  Nachricht: {ch.mention} (`{row['message_id']}`)"
            lines.append(
                f"**Panel {slot}** — {panel_filter_summary(pf)}"
                f"{' · 🪙 Credits an' if int(row.get('credits_enabled') or 0) else ''}\n"
                f"  Kategorien ({len(filtered)}): {names}{msg_hint}"
            )
        await interaction.response.send_message(
            embed=success_embed("Buy Panel Status", "\n\n".join(lines)),
            ephemeral=True,
        )

    @app_commands.command(
        name="buypanelboth",
        description="Buy Panel 1 und 2 in diesen Channel posten oder aktualisieren",
    )
    @app_commands.describe(
        channel="Ziel-Channel (Standard: aktueller Channel)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def buypanelboth(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        await _ensure_catalog(self.bot, interaction.guild.id)
        target = await _resolve_target_channel(interaction, channel)
        if target is None:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed("Kein Channel", "Bitte einen Text-Channel wählen."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        posted: list[str] = []
        for slot in (1, 2):
            msg = await _post_slot_panel(
                self.bot, interaction.guild, target, slot
            )
            posted.append(f"Panel {slot} → {msg.jump_url}")
        await interaction.followup.send(
            embed=success_embed(
                "Beide Panels gepostet",
                "\n".join(posted)
                + "\n\n**Empfohlene Reihenfolge:**\n"
                "1. `/buypanelconfig` — Kategorien für Panel 1 und 2 einstellen\n"
                "2. `/buypanelrefresh` — Panels mit neuer Config aktualisieren\n"
                "3. `/buypanelstatus` — prüfen",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="panelsetup",
        description="Empfohlen: Buy Panel 1+2 posten, aktualisieren und Status zeigen",
    )
    @app_commands.describe(
        channel="Ziel-Channel (Standard: aktueller Channel)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def panelsetup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        """Alles-in-einem: Panels posten/aktualisieren + Kurzstatus."""
        assert interaction.guild is not None
        target = await _resolve_target_channel(interaction, channel)
        if target is None:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed("Kein Channel", "Bitte einen Text-Channel wählen."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        posted: list[str] = []
        for slot in (1, 2):
            msg = await _post_slot_panel(
                self.bot,
                interaction.guild,
                target,
                slot,
                force_repost=False,
            )
            posted.append(f"**Panel {slot}** → {msg.jump_url}")
        cats = await self.bot.db.list_categories(interaction.guild.id)
        status_lines: list[str] = []
        for slot in (1, 2):
            pf, _ = await get_panel_filter_for_slot(
                self.bot, interaction.guild.id, slot
            )
            filtered = apply_panel_filter(cats, pf)
            status_lines.append(
                f"Panel {slot}: {panel_filter_summary(pf)} ({len(filtered)} sichtbar)"
            )
        await interaction.followup.send(
            embed=success_embed(
                "Panel-Setup abgeschlossen",
                "\n".join(posted)
                + "\n\n"
                + "\n".join(status_lines)
                + "\n\nKategorien ändern: `/buypanelconfig` → Buy Panel 1 oder 2"
                + "\n\n_Alte kaputte „Buy Panel“-Nachrichten (nur „Weiter einkaufen“) bitte manuell löschen._",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="buypanelrefresh",
        description="Gespeicherte Buy Panels mit aktuellen Buttons und Filtern aktualisieren",
    )
    @app_commands.describe(
        slot="Nur Panel 1 oder 2 (Standard: beide)",
    )
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="Buy Panel 1", value=1),
            app_commands.Choice(name="Buy Panel 2", value=2),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def buypanelrefresh(
        self,
        interaction: discord.Interaction,
        slot: app_commands.Choice[int] | None = None,
    ) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True)
        slots = [slot.value] if slot is not None else [1, 2]
        results = [
            await _refresh_slot_panel(self.bot, interaction.guild, s) for s in slots
        ]
        await interaction.followup.send(
            embed=success_embed("Panels aktualisiert", "\n".join(results)),
            ephemeral=True,
        )

    @app_commands.command(
        name="buypanel",
        description="Buy Panel 1 oder 2 posten (nach /buypanelconfig)",
    )
    @app_commands.describe(
        slot="Welches Panel gepostet werden soll",
        channel="Ziel-Channel (Standard: aktueller Channel)",
        title="Optional: eigener Titel (überschreibt gespeicherten Titel)",
    )
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="Buy Panel 1", value=1),
            app_commands.Choice(name="Buy Panel 2", value=2),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def buypanel(
        self,
        interaction: discord.Interaction,
        slot: app_commands.Choice[int],
        channel: discord.TextChannel | None = None,
        title: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        await _ensure_catalog(self.bot, interaction.guild.id)

        panel_slot = slot.value
        _, stored_title = await get_panel_filter_for_slot(
            self.bot, interaction.guild.id, panel_slot
        )
        panel_title = title or stored_title
        await ensure_buy_panel_slot_view(self.bot, panel_slot)

        target = await _resolve_target_channel(interaction, channel)
        if target is None:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed(
                    "Kein Channel",
                    "Bitte einen Text-Channel angeben oder den Befehl dort ausführen.",
                ),
                ephemeral=True,
            )
            return

        label = panel_title or f"Buy Panel {panel_slot}"
        await interaction.response.send_message(
            embed=success_embed(
                "Buy-Panel gepostet", f"**{label}** → {target.mention}"
            ),
            ephemeral=True,
        )
        await _post_slot_panel(
            self.bot,
            interaction.guild,
            target,
            panel_slot,
            title=panel_title,
        )

    @app_commands.command(
        name="buypanels",
        description="Ein Buy-Panel pro Kategorie posten (Kategorien von Website)",
    )
    @app_commands.describe(
        channel="Optional: Ziel-Channel (Standard: aktueller Channel)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def buypanels(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None
        await _ensure_catalog(self.bot, interaction.guild.id)

        cats = await self.bot.db.list_categories(interaction.guild.id)
        if not cats:
            from utils.embeds import error_embed

            hint = (
                "Keine Kategorien gefunden. "
                "Lege welche an mit `/adminpanel` oder `/category add`."
            )
            if shop_api.enabled:
                hint += " Optional: `/syncshop` für Website-Sync."
            await interaction.response.send_message(
                embed=error_embed("Keine Kategorien", hint),
                ephemeral=True,
            )
            return

        target = await _resolve_target_channel(interaction, channel)
        if target is None:
            from utils.embeds import error_embed

            await interaction.response.send_message(
                embed=error_embed(
                    "Kein Channel",
                    "Bitte einen Text-Channel angeben oder den Befehl dort ausführen.",
                ),
                ephemeral=True,
            )
            return

        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        posted: list[str] = []

        for cat in cats:
            category_id = int(cat["id"])
            await ensure_buy_panel_view(self.bot, category_id)
            embed = build_buy_panel_embed(
                categories=cats,
                settings=settings,
                category=cat,
            )
            await target.send(
                embed=embed,
                view=BuyPanelView(self.bot, category_id=category_id),
            )
            posted.append(cat["name"])

        await interaction.response.send_message(
            embed=success_embed(
                "Buy-Panels gepostet",
                f"{len(posted)} Panel(s) in {target.mention}:\n"
                + "\n".join(f"• **{n}**" for n in posted),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="shoppanel", description="Shop-Panel in diesen Channel posten"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def shoppanel(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await _ensure_catalog(self.bot, interaction.guild.id)

        cats = await self.bot.db.list_categories(interaction.guild.id)
        embed = base_embed(
            "Shop",
            "Wähle **Kategorien anzeigen**, um Artikel in den Warenkorb zu legen.\n"
            "Danach **Weiter einkaufen**, **Warenkorb** oder **Kaufen**.\n\n"
            f"**{PAYMENT_NOTICE}**\n\n"
            "_Tipp: `/buypanels` postet je Kategorie ein Panel (Website-Sync)._",
        )
        if cats:
            embed.add_field(
                name="Kategorien",
                value="\n".join(
                    f"{c.get('emoji') or '•'} **{c['name']}**" for c in cats[:20]
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Hinweis",
                value="Noch keine Kategorien — `/adminpanel` oder `/category add`.",
                inline=False,
            )
        await interaction.response.send_message(
            embed=success_embed("Panel gepostet", "Shop-Panel wurde gesendet."),
            ephemeral=True,
        )
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            await channel.send(embed=embed, view=ShopPanelView(self.bot))

    @app_commands.command(name="cart", description="Deinen Warenkorb anzeigen")
    async def cart_cmd(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        view = CartView(self.bot, interaction.user.id, interaction.guild.id)
        await view.refresh(interaction)


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(ShopCog(bot))
