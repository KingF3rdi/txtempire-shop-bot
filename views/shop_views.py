from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from utils.embeds import cart_embed, error_embed, format_price, success_embed
from utils.view_helpers import SafeView
from utils.panels import PanelFilter, apply_panel_filter, parse_buy_custom_id, parse_buy_panel_custom_id
from config import DEFAULT_PAYEE, PAYMENT_NOTICE

if TYPE_CHECKING:
    from bot import ShopBot


@dataclass
class BrowseContext:
    """Kontext für Kategorie-Browsing (Panel-Slot oder Legacy-Kategorie)."""

    panel_slot: int | None = None
    category_id: int | None = None
    panel_filter: PanelFilter | None = None

    async def resolve_filter(self, bot: ShopBot, guild_id: int) -> PanelFilter:
        if self.panel_filter is not None:
            return self.panel_filter
        if self.panel_slot is not None:
            try:
                from utils.panels import get_panel_filter_for_slot

                pf, _ = await get_panel_filter_for_slot(
                    bot, guild_id, self.panel_slot
                )
            except Exception as exc:
                print(
                    f"[BuyPanel] Slot {self.panel_slot} Filter laden fehlgeschlagen: {exc!r}"
                )
                pf = PanelFilter.all_categories()
            self.panel_filter = pf
            return pf
        if self.category_id is not None:
            return PanelFilter.single(self.category_id)
        return PanelFilter.all_categories()


async def _reply_ephemeral(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
) -> discord.Message:
    kwargs: dict = {"ephemeral": True}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if interaction.response.is_done():
        return await interaction.followup.send(**kwargs, wait=True)
    await interaction.response.send_message(**kwargs)
    return await interaction.original_response()


class ShopPanelView(discord.ui.View):
    """Persistentes Shop-Panel mit Kategorie-Auswahl."""

    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Kategorien anzeigen",
        style=discord.ButtonStyle.primary,
        custom_id="shop:browse",
        emoji="🛒",
    )
    async def browse(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _browse_categories(self.bot, interaction)

    @discord.ui.button(
        label="Warenkorb",
        style=discord.ButtonStyle.secondary,
        custom_id="shop:cart",
        emoji="🧺",
    )
    async def open_cart(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        assert interaction.guild is not None
        view = CartView(self.bot, interaction.user.id, interaction.guild.id)
        await view.refresh(interaction)


class BuyPanelView(discord.ui.View):
    """Persistentes Buy-Panel — Kategorie, Slot (1/2) oder alle Kategorien."""

    def __init__(
        self,
        bot: ShopBot,
        category_id: int | None = None,
        panel_slot: int | None = None,
        *,
        credits_enabled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.category_id = category_id
        self.panel_slot = panel_slot
        self.credits_enabled = credits_enabled
        if panel_slot is not None:
            suffix = f":slot:{panel_slot}"
        else:
            suffix = f":{category_id}" if category_id is not None else ":all"
        self.browse_ctx = BrowseContext(
            panel_slot=panel_slot, category_id=category_id
        )

        buy_btn = discord.ui.Button(
            label="Kaufen",
            style=discord.ButtonStyle.success,
            custom_id=f"buy:start{suffix}",
            emoji="💳",
        )
        buy_btn.callback = self._on_buy_click
        self.add_item(buy_btn)

        cart_btn = discord.ui.Button(
            label="Warenkorb",
            style=discord.ButtonStyle.primary,
            custom_id=f"buy:cart{suffix}",
            emoji="🧺",
        )
        cart_btn.callback = self._on_cart_click
        self.add_item(cart_btn)

        info_btn = discord.ui.Button(
            label="Info",
            style=discord.ButtonStyle.secondary,
            custom_id=f"buy:info{suffix}",
            emoji="ℹ️",
        )
        info_btn.callback = self._on_info_click
        self.add_item(info_btn)

        if panel_slot is not None and credits_enabled:
            credits_btn = discord.ui.Button(
                label="Buy Credits",
                style=discord.ButtonStyle.primary,
                custom_id=f"buy:credits{suffix}",
                emoji="🪙",
                row=1,
            )
            credits_btn.callback = self._on_credits_click
            self.add_item(credits_btn)

    def _sync_ctx_from_interaction(self, interaction: discord.Interaction) -> None:
        """Slot/Kategorie aus custom_id lesen — wichtig für parallele Panels 1/2."""
        custom_id = interaction.data.get("custom_id") if interaction.data else None
        slot, category_id = parse_buy_custom_id(custom_id)
        if slot is not None:
            self.panel_slot = slot
            self.category_id = None
            self.browse_ctx.panel_slot = slot
            self.browse_ctx.category_id = None
            self.browse_ctx.panel_filter = None
        elif category_id is not None:
            self.category_id = category_id
            self.panel_slot = None
            self.browse_ctx.category_id = category_id
            self.browse_ctx.panel_slot = None
            self.browse_ctx.panel_filter = None

    async def _on_buy_click(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        self._sync_ctx_from_interaction(interaction)
        custom_id = interaction.data.get("custom_id") if interaction.data else "?"
        print(
            f"[BuyPanel] Kaufen geklickt: {custom_id} "
            f"(slot={self.panel_slot}, cat={self.category_id})"
        )
        await self._buy(interaction)

    async def _on_cart_click(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        self._sync_ctx_from_interaction(interaction)
        await self._cart(interaction)

    async def _on_info_click(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        self._sync_ctx_from_interaction(interaction)
        await self._info(interaction)

    async def _on_credits_click(self, interaction: discord.Interaction) -> None:
        self._sync_ctx_from_interaction(interaction)
        await self._buy_credits(interaction)

    async def _buy_credits(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await _reply_ephemeral(
                interaction,
                embed=error_embed("Nur auf dem Server", "Bitte im Server-Channel."),
            )
            return
        slot = self.panel_slot
        if slot is None:
            await _reply_ephemeral(
                interaction,
                embed=error_embed(
                    "Nicht verfügbar", "Credits nur über Buy Panel 1/2."
                ),
            )
            return
        row = await self.bot.db.ensure_buy_panel_slot(interaction.guild.id, slot)
        if not int(row.get("credits_enabled") or 0):
            await _reply_ephemeral(
                interaction,
                embed=error_embed(
                    "Credits deaktiviert",
                    "Credits sind für dieses Panel nicht aktiviert. "
                    "Admin: `/buypanelconfig` mit Credits an.",
                ),
            )
            return
        from views.credits_views import BuyCreditsModal

        await interaction.response.send_modal(
            BuyCreditsModal(self.bot, panel_slot=slot)
        )

    async def _buy(self, interaction: discord.Interaction) -> None:
        try:
            ctx = BrowseContext(
                panel_slot=self.panel_slot,
                category_id=self.category_id,
            )
            await _browse_categories(self.bot, interaction, ctx=ctx)
        except Exception as exc:
            print(
                f"[BuyPanel] Kaufen fehlgeschlagen "
                f"(slot={self.panel_slot}, cat={self.category_id}): {exc!r}"
            )
            await _reply_ephemeral(
                interaction,
                embed=error_embed(
                    "Fehler",
                    "Kaufen konnte nicht gestartet werden. "
                    "Bitte Bot neu starten oder Admin kontaktieren.",
                ),
            )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        print(f"[BuyPanel] View-Fehler ({getattr(item, 'custom_id', item)}): {error!r}")
        try:
            await _reply_ephemeral(
                interaction,
                embed=error_embed("Fehler", "Diese Aktion ist fehlgeschlagen."),
            )
        except discord.HTTPException:
            pass

    async def _cart(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await _reply_ephemeral(
                interaction,
                embed=error_embed("Nur auf dem Server", "Bitte im Server-Channel."),
            )
            return
        ctx = BrowseContext(
            panel_slot=self.panel_slot,
            category_id=self.category_id,
        )
        view = CartView(
            self.bot, interaction.user.id, interaction.guild.id, ctx
        )
        await view.refresh(interaction)

    async def _info(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await _reply_ephemeral(
                interaction,
                embed=error_embed("Nur auf dem Server", "Bitte im Server-Channel."),
            )
            return
        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        name = settings.get("payee_a_label") or DEFAULT_PAYEE
        details = (settings.get("payee_a_details") or "").strip()
        pay_line = f"4. **Gesamten Betrag** an **{name}** überweisen"
        if details:
            pay_line += f"\n{details}"
        credits_info = ""
        if self.panel_slot is not None:
            row = await self.bot.db.ensure_buy_panel_slot(
                interaction.guild.id, self.panel_slot
            )
            if int(row.get("credits_enabled") or 0):
                from utils.credits import CREDIT_VALUE, format_credits

                balance = await self.bot.db.get_credits(
                    interaction.guild.id, interaction.user.id
                )
                credits_info = (
                    f"\n\n**Credits** (dein Guthaben: **{format_credits(balance)}**)\n"
                    f"• 1 Credit = **{int(CREDIT_VALUE / 1000)}k**\n"
                    "• **Buy Credits** — Credits kaufen\n"
                    "• Im Ticket: **Fast Buy** — sofort mit Credits bezahlen"
                )
        await interaction.followup.send(
            embed=success_embed(
                "So funktioniert der Kauf",
                "1. **Kaufen** → Kategorie & Item wählen\n"
                "2. Danach **Weiter einkaufen**, **Warenkorb** oder **Kaufen**\n"
                "3. Im Warenkorb oder direkt **Kaufen** → privates Ticket\n"
                f"{pay_line}\n"
                "5. Payment beweisen (Bild + IGN)\n"
                "6. Staff bestätigt → Pack + Rollen\n\n"
                f"**{PAYMENT_NOTICE}**"
                f"{credits_info}",
            ),
            ephemeral=True,
        )


async def handle_buy_panel_interaction(
    bot: "ShopBot", interaction: discord.Interaction
) -> bool:
    """Fallback-Handler wenn persistente View nicht registriert ist."""
    if interaction.type != discord.InteractionType.component:
        return False
    data = interaction.data or {}
    custom_id = data.get("custom_id") or ""
    if not custom_id.startswith("buy:"):
        return False
    action, slot, category_id = parse_buy_panel_custom_id(custom_id)
    if not action:
        return False
    view = BuyPanelView(
        bot, category_id=category_id, panel_slot=slot, credits_enabled=True
    )
    if action == "start":
        await view._on_buy_click(interaction)
    elif action == "cart":
        await view._on_cart_click(interaction)
    elif action == "info":
        await view._on_info_click(interaction)
    elif action == "credits":
        await view._on_credits_click(interaction)
    else:
        return False
    return True


async def _browse_categories(
    bot: ShopBot,
    interaction: discord.Interaction,
    category_id: int | None = None,
    ctx: BrowseContext | None = None,
) -> None:
    from views.selectors import CategorySearchView
    from integrations.catalog_sync import maybe_sync_shop_catalog

    if interaction.guild is None:
        await _reply_ephemeral(
            interaction,
            embed=error_embed("Nur auf dem Server", "Bitte im Server-Channel kaufen."),
        )
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    try:
        await maybe_sync_shop_catalog(bot, interaction.guild.id)
    except Exception as exc:
        print(f"[browse] Katalog-Sync fehlgeschlagen: {exc!r}")

    browse_ctx = ctx or BrowseContext(category_id=category_id)
    if category_id is not None and ctx is None:
        browse_ctx.category_id = category_id

    panel_filter = await browse_ctx.resolve_filter(bot, interaction.guild.id)
    cats = await bot.db.list_categories(interaction.guild.id)
    filtered = apply_panel_filter(cats, panel_filter)

    # Legacy / single-category: direkt zu Items
    if panel_filter.mode == "single" and panel_filter.category_ids:
        single_id = panel_filter.category_ids[0]
        cat = await bot.db.get_category(single_id)
        if not cat or int(cat["guild_id"]) != interaction.guild.id:
            await _reply_ephemeral(
                interaction,
                embed=error_embed("Kategorie nicht gefunden"),
            )
            return
        items = await bot.db.list_items(
            interaction.guild.id, category_id=single_id
        )
        if not items:
            await _reply_ephemeral(
                interaction,
                embed=error_embed("Leer", "Diese Kategorie hat keine Items."),
            )
            return
        view = ItemSelectView(bot, items, cat["name"], browse_ctx)
        msg = await _reply_ephemeral(
            interaction,
            content=f"**{cat['name']}** — Item wählen:",
            view=view,
        )
        view.message = msg
        return

    if not filtered:
        await _reply_ephemeral(
            interaction,
            embed=error_embed(
                "Shop leer",
                "Für dieses Panel sind keine Kategorien verfügbar. "
                "Admin: `/buypanelconfig` oder `/syncshop`.",
            ),
        )
        return

    # Genau eine Kategorie im Filter → direkt Items
    if len(filtered) == 1:
        cat = filtered[0]
        category_id_one = int(cat["id"])
        items = await bot.db.list_items(
            interaction.guild.id, category_id=category_id_one
        )
        if not items:
            await _reply_ephemeral(
                interaction,
                embed=error_embed("Leer", "Diese Kategorie hat keine Items."),
            )
            return
        view = ItemSelectView(bot, items, cat["name"], browse_ctx)
        msg = await _reply_ephemeral(
            interaction,
            content=f"**{cat['name']}** — Item wählen:",
            view=view,
        )
        view.message = msg
        return

    async def on_pick(inter: discord.Interaction, cat: dict) -> None:
        items = await bot.db.list_items(
            inter.guild.id, category_id=int(cat["id"])  # type: ignore[union-attr]
        )
        if not items:
            await inter.response.send_message(
                embed=error_embed("Leer", "Diese Kategorie hat keine Items."),
                ephemeral=True,
            )
            return
        view = ItemSelectView(bot, items, cat["name"], browse_ctx)
        await inter.response.send_message(
            f"**{cat['name']}** — Item wählen:",
            view=view,
            ephemeral=True,
        )
        view.message = await inter.original_response()

    view = CategorySearchView(
        bot,
        interaction.guild.id,
        filtered,
        on_pick=on_pick,
        placeholder="Kategorie suchen / wählen…",
    )
    await _reply_ephemeral(
        interaction,
        content="Wähle eine Kategorie (Suche möglich):",
        view=view,
    )


async def start_checkout(
    bot: ShopBot,
    interaction: discord.Interaction,
    *,
    browse_ctx: BrowseContext | None = None,
) -> None:
    """Erstellt das Kauf-Ticket aus dem aktuellen Warenkorb."""
    from cogs.tickets import create_order_ticket

    assert interaction.guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    panel_slot = browse_ctx.panel_slot if browse_ctx else None
    credits_enabled = False
    if panel_slot is not None:
        row = await bot.db.ensure_buy_panel_slot(interaction.guild.id, panel_slot)
        credits_enabled = bool(int(row.get("credits_enabled") or 0))

    try:
        channel = await create_order_ticket(
            bot,
            interaction,
            credits_enabled=credits_enabled,
            source_panel_slot=panel_slot,
        )
    except ValueError as e:
        await interaction.followup.send(
            embed=error_embed("Kauf fehlgeschlagen", str(e)[:1500]),
            ephemeral=True,
        )
        return
    except Exception as e:
        await interaction.followup.send(
            embed=error_embed(
                "Kauf fehlgeschlagen",
                f"Unerwarteter Fehler: `{type(e).__name__}: {e}`",
            ),
            ephemeral=True,
        )
        return

    extra = ""
    if credits_enabled:
        from utils.credits import format_credits

        balance = await bot.db.get_credits(interaction.guild.id, interaction.user.id)
        extra = (
            f"\n\n🪙 **Credits aktiv** — Guthaben: **{format_credits(balance)}**\n"
            "Im Ticket kannst du **Fast Buy** nutzen."
        )
    await interaction.followup.send(
        embed=success_embed(
            "Ticket erstellt",
            f"Dein Kauf-Ticket: {channel.mention}\n\n**{PAYMENT_NOTICE}**{extra}",
        ),
        ephemeral=True,
    )


class CategorySelectView(discord.ui.View):
    def __init__(self, bot: ShopBot, categories: list[dict]) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        options = []
        for cat in categories[:25]:
            label = cat["name"][:100]
            emoji = (cat.get("emoji") or "").strip() or None
            desc = (cat.get("description") or "")[:100] or None
            try:
                opt = discord.SelectOption(
                    label=label,
                    value=str(cat["id"]),
                    description=desc,
                    emoji=emoji,
                )
            except (TypeError, ValueError):
                opt = discord.SelectOption(
                    label=label, value=str(cat["id"]), description=desc
                )
            options.append(opt)
        select = discord.ui.Select(
            placeholder="Kategorie wählen…",
            options=options,
            custom_id="shop:cat_select",
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = self.children[0]  # type: ignore[assignment]
        cat_id = int(select.values[0])
        assert interaction.guild is not None
        items = await self.bot.db.list_items(interaction.guild.id, category_id=cat_id)
        cat = await self.bot.db.get_category(cat_id)
        if not items:
            await interaction.response.send_message(
                embed=error_embed("Leer", "Diese Kategorie hat keine Items."),
                ephemeral=True,
            )
            return
        view = ItemSelectView(self.bot, items, cat["name"] if cat else "Items")
        await interaction.response.send_message(
            f"**{cat['name'] if cat else 'Items'}** — Item wählen:",
            view=view,
            ephemeral=True,
        )


class ItemSelectView(SafeView):
    """Mehrfachauswahl von Items (Checkboxen) mit Bestätigen + Zurück-Button."""

    def __init__(
        self,
        bot: ShopBot,
        items: list[dict],
        category_name: str,
        browse_ctx: BrowseContext | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.items = items
        self.category_name = category_name
        self.browse_ctx = browse_ctx or BrowseContext()
        self.selected_ids: set[int] = set()
        self._build()

    def _build(self) -> None:
        self.clear_items()
        options = []
        for item in self.items[:25]:
            item_id = int(item["id"])
            options.append(
                discord.SelectOption(
                    label=f"{item['name'][:80]}",
                    value=str(item_id),
                    description=f"{format_price(float(item['price']))}"[:100],
                    default=item_id in self.selected_ids,
                )
            )
        select = discord.ui.Select(
            placeholder=f"Items aus {self.category_name} auswählen (mehrfach möglich)…",
            options=options,
            min_values=0,
            max_values=len(options) if options else 1,
            row=0,
        )
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

        confirm_btn = discord.ui.Button(
            label="Auswahl bestätigen",
            style=discord.ButtonStyle.success,
            emoji="✅",
            row=1,
        )
        confirm_btn.callback = self._confirm  # type: ignore[method-assign]
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(
            label="Weiter einkaufen",
            style=discord.ButtonStyle.secondary,
            emoji="🛍️",
            row=2,
        )
        back_btn.callback = self._back  # type: ignore[method-assign]
        self.add_item(back_btn)

        cart_btn = discord.ui.Button(
            label="Warenkorb",
            style=discord.ButtonStyle.primary,
            emoji="🧺",
            row=2,
        )
        cart_btn.callback = self._open_cart  # type: ignore[method-assign]
        self.add_item(cart_btn)

        buy_btn = discord.ui.Button(
            label="Kaufen",
            style=discord.ButtonStyle.success,
            emoji="💳",
            row=2,
        )
        buy_btn.callback = self._buy  # type: ignore[method-assign]
        self.add_item(buy_btn)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = [
            c for c in self.children if isinstance(c, discord.ui.Select)
        ][0]
        self.selected_ids = {int(v) for v in select.values}
        self._build()
        await interaction.response.edit_message(view=self)

    async def _add_selected(self, interaction: discord.Interaction) -> list[str]:
        added: list[str] = []
        if not self.selected_ids:
            return added
        assert interaction.guild is not None
        for item_id in self.selected_ids:
            item = next((i for i in self.items if int(i["id"]) == item_id), None)
            if item is None:
                continue
            await self.bot.db.cart_add(
                interaction.user.id, interaction.guild.id, item_id, 1
            )
            added.append(item["name"])
        self.selected_ids = set()
        self._build()
        return added

    async def _confirm(self, interaction: discord.Interaction) -> None:
        added = await self._add_selected(interaction)
        if not added:
            await interaction.response.send_message(
                embed=error_embed("Keine Auswahl", "Bitte zuerst Items ankreuzen."),
                ephemeral=True,
            )
            return

        view = PostAddToCartView(self.bot, self.browse_ctx)
        await interaction.response.send_message(
            embed=success_embed(
                "Zum Warenkorb hinzugefügt",
                "\n".join(f"• **{n}**" for n in added)
                + "\n\n**Weiter einkaufen**, **Warenkorb** oder **Kaufen**:"
                + f"\n\n*{PAYMENT_NOTICE}*",
            ),
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    async def _back(self, interaction: discord.Interaction) -> None:
        await self._add_selected(interaction)
        await _browse_categories(self.bot, interaction, ctx=self.browse_ctx)

    async def _open_cart(self, interaction: discord.Interaction) -> None:
        await self._add_selected(interaction)
        assert interaction.guild is not None
        view = CartView(
            self.bot, interaction.user.id, interaction.guild.id, self.browse_ctx
        )
        await view.refresh(interaction)

    async def _buy(self, interaction: discord.Interaction) -> None:
        await self._add_selected(interaction)
        await start_checkout(self.bot, interaction, browse_ctx=self.browse_ctx)


class PostAddToCartView(SafeView):
    """Buttons nach dem Hinzufügen: weiter einkaufen, Warenkorb oder kaufen."""

    def __init__(
        self, bot: ShopBot, browse_ctx: BrowseContext | None = None
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.browse_ctx = browse_ctx or BrowseContext()

    @discord.ui.button(
        label="Weiter einkaufen", style=discord.ButtonStyle.primary, emoji="🛍️"
    )
    async def continue_shopping(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _browse_categories(self.bot, interaction, ctx=self.browse_ctx)

    @discord.ui.button(
        label="Warenkorb", style=discord.ButtonStyle.secondary, emoji="🧺"
    )
    async def open_cart(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        assert interaction.guild is not None
        view = CartView(
            self.bot, interaction.user.id, interaction.guild.id, self.browse_ctx
        )
        await view.refresh(interaction)

    @discord.ui.button(
        label="Kaufen", style=discord.ButtonStyle.success, emoji="💳"
    )
    async def buy_now(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await start_checkout(self.bot, interaction, browse_ctx=self.browse_ctx)


class AddToCartView(SafeView):
    def __init__(self, bot: ShopBot, item: dict) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.item = item

    @discord.ui.button(label="In den Warenkorb", style=discord.ButtonStyle.success)
    async def add(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        assert interaction.guild is not None
        await self.bot.db.cart_add(
            interaction.user.id, interaction.guild.id, int(self.item["id"]), 1
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Hinzugefügt",
                f"**{self.item['name']}** ist im Warenkorb.\n"
                "**Weiter einkaufen**, **Warenkorb** oder **Kaufen**."
                f"\n\n*{PAYMENT_NOTICE}*",
            ),
            view=PostAddToCartView(self.bot),
            ephemeral=True,
        )


class CartView(SafeView):
    def __init__(
        self,
        bot: ShopBot,
        user_id: int,
        guild_id: int,
        browse_ctx: BrowseContext | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.guild_id = guild_id
        self.browse_ctx = browse_ctx or BrowseContext()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Das ist nicht dein Warenkorb.", ephemeral=True
            )
            return False
        return True

    async def refresh(self, interaction: discord.Interaction) -> None:
        items = await self.bot.db.cart_get(self.user_id, self.guild_id)
        total = await self.bot.db.cart_total(self.user_id, self.guild_id)
        self.clear_items()

        if items:
            options = [
                discord.SelectOption(
                    label=f"{r['name'][:70]} ×{r['qty']}",
                    value=str(r["item_id"]),
                    description=format_price(float(r["price"]) * int(r["qty"]))[:100],
                )
                for r in items[:25]
            ]
            select = discord.ui.Select(
                placeholder="Item zum Anpassen wählen…", options=options
            )
            select.callback = self._on_item_select  # type: ignore[method-assign]
            self.add_item(select)

            buy_btn = discord.ui.Button(
                label="Kaufen", style=discord.ButtonStyle.success, emoji="💳"
            )
            buy_btn.callback = self._buy  # type: ignore[method-assign]
            self.add_item(buy_btn)

            clear_btn = discord.ui.Button(
                label="Leeren", style=discord.ButtonStyle.danger
            )
            clear_btn.callback = self._clear  # type: ignore[method-assign]
            self.add_item(clear_btn)

        shop_btn = discord.ui.Button(
            label="Weiter einkaufen",
            style=discord.ButtonStyle.primary,
            emoji="🛍️",
        )
        shop_btn.callback = self._continue  # type: ignore[method-assign]
        self.add_item(shop_btn)

        embed = cart_embed(items, total)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=embed, view=self, ephemeral=True
            )
        try:
            self.message = await interaction.original_response()
        except discord.HTTPException:
            pass

    async def _on_item_select(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = [
            c for c in self.children if isinstance(c, discord.ui.Select)
        ][0]
        item_id = int(select.values[0])
        view = CartItemAdjustView(self.bot, self.user_id, self.guild_id, item_id, self)
        await interaction.response.send_message(
            "Menge anpassen:", view=view, ephemeral=True
        )
        view.message = await interaction.original_response()

    async def _clear(self, interaction: discord.Interaction) -> None:
        await self.bot.db.cart_clear(self.user_id, self.guild_id)
        await interaction.response.send_message(
            embed=success_embed("Warenkorb geleert"), ephemeral=True
        )

    async def _continue(self, interaction: discord.Interaction) -> None:
        await _browse_categories(self.bot, interaction, ctx=self.browse_ctx)

    async def _buy(self, interaction: discord.Interaction) -> None:
        await start_checkout(self.bot, interaction, browse_ctx=self.browse_ctx)


class CartItemAdjustView(SafeView):
    def __init__(
        self,
        bot: ShopBot,
        user_id: int,
        guild_id: int,
        item_id: int,
        parent: CartView,
    ) -> None:
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.guild_id = guild_id
        self.item_id = item_id
        self.parent = parent

    async def _current_qty(self) -> int:
        items = await self.bot.db.cart_get(self.user_id, self.guild_id)
        for r in items:
            if int(r["item_id"]) == self.item_id:
                return int(r["qty"])
        return 0

    @discord.ui.button(label="+1", style=discord.ButtonStyle.success)
    async def plus(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        qty = await self._current_qty()
        await self.bot.db.cart_set_qty(
            self.user_id, self.guild_id, self.item_id, qty + 1
        )
        await interaction.response.send_message(
            embed=success_embed("Menge erhöht", f"Neue Menge: {qty + 1}"),
            ephemeral=True,
        )

    @discord.ui.button(label="-1", style=discord.ButtonStyle.secondary)
    async def minus(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        qty = await self._current_qty()
        new_qty = max(0, qty - 1)
        await self.bot.db.cart_set_qty(
            self.user_id, self.guild_id, self.item_id, new_qty
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Aktualisiert",
                f"Neue Menge: {new_qty}" if new_qty else "Item entfernt.",
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Entfernen", style=discord.ButtonStyle.danger)
    async def remove(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.bot.db.cart_remove(self.user_id, self.guild_id, self.item_id)
        await interaction.response.send_message(
            embed=success_embed("Entfernt", "Item aus dem Warenkorb entfernt."),
            ephemeral=True,
        )
