from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import base_embed, error_embed, format_price, success_embed
from utils.price import format_compact_number, parse_price
from views.selectors import delete_later

if TYPE_CHECKING:
    from bot import ShopBot


def _settings_summary(settings: dict) -> str:
    return (
        f"**Staff-Rolle:** `{settings.get('staff_role_id') or '—'}`\n"
        f"**Customer-Rolle:** `{settings.get('customer_role_id') or '—'}`\n"
        f"**Ticket-Kategorie:** `{settings.get('ticket_category_id') or '—'}`\n"
        f"**Vouch-Channel:** `{settings.get('vouch_channel_id') or '—'}`\n"
        f"**Ticket-Limit:** `{settings.get('max_open_tickets') or 1}`\n"
        f"**Zahlungsempfänger:** {settings.get('payee_a_label') or 'TxtEmpire'}\n"
        f"{settings.get('payee_a_details') or '_keine Details_'}"
    )


class AdminPanelView(discord.ui.View):
    def __init__(self, bot: ShopBot, guild_id: int) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Kategorie hinzufügen", style=discord.ButtonStyle.primary, row=0)
    async def add_cat(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(AddCategoryModal(self.bot, self.guild_id))

    @discord.ui.button(label="Kategorien", style=discord.ButtonStyle.secondary, row=0)
    async def list_cats(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from views.selectors import CategorySearchView

        cats = await self.bot.db.list_categories(self.guild_id)
        if not cats:
            await interaction.response.send_message(
                embed=error_embed("Keine Kategorien"), ephemeral=True
            )
            return

        async def on_pick(inter: discord.Interaction, cat: dict) -> None:
            role_txt = "—"
            if cat.get("role_id") and inter.guild:
                role = inter.guild.get_role(int(cat["role_id"]))
                role_txt = role.mention if role else f"`{cat['role_id']}`"
            view = CategoryActionsView(self.bot, self.guild_id, cat)
            await inter.response.send_message(
                embed=base_embed(
                    cat["name"],
                    f"ID `{cat['id']}`\n{cat.get('description') or '_'}\n"
                    f"Kauf-Rolle: {role_txt}",
                ),
                view=view,
                ephemeral=True,
            )

        view = CategorySearchView(
            self.bot,
            self.guild_id,
            cats,
            on_pick=on_pick,
            placeholder="Kategorie suchen / wählen…",
            stop_on_pick=False,
            keep_alive_content=(
                f"Kategorien ({len(cats)}) — suchen oder auswählen:"
            ),
        )
        await interaction.response.send_message(
            content=f"Kategorien ({len(cats)}) — suchen oder auswählen:",
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @discord.ui.button(label="Kategorie löschen", style=discord.ButtonStyle.danger, row=0)
    async def delete_cat(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from views.selectors import CategoryDeleteByNameModal

        cats = await self.bot.db.list_categories(self.guild_id)
        if not cats:
            await interaction.response.send_message(
                embed=error_embed("Keine Kategorien"), ephemeral=True
            )
            return
        await interaction.response.send_modal(
            CategoryDeleteByNameModal(self.bot, self.guild_id)
        )

    @discord.ui.button(label="Item hinzufügen", style=discord.ButtonStyle.primary, row=1)
    async def add_item(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from views.selectors import CategorySearchView

        cats = await self.bot.db.list_categories(self.guild_id)
        if not cats:
            await interaction.response.send_message(
                embed=error_embed(
                    "Keine Kategorie",
                    "Lege zuerst eine Kategorie an.",
                ),
                ephemeral=True,
            )
            return

        keep_txt = (
            "Item hinzufügen — Kategorie wählen (Leiste bleibt aktiv, "
            "danach einfach nächstes Item):"
        )

        async def on_pick(inter: discord.Interaction, cat: dict) -> None:
            await inter.response.send_modal(
                AddItemModal(
                    self.bot,
                    self.guild_id,
                    int(cat["id"]),
                    category_name=str(cat.get("name") or ""),
                    picker_view=view,
                )
            )

        view = CategorySearchView(
            self.bot,
            self.guild_id,
            cats,
            on_pick=on_pick,
            placeholder="Kategorie für neues Item…",
            stop_on_pick=False,
            keep_alive_content=keep_txt,
            timeout=600,
        )
        await interaction.response.send_message(
            content=keep_txt,
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @discord.ui.button(label="Items anzeigen", style=discord.ButtonStyle.secondary, row=1)
    async def list_items(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from views.selectors import ItemSearchView

        items = await self.bot.db.list_items(self.guild_id, active_only=False)
        if not items:
            await interaction.response.send_message(
                embed=error_embed("Keine Items"), ephemeral=True
            )
            return

        async def on_pick(inter: discord.Interaction, item: dict) -> None:
            fresh = await self.bot.db.get_item(int(item["id"])) or item
            # Wie bei „Item hinzufügen“: Auswahl → Formular sofort öffnen
            await inter.response.send_modal(EditItemModal(self.bot, fresh))

        keep_txt = f"Items ({len(items)}) — suchen oder auswählen:"
        view = ItemSearchView(
            self.bot,
            self.guild_id,
            items,
            on_pick=on_pick,
            placeholder="Item suchen / wählen…",
            stop_on_pick=False,
            keep_alive_content=keep_txt,
        )
        await interaction.response.send_message(
            content=keep_txt,
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @discord.ui.button(label="Ticket-Limit", style=discord.ButtonStyle.secondary, row=2)
    async def ticket_limit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            TicketLimitModal(self.bot, self.guild_id)
        )

    @discord.ui.button(label="Zahlungsempfänger", style=discord.ButtonStyle.secondary, row=2)
    async def payee(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(PayeeModal(self.bot, self.guild_id))

    @discord.ui.button(label="Einstellungen", style=discord.ButtonStyle.secondary, row=2)
    async def show_settings(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        settings = await self.bot.db.ensure_guild(self.guild_id)
        await interaction.response.send_message(
            embed=base_embed("Einstellungen", _settings_summary(settings)),
            ephemeral=True,
        )


class ManageCategoriesView(discord.ui.View):
    """Legacy wrapper — unused, kept for imports safety."""

    pass


class CategoryActionsView(discord.ui.View):
    def __init__(self, bot: ShopBot, guild_id: int, cat: dict) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.cat = cat

    @discord.ui.button(label="Bearbeiten", style=discord.ButtonStyle.primary)
    async def edit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            EditCategoryModal(self.bot, self.cat)
        )

    @discord.ui.button(label="Kauf-Rolle", style=discord.ButtonStyle.secondary)
    async def set_role(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from views.selectors import RolePickView

        async def on_pick(inter: discord.Interaction, role: discord.Role | None) -> None:
            await self.bot.db.update_category(
                int(self.cat["id"]), role_id=role.id if role else None
            )
            self.cat["role_id"] = role.id if role else None
            if role:
                await inter.response.send_message(
                    embed=success_embed(
                        "Rolle gesetzt",
                        f"**{self.cat['name']}** → {role.mention}",
                    ),
                    ephemeral=True,
                )
            else:
                await inter.response.send_message(
                    embed=success_embed("Rolle entfernt", f"**{self.cat['name']}**"),
                    ephemeral=True,
                )

        await interaction.response.send_message(
            content="Kauf-Rolle für die Kategorie wählen (Suche im Dropdown):",
            view=RolePickView(on_pick=on_pick),
            ephemeral=True,
        )

    @discord.ui.button(label="Löschen", style=discord.ButtonStyle.danger)
    async def delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        name = self.cat["name"]
        await self.bot.db.delete_category(int(self.cat["id"]))
        await interaction.response.edit_message(
            embed=success_embed("Gelöscht", f"**{name}** entfernt."),
            view=None,
        )
        try:
            msg = interaction.message
            if msg is not None:
                asyncio.create_task(delete_later(msg, 6.0))
        except discord.HTTPException:
            pass


class ManageItemsView(discord.ui.View):
    def __init__(self, bot: ShopBot, guild_id: int, items: list[dict]) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label=f"{i['name'][:70]}",
                value=str(i["id"]),
                description=format_price(float(i["price"]))[:100],
            )
            for i in items[:25]
        ]
        select = discord.ui.Select(placeholder="Item bearbeiten/löschen…", options=options)
        select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = self.children[0]  # type: ignore[assignment]
        item_id = int(select.values[0])
        item = await self.bot.db.get_item(item_id)
        if not item:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        await interaction.response.send_modal(EditItemModal(self.bot, item))


class ItemActionsView(discord.ui.View):
    def __init__(self, bot: ShopBot, item: dict) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.item = item

    @discord.ui.button(label="Preis/Pack bearbeiten", style=discord.ButtonStyle.primary)
    async def edit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(EditItemModal(self.bot, self.item))

    @discord.ui.button(label="Autorole", style=discord.ButtonStyle.secondary)
    async def set_role(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from views.selectors import RolePickView

        async def on_pick(inter: discord.Interaction, role: discord.Role | None) -> None:
            await self.bot.db.update_item(
                int(self.item["id"]), role_id=role.id if role else None
            )
            self.item["role_id"] = role.id if role else None
            if role:
                await inter.response.send_message(
                    embed=success_embed(
                        "Autorole gesetzt",
                        f"**{self.item['name']}** → {role.mention}",
                    ),
                    ephemeral=True,
                )
            else:
                await inter.response.send_message(
                    embed=success_embed("Autorole entfernt", f"**{self.item['name']}**"),
                    ephemeral=True,
                )

        await interaction.response.send_message(
            content="Artikel-Autorole wählen (Suche im Dropdown):",
            view=RolePickView(on_pick=on_pick),
            ephemeral=True,
        )

    @discord.ui.button(label="Pack-Link", style=discord.ButtonStyle.success)
    async def pack_file(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        from views.pack_upload import collect_pack_from_user

        await interaction.response.defer(ephemeral=True)
        await collect_pack_from_user(
            self.bot, interaction, int(self.item["id"])
        )

    @discord.ui.button(label="Löschen", style=discord.ButtonStyle.danger)
    async def delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        name = self.item["name"]
        await self.bot.db.delete_item(int(self.item["id"]))
        await interaction.response.edit_message(
            embed=success_embed("Gelöscht", f"**{name}** entfernt."),
            view=None,
        )
        try:
            msg = interaction.message
            if msg is not None:
                asyncio.create_task(delete_later(msg, 6.0))
        except discord.HTTPException:
            pass


class PickCategoryForItemView(discord.ui.View):
    pass


class AddCategoryModal(discord.ui.Modal, title="Kategorie hinzufügen"):
    name = discord.ui.TextInput(label="Name", max_length=100)
    description = discord.ui.TextInput(
        label="Beschreibung", required=False, max_length=500, style=discord.TextStyle.paragraph
    )
    emoji = discord.ui.TextInput(label="Emoji (optional)", required=False, max_length=32)

    def __init__(self, bot: ShopBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from views.selectors import RolePickView

        cid = await self.bot.db.add_category(
            self.guild_id,
            name=str(self.name.value),
            description=str(self.description.value or ""),
            emoji=str(self.emoji.value or ""),
        )
        from utils.panels import ensure_buy_panel_view

        await ensure_buy_panel_view(self.bot, cid)

        async def on_pick(inter: discord.Interaction, role: discord.Role | None) -> None:
            await self.bot.db.update_category(cid, role_id=role.id if role else None)
            if role:
                await inter.response.send_message(
                    embed=success_embed(
                        "Kauf-Rolle gesetzt",
                        f"**{self.name.value}** → {role.mention}",
                    ),
                    ephemeral=True,
                )
            else:
                await inter.response.send_message(
                    embed=success_embed("Ohne Kauf-Rolle", f"**{self.name.value}**"),
                    ephemeral=True,
                )
            try:
                msg = await inter.original_response()
                asyncio.create_task(delete_later(msg, 8.0))
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            embed=success_embed(
                "Kategorie erstellt",
                f"ID `{cid}` — **{self.name.value}**\n"
                "Optional: Kauf-Rolle wählen (mit Suche).",
            ),
            view=RolePickView(on_pick=on_pick),
            ephemeral=True,
        )
        try:
            msg = await interaction.original_response()
            asyncio.create_task(delete_later(msg, 60.0))
        except discord.HTTPException:
            pass


class EditCategoryModal(discord.ui.Modal, title="Kategorie bearbeiten"):
    name = discord.ui.TextInput(label="Name", max_length=100)
    description = discord.ui.TextInput(
        label="Beschreibung", required=False, max_length=500, style=discord.TextStyle.paragraph
    )
    emoji = discord.ui.TextInput(label="Emoji", required=False, max_length=32)

    def __init__(self, bot: ShopBot, cat: dict) -> None:
        super().__init__()
        self.bot = bot
        self.cat_id = int(cat["id"])
        self.name.default = cat.get("name") or ""
        self.description.default = cat.get("description") or ""
        self.emoji.default = cat.get("emoji") or ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.db.update_category(
            self.cat_id,
            name=str(self.name.value),
            description=str(self.description.value or ""),
            emoji=str(self.emoji.value or ""),
        )
        await interaction.response.send_message(
            embed=success_embed("Aktualisiert", f"**{self.name.value}** gespeichert."),
            ephemeral=True,
        )


class PostCreateItemView(discord.ui.View):
    """Pack + Autorole nach Item-Erstellung; Nachricht löscht sich danach."""

    def __init__(
        self,
        bot: ShopBot,
        item_id: int,
        item_name: str,
        user_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.item_id = item_id
        self.item_name = item_name
        self.user_id = user_id
        self.message: discord.Message | None = None

        pack_btn = discord.ui.Button(
            label="Pack per Drag & Drop senden",
            style=discord.ButtonStyle.success,
            emoji="📎",
            row=0,
        )
        pack_btn.callback = self._pack  # type: ignore[method-assign]
        self.add_item(pack_btn)

        role_select = discord.ui.RoleSelect(
            placeholder="Autorole setzen (optional)…",
            min_values=1,
            max_values=1,
            row=1,
        )
        role_select.callback = self._role  # type: ignore[method-assign]
        self.add_item(role_select)

        done = discord.ui.Button(
            label="Fertig", style=discord.ButtonStyle.secondary, row=2
        )
        done.callback = self._done  # type: ignore[method-assign]
        self.add_item(done)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Nur der Admin, der das Item angelegt hat.", ephemeral=True
            )
            return False
        return True

    async def _pack(self, interaction: discord.Interaction) -> None:
        from views.pack_upload import collect_pack_from_user

        await interaction.response.defer(ephemeral=True)
        await collect_pack_from_user(self.bot, interaction, self.item_id)

    async def _role(self, interaction: discord.Interaction) -> None:
        select: discord.ui.RoleSelect = next(
            c for c in self.children if isinstance(c, discord.ui.RoleSelect)
        )
        role = select.values[0] if select.values else None
        await self.bot.db.update_item(
            self.item_id, role_id=role.id if role else None
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Autorole gesetzt",
                f"**{self.item_name}** → {role.mention}" if role else "entfernt",
            ),
            ephemeral=True,
        )
        try:
            msg = await interaction.original_response()
            asyncio.create_task(delete_later(msg, 6.0))
        except discord.HTTPException:
            pass

    async def _done(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Erledigt.", embed=None, view=None
        )
        try:
            msg = interaction.message
            if msg is not None:
                asyncio.create_task(delete_later(msg, 2.0))
        except discord.HTTPException:
            pass

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.delete()
            except (discord.HTTPException, discord.NotFound):
                pass


class AddItemModal(discord.ui.Modal, title="Item hinzufügen"):
    name = discord.ui.TextInput(label="Name", max_length=100)
    price = discord.ui.TextInput(
        label="Preis",
        max_length=20,
        placeholder="z.B. 500k, 1.5m, 2b, 9,99",
    )
    description = discord.ui.TextInput(
        label="Beschreibung", required=False, max_length=500, style=discord.TextStyle.paragraph
    )
    pack_dm = discord.ui.TextInput(
        label="Pack-DM Text", required=False, max_length=500, style=discord.TextStyle.paragraph
    )

    def __init__(
        self,
        bot: ShopBot,
        guild_id: int,
        category_id: int,
        *,
        category_name: str = "",
        picker_view: object | None = None,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.category_id = category_id
        self.category_name = category_name
        self.picker_view = picker_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            price = parse_price(self.price.value)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed(
                    "Ungültiger Preis",
                    "Beispiele: `500`, `9,99`, `500k`, `1.5m`, `2b`",
                ),
                ephemeral=True,
            )
            return
        iid = await self.bot.db.add_item(
            self.guild_id,
            self.category_id,
            name=str(self.name.value),
            price=price,
            description=str(self.description.value or ""),
            pack_dm_text=str(self.pack_dm.value or ""),
            pack_link="",
        )

        cat_label = self.category_name or f"#{self.category_id}"
        # Kategorie-Leiste aktualisieren und weiter nutzbar lassen
        if self.picker_view is not None:
            refresh = getattr(self.picker_view, "refresh_message", None)
            if callable(refresh):
                await refresh(
                    content=(
                        f"✅ **{self.name.value}** ({format_price(price)}) "
                        f"in **{cat_label}** erstellt.\n"
                        "Nächstes Item — Kategorie wählen (oder Schließen):"
                    )
                )

        extras = PostCreateItemView(
            self.bot, iid, str(self.name.value), interaction.user.id
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Item erstellt",
                f"ID `{iid}` — **{self.name.value}** · {format_price(price)}\n"
                "Optional: Pack / Autorole — oder **Fertig**.\n"
                "Die Kategorie-Leiste bleibt aktiv für weitere Items.",
            ),
            view=extras,
            ephemeral=True,
        )
        try:
            extras.message = await interaction.original_response()
            asyncio.create_task(delete_later(extras.message, 90.0))
        except discord.HTTPException:
            pass


class EditItemModal(discord.ui.Modal, title="Item bearbeiten"):
    name = discord.ui.TextInput(label="Name", max_length=100)
    price = discord.ui.TextInput(
        label="Preis",
        max_length=20,
        placeholder="z.B. 500k, 1.5m, 2b, 9,99",
    )
    pack_dm = discord.ui.TextInput(
        label="Pack-DM Text", required=False, max_length=500, style=discord.TextStyle.paragraph
    )

    def __init__(self, bot: ShopBot, item: dict) -> None:
        super().__init__()
        self.bot = bot
        self.item_id = int(item["id"])
        self.name.default = item.get("name") or ""
        self.price.default = format_compact_number(float(item["price"]))
        self.pack_dm.default = item.get("pack_dm_text") or ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            price = parse_price(self.price.value)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed(
                    "Ungültiger Preis",
                    "Beispiele: `500`, `9,99`, `500k`, `1.5m`, `2b`",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_item(
            self.item_id,
            name=str(self.name.value),
            price=price,
            pack_dm_text=str(self.pack_dm.value or ""),
        )
        fresh = await self.bot.db.get_item(self.item_id)
        extras = ItemActionsView(self.bot, fresh or {"id": self.item_id, "name": self.name.value})
        await interaction.response.send_message(
            embed=success_embed(
                "Item aktualisiert",
                f"**{self.name.value}** · {format_price(price)}\n"
                "Weiter: Pack-Link, Autorole oder löschen.",
            ),
            view=extras,
            ephemeral=True,
        )


class TicketLimitModal(discord.ui.Modal, title="Ticket-Limit"):
    limit = discord.ui.TextInput(
        label="Max. offene Tickets pro User (1–10)", max_length=2, placeholder="1"
    )

    def __init__(self, bot: ShopBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.limit.value).strip()
        if not raw.isdigit() or not (1 <= int(raw) <= 10):
            await interaction.response.send_message(
                embed=error_embed("Wert muss 1–10 sein"), ephemeral=True
            )
            return
        await self.bot.db.update_guild_settings(
            self.guild_id, max_open_tickets=int(raw)
        )
        await interaction.response.send_message(
            embed=success_embed("Ticket-Limit", f"Gesetzt auf **{raw}**"),
            ephemeral=True,
        )


class PayeeModal(discord.ui.Modal, title="Zahlungsempfänger"):
    payee_name = discord.ui.TextInput(label="Name", max_length=100, default="TxtEmpire")
    payee_details = discord.ui.TextInput(
        label="Zahlungsdetails",
        max_length=400,
        style=discord.TextStyle.paragraph,
        required=False,
    )

    def __init__(self, bot: ShopBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = str(self.payee_name.value)
        details = str(self.payee_details.value)
        await self.bot.db.update_guild_settings(
            self.guild_id,
            payee_a_label=name[:100],
            payee_a_details=details[:500],
            payee_b_label="",
            payee_b_details="",
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Empfänger gespeichert",
                f"**{name}**\n{details or '_keine Details_'}",
            ),
            ephemeral=True,
        )


class AdminPanelCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="adminpanel",
        description="Admin-Panel für Shop, Tickets und Zahlung",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def adminpanel(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not await self._is_allowed(interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        cats = await self.bot.db.list_categories(interaction.guild.id)
        items = await self.bot.db.list_items(interaction.guild.id, active_only=False)
        embed = base_embed(
            "Admin Panel",
            f"**Kategorien:** {len(cats)} · **Items:** {len(items)}\n\n"
            + _settings_summary(settings),
        )
        view = AdminPanelView(self.bot, interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _is_allowed(self, interaction: discord.Interaction) -> bool:
        assert interaction.guild is not None
        member = interaction.user
        if isinstance(member, discord.Member) and member.guild_permissions.manage_guild:
            return True
        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        staff_id = settings.get("staff_role_id")
        if staff_id and isinstance(member, discord.Member):
            return any(r.id == int(staff_id) for r in member.roles)
        return False


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(AdminPanelCog(bot))
