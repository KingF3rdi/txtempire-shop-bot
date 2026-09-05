from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import error_embed, format_price, success_embed
from utils.price import parse_price

if TYPE_CHECKING:
    from bot import ShopBot


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

    category = app_commands.Group(name="category", description="Kategorien verwalten")

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

    item = app_commands.Group(name="item", description="Items verwalten")

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
            f"`{i['id']}` **{i['name']}** — {format_price(float(i['price']))} "
            f"(Cat `{i['category_id']}`)"
            + (" · inaktiv" if not i["active"] else "")
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

    new = app_commands.Group(
        name="new",
        description="Neues Item + Buy-Panel (optional Rabattcode)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

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

        # Buy-Panel für die Kategorie des neuen Items
        from utils.panels import build_buy_panel_embed, ensure_buy_panel_view
        from views.shop_views import BuyPanelView

        settings = await self.bot.db.ensure_guild(interaction.guild.id)
        cats = await self.bot.db.list_categories(interaction.guild.id)
        await ensure_buy_panel_view(self.bot, category)
        panel_embed = build_buy_panel_embed(
            categories=cats,
            settings=settings,
            category=cat,
            title=f"Neu: {name[:80]}",
        )
        panel_embed.insert_field_at(
            0,
            name="Neues Item",
            value=(
                f"**{name[:100]}** — {format_price(price_val)}\n"
                + (
                    f"_{(description[:120])}…_"
                    if len(description) > 120
                    else (f"_{description}_" if description.strip() else "")
                )
            ).strip(),
            inline=False,
        )
        panel_msg = await target.send(
            embed=panel_embed,
            view=BuyPanelView(self.bot, category_id=category),
        )

        code_note = ""
        if want_code and dtype is not None and dval is not None:
            from utils.discount_codes import format_code_discount

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
            code_note = (
                f"\n\n**Rabattcode** `{code_raw.upper()}` — "
                f"{format_code_discount(dtype, dval)}\n"
                f"Limit: **5** Uses · max. **1**/User · ID `{code_id}`"
            )

        role_note = f"\nAutorole: {role.mention}" if role else ""
        await interaction.followup.send(
            embed=success_embed(
                "Neues Item live",
                f"ID `{iid}` — **{name}** · {format_price(price_val)} in **{cat['name']}**"
                f"{pack_note}{role_note}\n\n"
                f"**Buy-Panel** → {panel_msg.jump_url} ({target.mention})"
                f"{code_note}",
            ),
            ephemeral=True,
        )

    @new_item.autocomplete("category")
    async def new_item_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._cat_ac(interaction, current)


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(SetupCog(bot))
