from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Union

import discord
from discord import app_commands

from utils.embeds import error_embed, format_price, success_embed
from utils.view_helpers import SafeView

if TYPE_CHECKING:
    from bot import ShopBot

RoleCallback = Callable[[discord.Interaction, Optional[discord.Role]], Awaitable[None]]
# discord.ui.ChannelSelect.values resolves to partial AppCommandChannel/-Thread
# objects (has .id/.mention/.name/.resolve()), NOT real GuildChannel instances.
PartialChannel = Union[app_commands.AppCommandChannel, app_commands.AppCommandThread]
ChannelCallback = Callable[[discord.Interaction, Optional[PartialChannel]], Awaitable[None]]


async def delete_later(message: discord.Message, delay: float = 12.0) -> None:
    """Löscht eine Nachricht nach kurzer Zeit (Fehler werden ignoriert)."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except (discord.HTTPException, discord.NotFound):
        pass


class RolePickView(SafeView):
    """Native Discord Role-Select mit Suche."""

    def __init__(
        self,
        *,
        on_pick: RoleCallback,
        allow_clear: bool = True,
        placeholder: str = "Rolle suchen / auswählen…",
        timeout: float = 180,
        stop_on_pick: bool = True,
    ) -> None:
        super().__init__(timeout=timeout)
        self.on_pick = on_pick
        self.allow_clear = allow_clear
        self.stop_on_pick = stop_on_pick

        select = discord.ui.RoleSelect(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            row=0,
        )
        select.callback = self._role_chosen  # type: ignore[method-assign]
        self.add_item(select)

        if allow_clear:
            clear_btn = discord.ui.Button(
                label="Keine Rolle", style=discord.ButtonStyle.secondary, row=1
            )
            clear_btn.callback = self._clear  # type: ignore[method-assign]
            self.add_item(clear_btn)

    async def _role_chosen(self, interaction: discord.Interaction) -> None:
        select: discord.ui.RoleSelect = next(
            c for c in self.children if isinstance(c, discord.ui.RoleSelect)
        )
        role = select.values[0] if select.values else None
        await self.on_pick(interaction, role)
        if self.stop_on_pick:
            self.stop()

    async def _clear(self, interaction: discord.Interaction) -> None:
        await self.on_pick(interaction, None)
        if self.stop_on_pick:
            self.stop()


class ChannelPickView(SafeView):
    """Native Discord Channel-Select mit Suche (z.B. für Settings-Panel)."""

    def __init__(
        self,
        *,
        on_pick: ChannelCallback,
        channel_types: list[discord.ChannelType],
        allow_clear: bool = True,
        placeholder: str = "Channel suchen / auswählen…",
        timeout: float = 180,
        stop_on_pick: bool = True,
    ) -> None:
        super().__init__(timeout=timeout)
        self.on_pick = on_pick
        self.allow_clear = allow_clear
        self.stop_on_pick = stop_on_pick

        select = discord.ui.ChannelSelect(
            placeholder=placeholder,
            channel_types=channel_types,
            min_values=1,
            max_values=1,
            row=0,
        )
        select.callback = self._channel_chosen  # type: ignore[method-assign]
        self.add_item(select)

        if allow_clear:
            clear_btn = discord.ui.Button(
                label="Keinen Channel", style=discord.ButtonStyle.secondary, row=1
            )
            clear_btn.callback = self._clear  # type: ignore[method-assign]
            self.add_item(clear_btn)

    async def _channel_chosen(self, interaction: discord.Interaction) -> None:
        select: discord.ui.ChannelSelect = next(
            c for c in self.children if isinstance(c, discord.ui.ChannelSelect)
        )
        channel = select.values[0] if select.values else None
        await self.on_pick(interaction, channel)
        if self.stop_on_pick:
            self.stop()

    async def _clear(self, interaction: discord.Interaction) -> None:
        await self.on_pick(interaction, None)
        if self.stop_on_pick:
            self.stop()


class CategorySearchView(SafeView):
    """Kategorie per Suche filtern, dann auswählen."""

    def __init__(
        self,
        bot: ShopBot,
        guild_id: int,
        categories: list[dict],
        *,
        on_pick: Callable[[discord.Interaction, dict], Awaitable[None]],
        placeholder: str = "Kategorie wählen…",
        timeout: float = 300,
        stop_on_pick: bool = True,
        keep_alive_content: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.bot = bot
        self.guild_id = guild_id
        self.all_categories = categories
        self.on_pick = on_pick
        self.placeholder = placeholder
        self.stop_on_pick = stop_on_pick
        self.keep_alive_content = keep_alive_content
        self.message: discord.Message | None = None
        self._rebuild(categories[:25])

    def _rebuild(self, cats: list[dict]) -> None:
        self.clear_items()
        if cats:
            # emoji may be invalid — sanitize
            safe_options: list[discord.SelectOption] = []
            for c in cats[:25]:
                kwargs = {
                    "label": c["name"][:100],
                    "value": str(c["id"]),
                    "description": ((c.get("description") or "")[:100] or None),
                }
                emoji = (c.get("emoji") or "").strip()
                try:
                    if emoji:
                        safe_options.append(discord.SelectOption(**kwargs, emoji=emoji))
                    else:
                        safe_options.append(discord.SelectOption(**kwargs))
                except (TypeError, ValueError):
                    safe_options.append(discord.SelectOption(
                        label=c["name"][:100],
                        value=str(c["id"]),
                    ))
            select = discord.ui.Select(
                placeholder=self.placeholder, options=safe_options, row=0
            )
            select.callback = self._selected  # type: ignore[method-assign]
            self.add_item(select)

        search_btn = discord.ui.Button(
            label="Suchen…", style=discord.ButtonStyle.primary, emoji="🔍", row=1
        )
        search_btn.callback = self._open_search  # type: ignore[method-assign]
        self.add_item(search_btn)

        show_all = discord.ui.Button(
            label="Alle anzeigen", style=discord.ButtonStyle.secondary, row=1
        )
        show_all.callback = self._show_all  # type: ignore[method-assign]
        self.add_item(show_all)

        if not self.stop_on_pick:
            close_btn = discord.ui.Button(
                label="Schließen", style=discord.ButtonStyle.danger, row=1
            )
            close_btn.callback = self._close  # type: ignore[method-assign]
            self.add_item(close_btn)

    async def refresh_message(
        self,
        *,
        content: str | None = None,
        cats: list[dict] | None = None,
    ) -> None:
        """Dropdown neu aufbauen, damit die Leiste erneut nutzbar ist."""
        source = cats if cats is not None else self.all_categories
        self._rebuild(source[:25])
        if self.message is None:
            return
        text = content
        if text is None:
            text = self.keep_alive_content or (
                f"Kategorien ({min(25, len(self.all_categories))} "
                f"von {len(self.all_categories)}) — suchen oder auswählen:"
            )
        try:
            await self.message.edit(content=text, view=self)
        except (discord.HTTPException, discord.NotFound):
            pass

    async def _selected(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = next(
            c for c in self.children if isinstance(c, discord.ui.Select)
        )
        cat_id = int(select.values[0])
        cat = next((c for c in self.all_categories if int(c["id"]) == cat_id), None)
        if cat is None:
            cat = await self.bot.db.get_category(cat_id)
        if not cat:
            await interaction.response.send_message(
                embed=error_embed("Kategorie nicht gefunden"), ephemeral=True
            )
            return
        self.message = interaction.message
        await self.on_pick(interaction, cat)
        if self.stop_on_pick:
            self.stop()
        else:
            # Select sofort zurücksetzen (auch wenn Modal abgebrochen wird)
            await self.refresh_message()

    async def _open_search(self, interaction: discord.Interaction) -> None:
        self.message = interaction.message
        await interaction.response.send_modal(CategorySearchModal(self))

    async def _show_all(self, interaction: discord.Interaction) -> None:
        self.message = interaction.message
        self._visible_categories = self.all_categories[:25]
        self._rebuild(self._visible_categories)
        await interaction.response.edit_message(
            content=f"Kategorien ({min(25, len(self.all_categories))} von {len(self.all_categories)}):",
            view=self,
        )

    async def _close(self, interaction: discord.Interaction) -> None:
        self.stop()
        try:
            await interaction.response.edit_message(
                content="Geschlossen.", view=None
            )
            msg = interaction.message
            if msg is not None:
                asyncio.create_task(delete_later(msg, 3.0))
        except discord.HTTPException:
            await interaction.response.defer()

    async def apply_filter(self, interaction: discord.Interaction, query: str) -> None:
        q = query.strip().lower()
        if not q:
            filtered = self.all_categories
        else:
            filtered = [
                c
                for c in self.all_categories
                if q in (c.get("name") or "").lower()
                or q in (c.get("description") or "").lower()
                or q == str(c.get("id"))
            ]
        if not filtered:
            await interaction.response.send_message(
                embed=error_embed("Keine Treffer", f"Nichts gefunden für `{query}`."),
                ephemeral=True,
            )
            return
        self.message = interaction.message
        self._rebuild(filtered[:25])
        await interaction.response.edit_message(
            content=f"Suchergebnis für **{query}** ({len(filtered)}):",
            view=self,
        )


class CategorySearchModal(discord.ui.Modal, title="Kategorie suchen"):
    query = discord.ui.TextInput(
        label="Suchbegriff",
        placeholder="Name oder Teil des Namens…",
        max_length=100,
        required=True,
    )

    def __init__(self, parent: CategorySearchView) -> None:
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent.apply_filter(interaction, str(self.query.value))


CategoryMultiConfirmCallback = Callable[
    [discord.Interaction, list[dict]], Awaitable[None]
]


class CategoryMultiSelectView(SafeView):
    """Mehrere Kategorien per Dropdown auswählen (Auswahl sammeln, dann speichern)."""

    def __init__(
        self,
        categories: list[dict],
        *,
        on_confirm: CategoryMultiConfirmCallback,
        header: str = "Kategorien wählen",
        placeholder: str = "Kategorien hinzufügen…",
        min_selected: int = 1,
        initial_selected_ids: set[int] | list[int] | None = None,
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.all_categories = categories
        self.on_confirm = on_confirm
        self.header = header
        self.placeholder = placeholder
        self.min_selected = min_selected
        self.selected_ids: set[int] = {
            int(i) for i in (initial_selected_ids or ())
        }
        self.message: discord.Message | None = None
        self._visible_categories = categories[:25]
        self._rebuild(self._visible_categories)

    def _selected_categories(self) -> list[dict]:
        by_id = {int(c["id"]): c for c in self.all_categories}
        return [by_id[i] for i in sorted(self.selected_ids) if i in by_id]

    def _status_text(self, *, list_hint: str | None = None) -> str:
        selected = self._selected_categories()
        if selected:
            names = ", ".join(
                f"**{c['name']}**" for c in selected[:12]
            )
            if len(selected) > 12:
                names += f" … (+{len(selected) - 12})"
            picked = f"**Ausgewählt ({len(selected)}):** {names}"
        else:
            picked = "_Noch keine Kategorien ausgewählt._"
        hint = list_hint or (
            f"Zeige {min(25, len(self.all_categories))} von "
            f"{len(self.all_categories)} — Suche bei vielen Kategorien."
        )
        return f"{self.header}\n\n{hint}\n\n{picked}"

    def _rebuild(self, cats: list[dict]) -> None:
        self.clear_items()
        visible = cats[:25]
        if visible:
            safe_options: list[discord.SelectOption] = []
            for c in visible:
                kwargs = {
                    "label": c["name"][:100],
                    "value": str(c["id"]),
                    "description": ((c.get("description") or "")[:100] or None),
                    "default": int(c["id"]) in self.selected_ids,
                }
                emoji = (c.get("emoji") or "").strip()
                try:
                    if emoji:
                        safe_options.append(discord.SelectOption(**kwargs, emoji=emoji))
                    else:
                        safe_options.append(discord.SelectOption(**kwargs))
                except (TypeError, ValueError):
                    safe_options.append(
                        discord.SelectOption(
                            label=c["name"][:100],
                            value=str(c["id"]),
                            default=int(c["id"]) in self.selected_ids,
                        )
                    )
            max_vals = min(25, len(safe_options))
            select = discord.ui.Select(
                placeholder=self.placeholder,
                options=safe_options,
                min_values=0,
                max_values=max_vals,
                row=0,
            )
            select.callback = self._add_selected  # type: ignore[method-assign]
            self.add_item(select)

        if self.selected_ids:
            remove_options: list[discord.SelectOption] = []
            by_id = {int(c["id"]): c for c in self.all_categories}
            for cat_id in sorted(self.selected_ids):
                cat = by_id.get(cat_id)
                if not cat:
                    continue
                remove_options.append(
                    discord.SelectOption(
                        label=f"✖ {cat['name']}"[:100],
                        value=str(cat_id),
                        description="Aus Auswahl entfernen",
                    )
                )
            if remove_options:
                remove_select = discord.ui.Select(
                    placeholder="Kategorie abwählen…",
                    options=remove_options[:25],
                    min_values=1,
                    max_values=1,
                    row=1,
                )
                remove_select.callback = self._remove_selected  # type: ignore[method-assign]
                self.add_item(remove_select)

        search_btn = discord.ui.Button(
            label="Suchen…", style=discord.ButtonStyle.secondary, emoji="🔍", row=2
        )
        search_btn.callback = self._open_search  # type: ignore[method-assign]
        self.add_item(search_btn)

        show_all = discord.ui.Button(
            label="Alle anzeigen", style=discord.ButtonStyle.secondary, row=2
        )
        show_all.callback = self._show_all  # type: ignore[method-assign]
        self.add_item(show_all)

        clear_btn = discord.ui.Button(
            label="Auswahl leeren",
            style=discord.ButtonStyle.secondary,
            row=3,
            disabled=not self.selected_ids,
        )
        clear_btn.callback = self._clear_selection  # type: ignore[method-assign]
        self.add_item(clear_btn)

        save_btn = discord.ui.Button(
            label="Speichern",
            style=discord.ButtonStyle.success,
            row=3,
            disabled=len(self.selected_ids) < self.min_selected,
        )
        save_btn.callback = self._save  # type: ignore[method-assign]
        self.add_item(save_btn)

        cancel_btn = discord.ui.Button(
            label="Abbrechen", style=discord.ButtonStyle.danger, row=3
        )
        cancel_btn.callback = self._cancel  # type: ignore[method-assign]
        self.add_item(cancel_btn)

    async def _add_selected(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = next(
            c
            for c in self.children
            if isinstance(c, discord.ui.Select) and c.placeholder == self.placeholder
        )
        visible_ids = {int(c["id"]) for c in self._visible_categories}
        chosen = {int(v) for v in select.values}
        self.selected_ids = (self.selected_ids - visible_ids) | chosen
        self.message = interaction.message
        self._rebuild(self._visible_categories)
        await interaction.response.edit_message(
            content=self._status_text(), view=self
        )

    async def _remove_selected(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = next(
            c
            for c in self.children
            if isinstance(c, discord.ui.Select)
            and c.placeholder == "Kategorie abwählen…"
        )
        self.selected_ids.discard(int(select.values[0]))
        self.message = interaction.message
        self._rebuild(self._visible_categories)
        await interaction.response.edit_message(
            content=self._status_text(), view=self
        )

    async def _save(self, interaction: discord.Interaction) -> None:
        if len(self.selected_ids) < self.min_selected:
            await interaction.response.send_message(
                embed=error_embed(
                    "Zu wenig ausgewählt",
                    f"Mindestens **{self.min_selected}** Kategorie(n) nötig.",
                ),
                ephemeral=True,
            )
            return
        selected = self._selected_categories()
        self.stop()
        await self.on_confirm(interaction, selected)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(content="Abgebrochen.", view=None)

    async def _clear_selection(self, interaction: discord.Interaction) -> None:
        self.selected_ids.clear()
        self.message = interaction.message
        self._visible_categories = self.all_categories[:25]
        self._rebuild(self._visible_categories)
        await interaction.response.edit_message(
            content=self._status_text(), view=self
        )

    async def _open_search(self, interaction: discord.Interaction) -> None:
        self.message = interaction.message
        await interaction.response.send_modal(CategoryMultiSearchModal(self))

    async def _show_all(self, interaction: discord.Interaction) -> None:
        self.message = interaction.message
        self._visible_categories = self.all_categories[:25]
        self._rebuild(self._visible_categories)
        await interaction.response.edit_message(
            content=self._status_text(
                list_hint=(
                    f"Alle Kategorien ({min(25, len(self.all_categories))} "
                    f"von {len(self.all_categories)}):"
                )
            ),
            view=self,
        )

    async def apply_filter(self, interaction: discord.Interaction, query: str) -> None:
        q = query.strip().lower()
        if not q:
            filtered = self.all_categories
        else:
            filtered = [
                c
                for c in self.all_categories
                if q in (c.get("name") or "").lower()
                or q in (c.get("description") or "").lower()
                or q == str(c.get("id"))
            ]
        if not filtered:
            await interaction.response.send_message(
                embed=error_embed("Keine Treffer", f"Nichts gefunden für `{query}`."),
                ephemeral=True,
            )
            return
        self.message = interaction.message
        self._visible_categories = filtered[:25]
        self._rebuild(self._visible_categories)
        await interaction.response.edit_message(
            content=self._status_text(
                list_hint=f"Suchergebnis für **{query}** ({len(filtered)}):"
            ),
            view=self,
        )


class CategoryMultiSearchModal(discord.ui.Modal, title="Kategorie suchen"):
    query = discord.ui.TextInput(
        label="Suchbegriff",
        placeholder="Name oder Teil des Namens…",
        max_length=100,
        required=True,
    )

    def __init__(self, parent: CategoryMultiSelectView) -> None:
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent.apply_filter(interaction, str(self.query.value))


class ItemSearchView(SafeView):
    """Item per Suche filtern (mit Vorschlägen), dann auswählen."""

    def __init__(
        self,
        bot: ShopBot,
        guild_id: int,
        items: list[dict],
        *,
        on_pick: Callable[[discord.Interaction, dict], Awaitable[None]],
        placeholder: str = "Item wählen…",
        timeout: float = 300,
        stop_on_pick: bool = True,
        keep_alive_content: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.bot = bot
        self.guild_id = guild_id
        self.all_items = items
        self.on_pick = on_pick
        self.placeholder = placeholder
        self.stop_on_pick = stop_on_pick
        self.keep_alive_content = keep_alive_content
        self.message: discord.Message | None = None
        self._rebuild(items[:25])

    def _rebuild(self, items: list[dict]) -> None:
        self.clear_items()
        if items:
            options = [
                discord.SelectOption(
                    label=i["name"][:100],
                    value=str(i["id"]),
                    description=format_price(float(i["price"]))[:100],
                )
                for i in items[:25]
            ]
            select = discord.ui.Select(
                placeholder=self.placeholder, options=options, row=0
            )
            select.callback = self._selected  # type: ignore[method-assign]
            self.add_item(select)

        search_btn = discord.ui.Button(
            label="Suchen…", style=discord.ButtonStyle.primary, emoji="🔍", row=1
        )
        search_btn.callback = self._open_search  # type: ignore[method-assign]
        self.add_item(search_btn)

        show_all = discord.ui.Button(
            label="Alle anzeigen", style=discord.ButtonStyle.secondary, row=1
        )
        show_all.callback = self._show_all  # type: ignore[method-assign]
        self.add_item(show_all)

        if not self.stop_on_pick:
            close_btn = discord.ui.Button(
                label="Schließen", style=discord.ButtonStyle.danger, row=1
            )
            close_btn.callback = self._close  # type: ignore[method-assign]
            self.add_item(close_btn)

    async def refresh_message(
        self,
        *,
        content: str | None = None,
        items: list[dict] | None = None,
    ) -> None:
        """Dropdown neu aufbauen, damit die Leiste erneut nutzbar ist."""
        source = items if items is not None else self.all_items
        self._rebuild(source[:25])
        if self.message is None:
            return
        text = content
        if text is None:
            text = self.keep_alive_content or (
                f"Items ({min(25, len(self.all_items))} "
                f"von {len(self.all_items)}) — suchen oder auswählen:"
            )
        try:
            await self.message.edit(content=text, view=self)
        except (discord.HTTPException, discord.NotFound):
            pass

    async def _selected(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = next(
            c for c in self.children if isinstance(c, discord.ui.Select)
        )
        item_id = int(select.values[0])
        item = next((i for i in self.all_items if int(i["id"]) == item_id), None)
        if item is None:
            item = await self.bot.db.get_item(item_id)
        if not item:
            await interaction.response.send_message(
                embed=error_embed("Item nicht gefunden"), ephemeral=True
            )
            return
        self.message = interaction.message
        await self.on_pick(interaction, item)
        if self.stop_on_pick:
            self.stop()
        else:
            # Select sofort zurücksetzen (auch wenn Modal abgebrochen wird)
            await self.refresh_message()

    async def _open_search(self, interaction: discord.Interaction) -> None:
        self.message = interaction.message
        await interaction.response.send_modal(ItemSearchModal(self))

    async def _show_all(self, interaction: discord.Interaction) -> None:
        self.message = interaction.message
        self._rebuild(self.all_items[:25])
        await interaction.response.edit_message(
            content=f"Items ({min(25, len(self.all_items))} von {len(self.all_items)}):",
            view=self,
        )

    async def _close(self, interaction: discord.Interaction) -> None:
        self.stop()
        try:
            await interaction.response.edit_message(
                content="Geschlossen.", view=None
            )
            msg = interaction.message
            if msg is not None:
                asyncio.create_task(delete_later(msg, 3.0))
        except discord.HTTPException:
            await interaction.response.defer()

    async def apply_filter(self, interaction: discord.Interaction, query: str) -> None:
        q = query.strip().lower()
        if not q:
            filtered = self.all_items
        else:
            filtered = [
                i
                for i in self.all_items
                if q in (i.get("name") or "").lower()
                or q in (i.get("description") or "").lower()
                or q == str(i.get("id"))
            ]
        if not filtered:
            await interaction.response.send_message(
                embed=error_embed("Keine Treffer", f"Nichts gefunden für `{query}`."),
                ephemeral=True,
            )
            return
        self.message = interaction.message
        self._rebuild(filtered[:25])
        await interaction.response.edit_message(
            content=f"Suchergebnis für **{query}** ({len(filtered)}):",
            view=self,
        )


class ItemSearchModal(discord.ui.Modal, title="Item suchen"):
    query = discord.ui.TextInput(
        label="Suchbegriff",
        placeholder="Name oder Teil des Namens…",
        max_length=100,
        required=True,
    )

    def __init__(self, parent: ItemSearchView) -> None:
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent.apply_filter(interaction, str(self.query.value))


class CategoryDeleteByNameModal(discord.ui.Modal, title="Kategorie löschen"):
    name = discord.ui.TextInput(
        label="Name der Kategorie",
        placeholder="Exakter oder teilweiser Name…",
        max_length=100,
        required=True,
    )

    def __init__(self, bot: ShopBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = str(self.name.value).strip()
        cats = await self.bot.db.list_categories(self.guild_id)
        exact = [c for c in cats if (c.get("name") or "").lower() == query.lower()]
        if len(exact) == 1:
            cat = exact[0]
        else:
            partial = [
                c for c in cats if query.lower() in (c.get("name") or "").lower()
            ]
            if len(partial) == 1:
                cat = partial[0]
            elif not partial:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Nicht gefunden",
                        f"Keine Kategorie mit Namen `{query}`.",
                    ),
                    ephemeral=True,
                )
                return
            else:
                names = ", ".join(f"**{c['name']}**" for c in partial[:8])
                await interaction.response.send_message(
                    embed=error_embed(
                        "Mehrdeutig",
                        f"Mehrere Treffer für `{query}`: {names}\n"
                        "Bitte genaueren Namen eingeben.",
                    ),
                    ephemeral=True,
                )
                return

        await self.bot.db.delete_category(int(cat["id"]))
        await interaction.response.send_message(
            embed=success_embed(
                "Gelöscht", f"Kategorie **{cat['name']}** entfernt."
            ),
            ephemeral=True,
        )
        try:
            msg = await interaction.original_response()
            asyncio.create_task(delete_later(msg, 8.0))
        except discord.HTTPException:
            pass


async def category_autocomplete(
    bot: ShopBot,
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[int]]:
    if not interaction.guild_id:
        return []
    cats = await bot.db.list_categories(interaction.guild_id)
    q = (current or "").lower().strip()
    if q:
        cats = [
            c
            for c in cats
            if q in (c.get("name") or "").lower()
            or q in (c.get("description") or "").lower()
            or q == str(c.get("id"))
        ]
    choices: list[app_commands.Choice[int]] = []
    for c in cats[:25]:
        label = f"{c.get('emoji') or ''} {c['name']}".strip()
        choices.append(
            app_commands.Choice(name=label[:100], value=int(c["id"]))
        )
    return choices


async def category_name_autocomplete(
    bot: ShopBot,
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete, das den Kategorienamen als Wert zurückgibt."""
    if not interaction.guild_id:
        return []
    cats = await bot.db.list_categories(interaction.guild_id)
    q = (current or "").lower().strip()
    if q:
        cats = [
            c
            for c in cats
            if q in (c.get("name") or "").lower()
            or q in (c.get("description") or "").lower()
        ]
    choices: list[app_commands.Choice[str]] = []
    for c in cats[:25]:
        label = f"{c.get('emoji') or ''} {c['name']}".strip()
        choices.append(
            app_commands.Choice(name=label[:100], value=str(c["name"])[:100])
        )
    return choices
