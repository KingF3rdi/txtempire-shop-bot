from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import error_embed, format_price, success_embed
from utils.price import parse_price

if TYPE_CHECKING:
    from bot import ShopBot


def _can_manage_guild(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    return bool(
        user.guild_permissions.manage_guild or user.guild_permissions.administrator
    )


async def _deny_manage_guild(interaction: discord.Interaction) -> None:
    embed = error_embed(
        "Keine Berechtigung",
        "Nur Admins / Mitglieder mit **Server verwalten** dürfen das.",
    )
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ManageGuildGroup(app_commands.Group):
    """Slash-Gruppe: Discord-Default + harte Runtime-Prüfung."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _can_manage_guild(interaction):
            return True
        await _deny_manage_guild(interaction)
        return False


class SetupCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    async def _cat_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        from views.selectors import category_autocomplete

        return await category_autocomplete(self.bot, interaction, current)

    @app_commands.command(name="setup", description="Shop-Grundkonfiguration")
    @app_commands.describe(
        staff_role="Staff-Rolle für Tickets",
        customer_role="Customer-Rolle nach Kauf",
        ticket_category="Discord-Kategorie für Order-Tickets",
        vouch_channel="Channel für /vouch Posts",
        max_tickets="Max. offene Tickets pro User",
    )
    @app_commands.default_permissions(administrator=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        staff_role: discord.Role,
        customer_role: discord.Role,
        ticket_category: discord.CategoryChannel,
        vouch_channel: discord.TextChannel,
        max_tickets: app_commands.Range[int, 1, 10] = 1,
    ) -> None:
        assert interaction.guild is not None
        if not (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                embed=error_embed(
                    "Keine Berechtigung",
                    "Nur **Administratoren** dürfen `/setup` nutzen.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild_settings(
            interaction.guild.id,
            staff_role_id=staff_role.id,
            customer_role_id=customer_role.id,
            ticket_category_id=ticket_category.id,
            vouch_channel_id=vouch_channel.id,
            max_open_tickets=int(max_tickets),
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Setup gespeichert",
                f"**Staff:** {staff_role.mention}\n"
                f"**Customer:** {customer_role.mention}\n"
                f"**Tickets:** {ticket_category.mention}\n"
                f"**Vouch:** {vouch_channel.mention}\n"
                f"**Ticket-Limit:** {max_tickets}",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="payee", description="Zahlungsempfänger setzen (gesamter Betrag)")
    @app_commands.describe(
        name="Name des Empfängers",
        details="Zahlungsdetails (IBAN, PayPal, …)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def payee(
        self,
        interaction: discord.Interaction,
        name: str,
        details: str,
    ) -> None:
        assert interaction.guild is not None
        if not _can_manage_guild(interaction):
            await _deny_manage_guild(interaction)
            return
        await self.bot.db.update_guild_settings(
            interaction.guild.id,
            payee_a_label=name[:100],
            payee_a_details=details[:500],
            payee_b_label="",
            payee_b_details="",
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Empfänger gespeichert",
                f"**{name}:** {details}\n\nDas gesamte Geld geht an TxtEmpire.",
            ),
            ephemeral=True,
        )

    category = ManageGuildGroup(
        name="category",
        description="Kategorien verwalten",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @category.command(name="add", description="Kategorie hinzufügen")
    @app_commands.describe(
        name="Name",
        description="Beschreibung",
        role="Kauf-Rolle (Discord-Vorschläge / Suche)",
        emoji="Optionales Emoji",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def category_add(
        self,
        interaction: discord.Interaction,
        name: str,
        description: str = "",
        role: Optional[discord.Role] = None,
        emoji: str = "",
    ) -> None:
        assert interaction.guild is not None
        cid = await self.bot.db.add_category(
            interaction.guild.id,
            name=name[:100],
            description=description[:500],
            role_id=role.id if role else None,
            emoji=emoji[:32],
        )
        from utils.panels import ensure_buy_panel_view

        await ensure_buy_panel_view(self.bot, cid)
        await interaction.response.send_message(
            embed=success_embed(
                "Kategorie erstellt",
                f"ID `{cid}` — **{name}**"
                + (f" · Rolle {role.mention}" if role else ""),
            ),
            ephemeral=True,
        )

    @category.command(name="list", description="Kategorien auflisten")
    @app_commands.default_permissions(manage_guild=True)
    async def category_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        cats = await self.bot.db.list_categories(interaction.guild.id)
        if not cats:
            await interaction.response.send_message(
                embed=error_embed("Keine Kategorien"), ephemeral=True
            )
            return
        lines = []
        for c in cats:
            role_txt = ""
            if c.get("role_id"):
                role = interaction.guild.get_role(int(c["role_id"]))
                role_txt = f" · {role.mention}" if role else f" · Rolle `{c['role_id']}`"
            lines.append(
                f"`{c['id']}` {c.get('emoji') or ''} **{c['name']}**{role_txt}"
            )
        await interaction.response.send_message(
            embed=success_embed("Kategorien", "\n".join(lines)), ephemeral=True
        )

    @category.command(name="delete", description="Kategorie per Name löschen")
    @app_commands.describe(name="Name der Kategorie (tippen zum Suchen)")
    @app_commands.default_permissions(manage_guild=True)
    async def category_delete(
        self, interaction: discord.Interaction, name: str
    ) -> None:
        assert interaction.guild is not None
        cats = await self.bot.db.list_categories(interaction.guild.id)
        query = name.strip()
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
                        f"Mehrere Treffer: {names}\nBitte genaueren Namen angeben.",
                    ),
                    ephemeral=True,
                )
                return

        await self.bot.db.delete_category(int(cat["id"]))
        await interaction.response.send_message(
            embed=success_embed("Gelöscht", f"Kategorie **{cat['name']}** entfernt."),
            ephemeral=True,
        )

    @category_delete.autocomplete("name")
    async def category_delete_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        from views.selectors import category_name_autocomplete

        return await category_name_autocomplete(self.bot, interaction, current)

    @category.command(name="setrole", description="Kauf-Rolle einer Kategorie setzen")
    @app_commands.describe(
        category="Kategorie (tippen zum Suchen)",
        role="Kauf-Rolle (Discord-Vorschläge / Suche)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def category_setrole(
        self,
        interaction: discord.Interaction,
        category: int,
        role: Optional[discord.Role] = None,
    ) -> None:
        cat = await self.bot.db.get_category(category)
        if not cat or cat["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        await self.bot.db.update_category(category, role_id=role.id if role else None)
        if role:
            await interaction.response.send_message(
                embed=success_embed(
                    "Kauf-Rolle gesetzt", f"**{cat['name']}** → {role.mention}"
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=success_embed("Kauf-Rolle entfernt", f"**{cat['name']}**"),
                ephemeral=True,
            )

    @category_setrole.autocomplete("category")
    async def category_setrole_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._cat_ac(interaction, current)

    item = ManageGuildGroup(
        name="item",
        description="Items verwalten",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @item.command(name="add", description="Item zu einer Kategorie hinzufügen")
    @app_commands.describe(
        category="Kategorie (tippen zum Suchen)",
        name="Item-Name",
        price="z.B. 500k, 1.5m, 2b, 9,99",
        description="Beschreibung",
        pack_file="Pack per Drag & Drop (setzt Pack-Link)",
        pack_dm="Pack-Text per DM nach Bestätigung",
        pack_link="Pack-Link (optional, sonst per Datei-Upload)",
        role="Autorole (Discord-Vorschläge / Suche)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def item_add(
        self,
        interaction: discord.Interaction,
        category: int,
        name: str,
        price: str,
        description: str = "",
        pack_file: Optional[discord.Attachment] = None,
        pack_dm: str = "",
        pack_link: str = "",
        role: Optional[discord.Role] = None,
    ) -> None:
        assert interaction.guild is not None
        try:
            price_val = parse_price(price)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed(
                    "Ungültiger Preis",
                    "Beispiele: `500`, `9,99`, `500k`, `1.5m`, `2b`",
                ),
                ephemeral=True,
            )
            return
        cat = await self.bot.db.get_category(category)
        if not cat or cat["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Kategorie nicht gefunden"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        iid = await self.bot.db.add_item(
            interaction.guild.id,
            category,
            name=name[:100],
            price=price_val,
            description=description[:1000],
            pack_dm_text=pack_dm[:1500],
            pack_link=pack_link[:500],
            role_id=role.id if role else None,
        )
        pack_note = ""
        if pack_file is not None:
            from views.pack_upload import apply_pack_attachment

            try:
                _rel, url = await apply_pack_attachment(
                    self.bot, iid, pack_file, channel=interaction.channel
                )
                pack_note = f"\nPack-Link: {url}"
            except ValueError as e:
                pack_note = f"\nPack-Upload fehlgeschlagen: {e}"
        role_note = f"\nAutorole: {role.mention}" if role else ""
        await interaction.followup.send(
            embed=success_embed(
                "Item erstellt",
                f"ID `{iid}` — **{name}** · {format_price(price_val)} in **{cat['name']}**"
                f"{pack_note}{role_note}",
            ),
            ephemeral=True,
        )

    @item_add.autocomplete("category")
    async def item_add_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._cat_ac(interaction, current)

    @item.command(
        name="set",
        description="Item bearbeiten — öffnet Formular (Name, Preis, Pack-DM)",
    )
    @app_commands.describe(item="Item (tippen zum Suchen)")
    @app_commands.default_permissions(manage_guild=True)
    async def item_set(
        self,
        interaction: discord.Interaction,
        item: int,
    ) -> None:
        row = await self.bot.db.get_item(item)
        if not row or row["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(
                embed=error_embed("Item nicht gefunden"), ephemeral=True
            )
            return
        from cogs.admin_panel import EditItemModal

        await interaction.response.send_modal(EditItemModal(self.bot, row))

    @item_set.autocomplete("item")
    async def item_set_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self.item_setpack_ac(interaction, current)

    @item.command(
        name="setpack",
        description="Pack-Datei anhängen (Drag & Drop) → Pack-Link",
    )
    @app_commands.describe(
        item="Item (tippen zum Suchen)",
        pack_file="Pack-Datei hier per Drag & Drop anhängen",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def item_setpack(
        self,
        interaction: discord.Interaction,
        item: int,
        pack_file: discord.Attachment,
    ) -> None:
        row = await self.bot.db.get_item(item)
        if not row or row["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(
                embed=error_embed("Item nicht gefunden"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        from views.pack_upload import apply_pack_attachment

        try:
            _rel, url = await apply_pack_attachment(
                self.bot, item, pack_file, channel=interaction.channel
            )
        except ValueError as e:
            await interaction.followup.send(
                embed=error_embed("Upload fehlgeschlagen", str(e)), ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=success_embed(
                "Pack-Link gesetzt",
                f"**{pack_file.filename}** → Item `{item}` (**{row['name']}**)\n"
                f"Link: {url}",
            ),
            ephemeral=True,
        )

    @item_setpack.autocomplete("item")
    async def item_setpack_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if not interaction.guild_id:
            return []
        items = await self.bot.db.list_items(
            interaction.guild_id, active_only=False
        )
        q = (current or "").lower().strip()
        if q:
            items = [
                i
                for i in items
                if q in (i.get("name") or "").lower() or q == str(i.get("id"))
            ]
        return [
            app_commands.Choice(
                name=f"{i['name'][:80]} (#{i['id']})", value=int(i["id"])
            )
            for i in items[:25]
        ]

    @item.command(name="setrole", description="Autorole eines Items setzen (nach Kauf)")
    @app_commands.describe(
        item="Item (tippen zum Suchen)",
        role="Autorole die nach erfolgreichem Kauf vergeben wird",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def item_setrole(
        self,
        interaction: discord.Interaction,
        item: int,
        role: Optional[discord.Role] = None,
    ) -> None:
        row = await self.bot.db.get_item(item)
        if not row or row["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        await self.bot.db.update_item(item, role_id=role.id if role else None)
        if role:
            await interaction.response.send_message(
                embed=success_embed(
                    "Item-Autorole gesetzt",
                    f"**{row['name']}** → {role.mention}\n"
                    "Wird automatisch vergeben, wenn Staff den Kauf bestätigt.",
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=success_embed("Autorole entfernt", f"**{row['name']}**"),
                ephemeral=True,
            )

    @item_setrole.autocomplete("item")
    async def item_setrole_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self.item_setpack_ac(interaction, current)

    @item.command(
        name="new",
        description="Bestehendes Item als NEU markieren + Buy-Panel (optional Launch-Code)",
    )
    @app_commands.describe(
        item="Item (tippen zum Suchen)",
        channel="Channel fürs Buy-Panel (Standard: hier)",
        code="Optional: neuen limitierten Rabattcode anlegen",
        discount_type="Rabattart — nur wenn code gesetzt",
        discount_value="z.B. 10 (%) oder 50k — nur wenn code gesetzt",
        max_uses="Max. Gesamtnutzungen für den Code (Standard: 5)",
        clear="Nur Markierung entfernen (kein Panel)",
    )
    @app_commands.choices(
        discount_type=[
            app_commands.Choice(name="Prozent (%)", value="percent"),
            app_commands.Choice(name="Betrag", value="amount"),
        ],
    )
    async def item_new(
        self,
        interaction: discord.Interaction,
        item: int,
        channel: Optional[discord.TextChannel] = None,
        code: Optional[str] = None,
        discount_type: Optional[app_commands.Choice[str]] = None,
        discount_value: Optional[str] = None,
        max_uses: app_commands.Range[int, 1, 1000] = 5,
        clear: bool = False,
    ) -> None:
        assert interaction.guild is not None
        row = await self.bot.db.get_item(item)
        if not row or int(row["guild_id"]) != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Item nicht gefunden"), ephemeral=True
            )
            return

        if clear:
            await self.bot.db.update_item(item, is_new=0, marked_new_at=None)
            await interaction.response.send_message(
                embed=success_embed(
                    "Neu-Markierung entfernt",
                    f"**{row['name']}** (ID `{item}`) ist nicht mehr als neu markiert.",
                ),
                ephemeral=True,
            )
            return

        if not int(row.get("active") or 0):
            await interaction.response.send_message(
                embed=error_embed(
                    "Item deaktiviert",
                    "Bitte Item zuerst aktivieren, bevor du es als neu postest.",
                ),
                ephemeral=True,
            )
            return

        code_raw = (code or "").strip()
        want_code = bool(code_raw)
        if want_code and (discount_type is None or not (discount_value or "").strip()):
            await interaction.response.send_message(
                embed=error_embed(
                    "Rabatt unvollständig",
                    "Mit `code` bitte auch **discount_type** und **discount_value** setzen.",
                ),
                ephemeral=True,
            )
            return

        dval: float | None = None
        dtype: str | None = None
        if want_code:
            from cogs.discount_codes import _parse_discount_value

            dtype = discount_type.value  # type: ignore[union-attr]
            try:
                dval = _parse_discount_value(dtype, discount_value or "")
            except ValueError as e:
                await interaction.response.send_message(
                    embed=error_embed("Ungültiger Rabattwert", str(e)),
                    ephemeral=True,
                )
                return
            existing = await self.bot.db.get_discount_code(
                interaction.guild.id, code_raw
            )
            if existing:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Code existiert schon",
                        f"`{code_raw.upper()}` ist bereits angelegt. "
                        "Anderen Code wählen oder ohne `code` nur Panel posten.",
                    ),
                    ephemeral=True,
                )
                return

        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Kein Channel",
                    "Bitte `channel` setzen oder den Befehl in einem Text-Channel ausführen.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        from datetime import datetime, timezone

        await self.bot.db.update_item(
            item,
            is_new=1,
            marked_new_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        row = await self.bot.db.get_item(item) or row

        code_note = ""
        discount_code: str | None = None
        discount_label: str | None = None
        if want_code and dtype is not None and dval is not None:
            code_note, discount_code, discount_label = await self._maybe_create_launch_code(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                code_raw=code_raw,
                dtype=dtype,
                dval=dval,
                label=f"Launch · {row['name']}"[:100],
                max_uses=int(max_uses),
            )

        try:
            msg = await self._post_item_buy_panel(
                guild_id=interaction.guild.id,
                target=target,
                item_id=item,
                discount_code=discount_code,
                discount_label=discount_label,
            )
        except ValueError as e:
            await interaction.followup.send(
                embed=error_embed("Panel fehlgeschlagen", str(e)), ephemeral=True
            )
            return

        await interaction.followup.send(
            embed=success_embed(
                "Als NEU markiert + Panel",
                f"**{row['name']}** (ID `{item}`) → {msg.jump_url}"
                f"{code_note}",
            ),
            ephemeral=True,
        )

    @item_new.autocomplete("item")
    async def item_new_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self.item_setpack_ac(interaction, current)

    @item.command(name="list", description="Items einer Kategorie auflisten")
    @app_commands.describe(category="Kategorie (tippen zum Suchen, leer = alle)")
    @app_commands.default_permissions(manage_guild=True)
    async def item_list(
        self,
        interaction: discord.Interaction,
        category: Optional[int] = None,
    ) -> None:
        assert interaction.guild is not None
        items = await self.bot.db.list_items(
            interaction.guild.id, category_id=category, active_only=False
        )
        if not items:
            await interaction.response.send_message(
                embed=error_embed("Keine Items"), ephemeral=True
            )
            return
        lines = [
            (
                f"{'🆕 ' if int(i.get('is_new') or 0) else ''}"
                f"`{i['id']}` **{i['name']}** — {format_price(float(i['price']))} "
                f"(Cat `{i['category_id']}`)"
                + (" · inaktiv" if not i["active"] else "")
            )
            for i in items[:40]
        ]
        await interaction.response.send_message(
            embed=success_embed("Items", "\n".join(lines)), ephemeral=True
        )

    @item_list.autocomplete("category")
    async def item_list_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._cat_ac(interaction, current)

    @item.command(name="delete", description="Item löschen")
    @app_commands.describe(item_id="Item-ID")
    @app_commands.default_permissions(manage_guild=True)
    async def item_delete(
        self, interaction: discord.Interaction, item_id: int
    ) -> None:
        item = await self.bot.db.get_item(item_id)
        if not item or item["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        await self.bot.db.delete_item(item_id)
        await interaction.response.send_message(
            embed=success_embed("Gelöscht", f"Item **{item['name']}** entfernt."),
            ephemeral=True,
        )

    new = ManageGuildGroup(
        name="new",
        description="Item/Pack anlegen + Buy-Panel",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _resolve_panel_channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel
        if isinstance(interaction.channel, discord.TextChannel):
            return interaction.channel
        return None

    async def _post_item_buy_panel(
        self,
        *,
        guild_id: int,
        target: discord.TextChannel,
        item_id: int,
        discount_code: str | None = None,
        discount_label: str | None = None,
    ) -> discord.Message:
        from views.item_buy_views import (
            ItemBuyView,
            build_item_buy_embed,
            ensure_item_buy_view,
        )

        row = await self.bot.db.get_item(item_id)
        if not row:
            raise ValueError("Item nicht gefunden")
        cat = await self.bot.db.get_category(int(row["category_id"]))
        ensure_item_buy_view(self.bot, item_id)
        embed = build_item_buy_embed(
            item=row,
            category_name=(cat["name"] if cat else None),
            discount_code=discount_code,
            discount_label=discount_label,
        )
        return await target.send(
            embed=embed,
            view=ItemBuyView(self.bot, item_id),
        )

    async def _maybe_create_launch_code(
        self,
        *,
        guild_id: int,
        user_id: int,
        code_raw: str,
        dtype: str,
        dval: float,
        label: str,
        max_uses: int = 5,
        max_per_user: int = 1,
    ) -> tuple[str, str, str]:
        """Returns (code_note, discount_code, discount_label)."""
        from utils.discount_codes import format_code_discount

        uses = max(1, int(max_uses))
        code_id = await self.bot.db.create_discount_code(
            guild_id,
            code_raw,
            discount_type=dtype,
            discount_value=dval,
            max_uses=uses,
            max_per_user=max(1, int(max_per_user)),
            label=label[:100],
            created_by=user_id,
            kind="rabatt",
        )
        discount_code = code_raw.strip().upper()
        discount_label = format_code_discount(dtype, dval)
        note = (
            f"\n\n**Rabattcode** `{discount_code}` — {discount_label}\n"
            f"Limit: **{uses}** Uses · max. **{max_per_user}**/User · ID `{code_id}`"
        )
        return note, discount_code, discount_label

    @new.command(
        name="item",
        description="Item anlegen, Buy-Panel posten, optional Rabattcode (5 Uses)",
    )
    @app_commands.describe(
        category="Kategorie (tippen zum Suchen)",
        name="Item-Name",
        price="z.B. 500k, 1.5m, 2b, 9,99",
        description="Beschreibung",
        channel="Channel fürs Buy-Panel (Standard: hier)",
        code="Optional: Rabattcode anlegen (fest auf 5 Uses limitiert)",
        discount_type="Rabattart — nur wenn code gesetzt",
        discount_value="z.B. 10 (%) oder 50k — nur wenn code gesetzt",
        pack_file="Pack per Drag & Drop",
        pack_dm="Pack-Text per DM nach Bestätigung",
        pack_link="Pack-Link (optional)",
        role="Autorole nach Kauf",
    )
    @app_commands.choices(
        discount_type=[
            app_commands.Choice(name="Prozent (%)", value="percent"),
            app_commands.Choice(name="Betrag", value="amount"),
        ],
    )
    async def new_item(
        self,
        interaction: discord.Interaction,
        category: int,
        name: str,
        price: str,
        description: str = "",
        channel: Optional[discord.TextChannel] = None,
        code: Optional[str] = None,
        discount_type: Optional[app_commands.Choice[str]] = None,
        discount_value: Optional[str] = None,
        pack_file: Optional[discord.Attachment] = None,
        pack_dm: str = "",
        pack_link: str = "",
        role: Optional[discord.Role] = None,
    ) -> None:
        assert interaction.guild is not None

        try:
            price_val = parse_price(price)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed(
                    "Ungültiger Preis",
                    "Beispiele: `500`, `9,99`, `500k`, `1.5m`, `2b`",
                ),
                ephemeral=True,
            )
            return

        cat = await self.bot.db.get_category(category)
        if not cat or cat["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Kategorie nicht gefunden"), ephemeral=True
            )
            return

        code_raw = (code or "").strip()
        want_code = bool(code_raw)
        if want_code and (discount_type is None or not (discount_value or "").strip()):
            await interaction.response.send_message(
                embed=error_embed(
                    "Rabatt unvollständig",
                    "Mit `code` bitte auch **discount_type** und **discount_value** setzen.\n"
                    "Der Code wird fest auf **5 Uses** limitiert.",
                ),
                ephemeral=True,
            )
            return

        dval: float | None = None
        dtype: str | None = None
        if want_code:
            from cogs.discount_codes import _parse_discount_value

            dtype = discount_type.value  # type: ignore[union-attr]
            try:
                dval = _parse_discount_value(dtype, discount_value or "")
            except ValueError as e:
                await interaction.response.send_message(
                    embed=error_embed("Ungültiger Rabattwert", str(e)),
                    ephemeral=True,
                )
                return
            existing = await self.bot.db.get_discount_code(
                interaction.guild.id, code_raw
            )
            if existing:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Code existiert",
                        f"`{code_raw.upper()}` gibt es bereits — anderen Code wählen.",
                    ),
                    ephemeral=True,
                )
                return

        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Kein Channel",
                    "Bitte `channel` setzen oder den Befehl in einem Text-Channel ausführen.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        iid = await self.bot.db.add_item(
            interaction.guild.id,
            category,
            name=name[:100],
            price=price_val,
            description=description[:1000],
            pack_dm_text=pack_dm[:1500],
            pack_link=pack_link[:500],
            role_id=role.id if role else None,
        )
        from datetime import datetime, timezone

        await self.bot.db.update_item(
            iid,
            is_new=1,
            marked_new_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )

        pack_note = ""
        if pack_file is not None:
            from views.pack_upload import apply_pack_attachment

            try:
                _rel, url = await apply_pack_attachment(
                    self.bot, iid, pack_file, channel=interaction.channel
                )
                pack_note = f"\nPack-Link: {url}"
            except ValueError as e:
                pack_note = f"\nPack-Upload fehlgeschlagen: {e}"

        # Buy-Panel verknüpft mit diesem Item (Direkt-Kauf)
        from utils.discount_codes import format_code_discount
        from views.item_buy_views import (
            ItemBuyView,
            build_item_buy_embed,
            ensure_item_buy_view,
        )

        item_row = await self.bot.db.get_item(iid)
        assert item_row is not None

        code_note = ""
        discount_code: str | None = None
        discount_label: str | None = None
        if want_code and dtype is not None and dval is not None:
            code_id = await self.bot.db.create_discount_code(
                interaction.guild.id,
                code_raw,
                discount_type=dtype,
                discount_value=dval,
                max_uses=5,
                max_per_user=1,
                label=f"Launch {name[:40]}",
                created_by=interaction.user.id,
                kind="rabatt",
            )
            discount_code = code_raw.upper()
            discount_label = format_code_discount(dtype, dval)
            code_note = (
                f"\n\n**Rabattcode** `{discount_code}` — {discount_label}\n"
                f"Limit: **5** Uses · max. **1**/User · ID `{code_id}`"
            )

        ensure_item_buy_view(self.bot, iid)
        panel_embed = build_item_buy_embed(
            item=item_row,
            category_name=str(cat["name"]),
            discount_code=discount_code,
            discount_label=discount_label,
        )
        panel_msg = await target.send(
            embed=panel_embed,
            view=ItemBuyView(self.bot, iid),
        )

        role_note = f"\nAutorole: {role.mention}" if role else ""
        await interaction.followup.send(
            embed=success_embed(
                "Neues Item live",
                f"ID `{iid}` — **{name}** · {format_price(price_val)} in **{cat['name']}**"
                f"{pack_note}{role_note}\n\n"
                f"**Item-Buy-Panel** (verknüpft mit Item `{iid}`) → "
                f"{panel_msg.jump_url} ({target.mention})"
                f"{code_note}",
            ),
            ephemeral=True,
        )

    @new_item.autocomplete("category")
    async def new_item_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._cat_ac(interaction, current)

    @new.command(
        name="panel",
        description="Buy-Panel für ein bestehendes Item posten (Direkt-Kauf)",
    )
    @app_commands.describe(
        item="Item (tippen zum Suchen)",
        channel="Ziel-Channel (Standard: hier)",
        code="Optional: bestehenden Rabattcode auf dem Panel anzeigen",
    )
    async def new_panel(
        self,
        interaction: discord.Interaction,
        item: int,
        channel: Optional[discord.TextChannel] = None,
        code: Optional[str] = None,
    ) -> None:
        assert interaction.guild is not None
        row = await self.bot.db.get_item(item)
        if not row or int(row["guild_id"]) != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Item nicht gefunden"), ephemeral=True
            )
            return
        if not int(row.get("active") or 0):
            await interaction.response.send_message(
                embed=error_embed("Item ist deaktiviert"), ephemeral=True
            )
            return

        target = channel
        if target is None and isinstance(interaction.channel, discord.TextChannel):
            target = interaction.channel
        if target is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Kein Channel",
                    "Bitte `channel` setzen oder den Befehl in einem Text-Channel ausführen.",
                ),
                ephemeral=True,
            )
            return

        discount_code = None
        discount_label = None
        code_raw = (code or "").strip()
        if code_raw:
            from utils.discount_codes import format_code_discount

            crow = await self.bot.db.get_discount_code(
                interaction.guild.id, code_raw
            )
            if not crow:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Code nicht gefunden",
                        f"`{code_raw.upper()}` existiert nicht.",
                    ),
                    ephemeral=True,
                )
                return
            discount_code = str(crow["code"])
            discount_label = format_code_discount(
                str(crow["discount_type"]), float(crow["discount_value"])
            )

        cat = await self.bot.db.get_category(int(row["category_id"]))
        from views.item_buy_views import (
            ItemBuyView,
            build_item_buy_embed,
            ensure_item_buy_view,
        )

        await interaction.response.defer(ephemeral=True)
        ensure_item_buy_view(self.bot, int(row["id"]))
        embed = build_item_buy_embed(
            item=row,
            category_name=(cat["name"] if cat else None),
            discount_code=discount_code,
            discount_label=discount_label,
        )
        msg = await target.send(
            embed=embed,
            view=ItemBuyView(self.bot, int(row["id"])),
        )
        await interaction.followup.send(
            embed=success_embed(
                "Item-Panel gepostet",
                f"**{row['name']}** (ID `{row['id']}`) → {msg.jump_url}",
            ),
            ephemeral=True,
        )

    @new_panel.autocomplete("item")
    async def new_panel_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self.item_setpack_ac(interaction, current)

    @new.command(
        name="pack",
        description="Pack setzen: bestehendes Item ODER neues Item + Buy-Panel",
    )
    @app_commands.describe(
        pack_file="Pack-Datei (Drag & Drop)",
        item="Bestehendes Item (tippen) — wenn gesetzt, kein neues Item",
        category="Nur für NEUES Item: Kategorie",
        name="Nur für NEUES Item: Name",
        price="Nur für NEUES Item: z.B. 500k, 1.5m",
        description="Nur für NEUES Item: Beschreibung",
        pack_dm="Pack-Text per DM nach Kauf",
        pack_link="Optionaler Pack-Link (sonst Datei)",
        channel="Channel fürs Buy-Panel (Standard: hier)",
        code="Optional: neuen Rabattcode anlegen (5 Uses)",
        discount_type="Rabattart — nur mit code",
        discount_value="z.B. 10 oder 50k — nur mit code",
        role="Nur für NEUES Item: Autorole",
        post_panel="Buy-Panel posten (Standard: ja)",
    )
    @app_commands.choices(
        discount_type=[
            app_commands.Choice(name="Prozent (%)", value="percent"),
            app_commands.Choice(name="Betrag", value="amount"),
        ],
        post_panel=[
            app_commands.Choice(name="Ja — Panel posten", value=1),
            app_commands.Choice(name="Nein — nur Pack setzen", value=0),
        ],
    )
    async def new_pack(
        self,
        interaction: discord.Interaction,
        pack_file: discord.Attachment,
        item: Optional[int] = None,
        category: Optional[int] = None,
        name: Optional[str] = None,
        price: Optional[str] = None,
        description: str = "",
        pack_dm: str = "",
        pack_link: str = "",
        channel: Optional[discord.TextChannel] = None,
        code: Optional[str] = None,
        discount_type: Optional[app_commands.Choice[str]] = None,
        discount_value: Optional[str] = None,
        role: Optional[discord.Role] = None,
        post_panel: app_commands.Choice[int] | None = None,
    ) -> None:
        assert interaction.guild is not None
        do_panel = True if post_panel is None else bool(post_panel.value)

        # Modus: bestehend ODER neu
        if item is None:
            if category is None or not (name or "").strip() or not (price or "").strip():
                await interaction.response.send_message(
                    embed=error_embed(
                        "Angaben fehlen",
                        "**Bestehendes Item:** Parameter `item` setzen.\n"
                        "**Neues Item:** `category` + `name` + `price` setzen "
                        "(plus `pack_file`).",
                    ),
                    ephemeral=True,
                )
                return
            try:
                price_val = parse_price(price or "")
            except ValueError:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Ungültiger Preis",
                        "Beispiele: `500`, `9,99`, `500k`, `1.5m`, `2b`",
                    ),
                    ephemeral=True,
                )
                return
            cat = await self.bot.db.get_category(int(category))
            if not cat or int(cat["guild_id"]) != interaction.guild.id:
                await interaction.response.send_message(
                    embed=error_embed("Kategorie nicht gefunden"), ephemeral=True
                )
                return
        else:
            row = await self.bot.db.get_item(item)
            if not row or int(row["guild_id"]) != interaction.guild.id:
                await interaction.response.send_message(
                    embed=error_embed("Item nicht gefunden"), ephemeral=True
                )
                return
            if not int(row.get("active") or 0):
                await interaction.response.send_message(
                    embed=error_embed("Item ist deaktiviert"), ephemeral=True
                )
                return
            cat = await self.bot.db.get_category(int(row["category_id"]))
            price_val = float(row["price"])

        code_raw = (code or "").strip()
        want_code = bool(code_raw)
        dval: float | None = None
        dtype: str | None = None
        if want_code:
            if discount_type is None or not (discount_value or "").strip():
                await interaction.response.send_message(
                    embed=error_embed(
                        "Rabatt unvollständig",
                        "Mit `code` bitte auch **discount_type** und **discount_value**.",
                    ),
                    ephemeral=True,
                )
                return
            from cogs.discount_codes import _parse_discount_value

            dtype = discount_type.value
            try:
                dval = _parse_discount_value(dtype, discount_value or "")
            except ValueError as e:
                await interaction.response.send_message(
                    embed=error_embed("Ungültiger Rabattwert", str(e)),
                    ephemeral=True,
                )
                return
            if await self.bot.db.get_discount_code(interaction.guild.id, code_raw):
                await interaction.response.send_message(
                    embed=error_embed(
                        "Code existiert",
                        f"`{code_raw.upper()}` gibt es bereits.",
                    ),
                    ephemeral=True,
                )
                return

        target = await self._resolve_panel_channel(interaction, channel)
        if do_panel and target is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Kein Channel",
                    "Bitte `channel` setzen oder den Befehl in einem Text-Channel ausführen.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        created_new = False
        if item is None:
            iid = await self.bot.db.add_item(
                interaction.guild.id,
                int(category),  # type: ignore[arg-type]
                name=(name or "")[:100],
                price=price_val,
                description=description[:1000],
                pack_dm_text=pack_dm[:1500],
                pack_link=pack_link[:500],
                role_id=role.id if role else None,
            )
            from datetime import datetime, timezone

            await self.bot.db.update_item(
                iid,
                is_new=1,
                marked_new_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            )
            created_new = True
            display_name = (name or "")[:100]
        else:
            iid = int(item)
            display_name = str((await self.bot.db.get_item(iid) or {}).get("name") or iid)
            updates: dict = {}
            if pack_dm.strip():
                updates["pack_dm_text"] = pack_dm[:1500]
            if pack_link.strip():
                updates["pack_link"] = pack_link[:500]
            if updates:
                await self.bot.db.update_item(iid, **updates)

        from views.pack_upload import apply_pack_attachment

        pack_note = ""
        try:
            _rel, url = await apply_pack_attachment(
                self.bot, iid, pack_file, channel=interaction.channel
            )
            pack_note = f"\nPack: **{pack_file.filename}** → {url}"
        except ValueError as e:
            await interaction.followup.send(
                embed=error_embed("Pack-Upload fehlgeschlagen", str(e)),
                ephemeral=True,
            )
            return

        code_note = ""
        discount_code = None
        discount_label = None
        if want_code and dtype is not None and dval is not None:
            code_note, discount_code, discount_label = await self._maybe_create_launch_code(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                code_raw=code_raw,
                dtype=dtype,
                dval=dval,
                label=f"Pack {display_name[:40]}",
            )

        panel_note = ""
        if do_panel and target is not None:
            panel_msg = await self._post_item_buy_panel(
                guild_id=interaction.guild.id,
                target=target,
                item_id=iid,
                discount_code=discount_code,
                discount_label=discount_label,
            )
            panel_note = (
                f"\n\n**Item-Buy-Panel** (Item `{iid}`) → "
                f"{panel_msg.jump_url} ({target.mention})"
            )

        mode = "Neues Item" if created_new else "Bestehendes Item"
        cat_name = (cat or {}).get("name") or "—"
        await interaction.followup.send(
            embed=success_embed(
                f"Pack gesetzt — {mode}",
                f"ID `{iid}` — **{display_name}** · {format_price(price_val)} "
                f"in **{cat_name}**"
                f"{pack_note}{panel_note}{code_note}",
            ),
            ephemeral=True,
        )

    @new_pack.autocomplete("item")
    async def new_pack_item_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self.item_setpack_ac(interaction, current)

    @new_pack.autocomplete("category")
    async def new_pack_cat_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._cat_ac(interaction, current)


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(SetupCog(bot))
