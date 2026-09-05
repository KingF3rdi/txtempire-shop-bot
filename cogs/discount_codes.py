from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.discount_codes import format_code_discount
from utils.embeds import error_embed, format_price, success_embed
from utils.price import parse_price
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


def _parse_discount_value(dtype: str, value: str) -> float:
    if dtype == "percent":
        raw = value.strip().replace("%", "").replace(",", ".")
        dval = float(raw)
        if dval <= 0 or dval > 100:
            raise ValueError("Prozent muss zwischen 0 und 100 liegen.")
        return dval
    return parse_price(value)


def _format_code_line(r: dict) -> str:
    status = "✅" if int(r.get("active") or 0) else "⛔"
    uses = int(r.get("uses") or 0)
    mx = r.get("max_uses")
    lim = f"{uses}/{mx}" if mx is not None else f"{uses}/∞"
    label = f" — **{r['label']}**" if r.get("label") else ""
    kind = str(r.get("kind") or "rabatt")
    kind_icon = "🎬" if kind == "creator" else "🏷️"
    return (
        f"{status} {kind_icon} `{r['code']}`{label}\n"
        f" {format_code_discount(r['discount_type'], float(r['discount_value']))} "
        f"· {lim} · ≤{int(r.get('max_per_user') or 1)}/User"
    )


def _month_label(month_key: str) -> str:
    try:
        dt = datetime.strptime(month_key, "%Y-%m")
        months_de = (
            "Januar",
            "Februar",
            "März",
            "April",
            "Mai",
            "Juni",
            "Juli",
            "August",
            "September",
            "Oktober",
            "November",
            "Dezember",
        )
        return f"{months_de[dt.month - 1]} {dt.year}"
    except ValueError:
        return month_key


class DiscountCodesCog(commands.Cog):
    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    # ── /code … (allgemein) ─────────────────────────────────────────

    code = app_commands.Group(
        name="code",
        description="Rabatt- / Creator-Codes verwalten",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @code.command(name="add", description="Rabatt- oder Creator-Code anlegen")
    @app_commands.describe(
        code="Code (z.B. SUMMER10 oder CREATOR)",
        discount_type="Rabattart",
        value="Wert — z.B. 10 (für 10%) oder 50k (für Betrag)",
        kind="Rabatt oder Creator-Code",
        max_uses="Max. Gesamtnutzungen (leer = unbegrenzt)",
        max_per_user="Max. Nutzungen pro User (Standard: 1)",
        label="Optional: Creator-/Anzeigename",
    )
    @app_commands.choices(
        discount_type=[
            app_commands.Choice(name="Prozent (%)", value="percent"),
            app_commands.Choice(name="Betrag", value="amount"),
        ],
        kind=[
            app_commands.Choice(name="Rabatt-Code", value="rabatt"),
            app_commands.Choice(name="Creator-Code", value="creator"),
        ],
    )
    async def code_add(
        self,
        interaction: discord.Interaction,
        code: str,
        discount_type: app_commands.Choice[str],
        value: str,
        kind: app_commands.Choice[str] | None = None,
        max_uses: app_commands.Range[int, 1, 1_000_000] | None = None,
        max_per_user: app_commands.Range[int, 1, 100] = 1,
        label: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return

        dtype = discount_type.value
        kind_v = kind.value if kind else "rabatt"
        try:
            dval = _parse_discount_value(dtype, value)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Wert", str(e)),
                ephemeral=True,
            )
            return

        existing = await self.bot.db.get_discount_code(
            interaction.guild.id, code
        )
        if existing:
            await interaction.response.send_message(
                embed=error_embed(
                    "Code existiert",
                    f"`{code.strip().upper()}` gibt es bereits.\n"
                    "Nutze `/code set` oder `/cc set` zum Ändern.",
                ),
                ephemeral=True,
            )
            return

        try:
            code_id = await self.bot.db.create_discount_code(
                interaction.guild.id,
                code,
                discount_type=dtype,
                discount_value=dval,
                max_uses=int(max_uses) if max_uses is not None else None,
                max_per_user=int(max_per_user),
                label=label or "",
                created_by=interaction.user.id,
                kind=kind_v,
            )
        except Exception as e:
            await interaction.response.send_message(
                embed=error_embed("Fehler", str(e)[:500]),
                ephemeral=True,
            )
            return

        limit_txt = (
            f"**{max_uses}**× gesamt"
            if max_uses is not None
            else "**unbegrenzt** gesamt"
        )
        kind_txt = "Creator-Code" if kind_v == "creator" else "Rabatt-Code"
        await interaction.response.send_message(
            embed=success_embed(
                f"{kind_txt} erstellt",
                f"`{code.strip().upper()}` — {format_code_discount(dtype, dval)}\n"
                f"Limit: {limit_txt} · max. **{max_per_user}**/User"
                + (f"\nLabel: **{label}**" if label else "")
                + f"\nID: `{code_id}`\n\n"
                "Im Ticket: **Rabatt / Creator Code** → Preis wird übernommen.",
            ),
            ephemeral=True,
        )

    @code.command(
        name="set",
        description="Rabatt eines bestehenden Codes festlegen / ändern",
    )
    @app_commands.describe(
        code="Bestehender Code",
        discount_type="Neue Rabattart",
        value="Neuer Wert — z.B. 15 oder 100k",
        label="Optional neues Label",
        kind="Optional Typ ändern",
    )
    @app_commands.choices(
        discount_type=[
            app_commands.Choice(name="Prozent (%)", value="percent"),
            app_commands.Choice(name="Betrag", value="amount"),
        ],
        kind=[
            app_commands.Choice(name="Rabatt-Code", value="rabatt"),
            app_commands.Choice(name="Creator-Code", value="creator"),
        ],
    )
    async def code_set(
        self,
        interaction: discord.Interaction,
        code: str,
        discount_type: app_commands.Choice[str],
        value: str,
        label: str | None = None,
        kind: app_commands.Choice[str] | None = None,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        row = await self.bot.db.get_discount_code(interaction.guild.id, code)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden", f"Code `{code}` unbekannt."),
                ephemeral=True,
            )
            return
        try:
            dval = _parse_discount_value(discount_type.value, value)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Wert", str(e)), ephemeral=True
            )
            return
        fields: dict = {
            "discount_type": discount_type.value,
            "discount_value": dval,
        }
        if label is not None:
            fields["label"] = label.strip()[:100]
        if kind is not None:
            fields["kind"] = kind.value
        await self.bot.db.update_discount_code(int(row["id"]), **fields)
        await interaction.response.send_message(
            embed=success_embed(
                "Code aktualisiert",
                f"`{row['code']}` → "
                f"{format_code_discount(discount_type.value, dval)}\n"
                "Neue Tickets / erneutes Einlösen übernehmen den Rabatt.",
            ),
            ephemeral=True,
        )

    @code.command(name="list", description="Alle Codes auflisten")
    async def code_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        rows = await self.bot.db.list_discount_codes(interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                embed=success_embed("Codes", "Keine Codes angelegt."),
                ephemeral=True,
            )
            return
        lines = [_format_code_line(r) for r in rows[:40]]
        await interaction.response.send_message(
            embed=success_embed("Rabatt- / Creator-Codes", "\n".join(lines)[:3900]),
            ephemeral=True,
        )

    @code.command(name="limit", description="Nutzungslimit eines Codes setzen")
    @app_commands.describe(
        code="Code",
        max_uses="Max. Gesamtnutzungen (0 = unbegrenzt)",
        max_per_user="Max. pro User",
    )
    async def code_limit(
        self,
        interaction: discord.Interaction,
        code: str,
        max_uses: app_commands.Range[int, 0, 1_000_000] | None = None,
        max_per_user: app_commands.Range[int, 1, 100] | None = None,
    ) -> None:
        assert interaction.guild is not None
        row = await self.bot.db.get_discount_code(interaction.guild.id, code)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden", f"Code `{code}` unbekannt."),
                ephemeral=True,
            )
            return
        fields: dict = {}
        if max_uses is not None:
            fields["max_uses"] = None if int(max_uses) == 0 else int(max_uses)
        if max_per_user is not None:
            fields["max_per_user"] = int(max_per_user)
        if not fields:
            await interaction.response.send_message(
                embed=error_embed("Nichts geändert", "Mindestens ein Limit angeben."),
                ephemeral=True,
            )
            return
        await self.bot.db.update_discount_code(int(row["id"]), **fields)
        updated = await self.bot.db.get_discount_code_by_id(int(row["id"]))
        assert updated is not None
        mx = updated.get("max_uses")
        await interaction.response.send_message(
            embed=success_embed(
                "Limit aktualisiert",
                f"`{updated['code']}`: "
                f"{'unbegrenzt' if mx is None else f'{mx}×'} gesamt · "
                f"≤{int(updated.get('max_per_user') or 1)}/User "
                f"(bisher {int(updated.get('uses') or 0)} Nutzungen)",
            ),
            ephemeral=True,
        )

    @code.command(name="disable", description="Code deaktivieren")
    @app_commands.describe(code="Code")
    async def code_disable(
        self, interaction: discord.Interaction, code: str
    ) -> None:
        assert interaction.guild is not None
        row = await self.bot.db.get_discount_code(interaction.guild.id, code)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        await self.bot.db.update_discount_code(int(row["id"]), active=0)
        await interaction.response.send_message(
            embed=success_embed("Deaktiviert", f"`{row['code']}` ist aus."),
            ephemeral=True,
        )

    @code.command(name="enable", description="Code aktivieren")
    @app_commands.describe(code="Code")
    async def code_enable(
        self, interaction: discord.Interaction, code: str
    ) -> None:
        assert interaction.guild is not None
        row = await self.bot.db.get_discount_code(interaction.guild.id, code)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        await self.bot.db.update_discount_code(int(row["id"]), active=1)
        await interaction.response.send_message(
            embed=success_embed("Aktiviert", f"`{row['code']}` ist an."),
            ephemeral=True,
        )

    @code.command(name="delete", description="Code löschen")
    @app_commands.describe(code="Code")
    async def code_delete(
        self, interaction: discord.Interaction, code: str
    ) -> None:
        assert interaction.guild is not None
        row = await self.bot.db.get_discount_code(interaction.guild.id, code)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        await self.bot.db.db.execute(
            "DELETE FROM discount_codes WHERE id = ?", (int(row["id"]),)
        )
        await self.bot.db.db.commit()
        await interaction.response.send_message(
            embed=success_embed("Gelöscht", f"`{row['code']}` entfernt."),
            ephemeral=True,
        )

    # ── /cc … (Creator-Codes) ───────────────────────────────────────

    cc = app_commands.Group(
        name="cc",
        description="Creator-Codes",
    )

    @cc.command(
        name="info",
        description="Übersicht aller Creator-Codes",
    )
    async def cc_info(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        staff = await is_staff(self.bot, interaction)
        rows = await self.bot.db.list_discount_codes(
            interaction.guild.id, kind="creator"
        )
        # Fallback: Codes mit Label als Creator anzeigen, falls kind noch fehlt
        if not rows:
            all_rows = await self.bot.db.list_discount_codes(interaction.guild.id)
            rows = [
                r
                for r in all_rows
                if str(r.get("kind") or "") == "creator" or (r.get("label") or "").strip()
            ]
        if not staff:
            rows = [r for r in rows if int(r.get("active") or 0)]
        if not rows:
            await interaction.response.send_message(
                embed=success_embed(
                    "Creator-Codes",
                    "Keine Creator-Codes angelegt.\n"
                    "Staff: `/cc add` oder `/code add kind:Creator-Code`.",
                ),
                ephemeral=True,
            )
            return

        lines = [_format_code_line(r) for r in rows[:40]]
        embed = success_embed(
            "🎬 Creator-Code Übersicht",
            "\n".join(lines)[:3900],
        )
        embed.set_footer(
            text="Stats & Verdienst: /cc stats · Im Ticket: Rabatt / Creator Code"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @cc.command(
        name="stats",
        description="Creator-Verdienst diesen Monat (10% vom Bestellpreis)",
    )
    @app_commands.describe(
        code="Optional: einzelner Creator-Code",
        month="Monat YYYY-MM (Standard: aktueller UTC-Monat)",
    )
    async def cc_stats(
        self,
        interaction: discord.Interaction,
        code: str | None = None,
        month: str | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return

        month_key = (month or "").strip() or datetime.now(timezone.utc).strftime(
            "%Y-%m"
        )
        try:
            datetime.strptime(month_key, "%Y-%m")
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed(
                    "Ungültiger Monat", "Format: `YYYY-MM` (z.B. `2026-09`)."
                ),
                ephemeral=True,
            )
            return

        staff = await is_staff(self.bot, interaction)
        code_id: int | None = None
        owner_filter: int | None = None

        if code:
            row = await self.bot.db.get_discount_code(interaction.guild.id, code)
            if not row or str(row.get("kind") or "") != "creator":
                await interaction.response.send_message(
                    embed=error_embed(
                        "Nicht gefunden",
                        f"`{(code or '').upper()}` ist kein Creator-Code.",
                    ),
                    ephemeral=True,
                )
                return
            owner_id = row.get("owner_user_id")
            if not staff and (
                not owner_id or int(owner_id) != interaction.user.id
            ):
                await interaction.response.send_message(
                    embed=error_embed(
                        "Keine Berechtigung",
                        "Nur Staff oder der zugewiesene Creator.",
                    ),
                    ephemeral=True,
                )
                return
            code_id = int(row["id"])
        elif not staff:
            owner_filter = interaction.user.id

        pct = float(config.CREATOR_COMMISSION_PCT)
        stats = await self.bot.db.get_creator_commission_stats(
            interaction.guild.id,
            code_id=code_id,
            owner_user_id=owner_filter,
            month_key=month_key,
            commission_pct=pct,
        )

        if not staff and not stats:
            await interaction.response.send_message(
                embed=error_embed(
                    "Keine Creator-Codes",
                    "Dir ist kein Creator-Code zugewiesen "
                    "(`/cc add owner:@du`). Staff sieht alle Stats.",
                ),
                ephemeral=True,
            )
            return

        if not stats:
            await interaction.response.send_message(
                embed=success_embed(
                    "Creator Stats",
                    f"Keine Creator-Codes · Monat **{_month_label(month_key)}**.\n"
                    f"Provision: **{pct:g}%** vom Bestellpreis (vor Code-Rabatt).",
                ),
                ephemeral=True,
            )
            return

        lines: list[str] = []
        total_earn = 0.0
        total_sales = 0.0
        total_orders = 0
        for s in stats:
            total_earn += float(s["earnings"])
            total_sales += float(s["sales_base"])
            total_orders += int(s["orders_month"])
            label = f" — {s['label']}" if s.get("label") else ""
            owner = ""
            oid = s.get("owner_user_id")
            if oid:
                owner = f" · <@{int(oid)}>"
            lines.append(
                f"`{s['code']}`{label}{owner}\n"
                f" Bestellungen: **{int(s['orders_month'])}** · "
                f"Umsatz: **{format_price(float(s['sales_base']))}** · "
                f"**Verdienst: {format_price(float(s['earnings']))}** "
                f"({pct:g}%)"
            )

        body = (
            f"**Monat:** {_month_label(month_key)} (UTC) · "
            f"Reset am 1. jedes Monats\n"
            f"**Provision:** {pct:g}% vom Preis (vor Code-Rabatt)\n\n"
            + "\n".join(lines[:30])
        )
        if len(stats) > 1:
            body += (
                f"\n\n**Summe:** {total_orders} Bestellungen · "
                f"Umsatz {format_price(total_sales)} · "
                f"Verdienst **{format_price(total_earn)}**"
            )

        await interaction.response.send_message(
            embed=success_embed("🎬 Creator Stats", body[:3900]),
            ephemeral=True,
        )

    @cc.command(name="add", description="Creator-Code anlegen (Staff)")
    @app_commands.describe(
        code="Code (z.B. FERDI10)",
        discount_type="Rabattart",
        value="Wert — z.B. 10 (%) oder 50k",
        creator="Creator-Name / Label",
        owner="Discord-User des Creators (für /cc stats)",
        max_uses="Max. Gesamtnutzungen (leer = unbegrenzt)",
        max_per_user="Max. pro User",
    )
    @app_commands.choices(
        discount_type=[
            app_commands.Choice(name="Prozent (%)", value="percent"),
            app_commands.Choice(name="Betrag", value="amount"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def cc_add(
        self,
        interaction: discord.Interaction,
        code: str,
        discount_type: app_commands.Choice[str],
        value: str,
        creator: str | None = None,
        owner: discord.Member | None = None,
        max_uses: app_commands.Range[int, 1, 1_000_000] | None = None,
        max_per_user: app_commands.Range[int, 1, 100] = 1,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        try:
            dval = _parse_discount_value(discount_type.value, value)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Wert", str(e)), ephemeral=True
            )
            return
        if await self.bot.db.get_discount_code(interaction.guild.id, code):
            await interaction.response.send_message(
                embed=error_embed(
                    "Existiert",
                    f"`{code.upper()}` gibt es schon — `/cc set` zum Ändern.",
                ),
                ephemeral=True,
            )
            return
        code_id = await self.bot.db.create_discount_code(
            interaction.guild.id,
            code,
            discount_type=discount_type.value,
            discount_value=dval,
            max_uses=int(max_uses) if max_uses is not None else None,
            max_per_user=int(max_per_user),
            label=creator or "",
            created_by=interaction.user.id,
            kind="creator",
            owner_user_id=owner.id if owner else None,
        )
        owner_line = f"Owner: {owner.mention}\n" if owner else ""
        await interaction.response.send_message(
            embed=success_embed(
                "Creator-Code erstellt",
                f"`{code.strip().upper()}` — "
                f"{format_code_discount(discount_type.value, dval)}\n"
                + (f"Creator: **{creator}**\n" if creator else "")
                + owner_line
                + f"Provision: **{config.CREATOR_COMMISSION_PCT:g}%** · "
                f"`/cc stats`\nID `{code_id}`",
            ),
            ephemeral=True,
        )

    @cc.command(name="set", description="Creator-Code-Rabatt festlegen (Staff)")
    @app_commands.describe(
        code="Creator-Code",
        discount_type="Rabattart",
        value="Neuer Rabattwert",
        creator="Optional neues Label",
        owner="Optional: Discord-User des Creators",
    )
    @app_commands.choices(
        discount_type=[
            app_commands.Choice(name="Prozent (%)", value="percent"),
            app_commands.Choice(name="Betrag", value="amount"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def cc_set(
        self,
        interaction: discord.Interaction,
        code: str,
        discount_type: app_commands.Choice[str],
        value: str,
        creator: str | None = None,
        owner: discord.Member | None = None,
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        row = await self.bot.db.get_discount_code(interaction.guild.id, code)
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Nicht gefunden"), ephemeral=True
            )
            return
        try:
            dval = _parse_discount_value(discount_type.value, value)
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed("Ungültiger Wert", str(e)), ephemeral=True
            )
            return
        fields: dict = {
            "discount_type": discount_type.value,
            "discount_value": dval,
            "kind": "creator",
        }
        if creator is not None:
            fields["label"] = creator.strip()[:100]
        if owner is not None:
            fields["owner_user_id"] = owner.id
        await self.bot.db.update_discount_code(int(row["id"]), **fields)
        owner_note = f"\nOwner: {owner.mention}" if owner else ""
        await interaction.response.send_message(
            embed=success_embed(
                "Creator-Code aktualisiert",
                f"`{row['code']}` → "
                f"{format_code_discount(discount_type.value, dval)}"
                f"{owner_note}\n"
                "Beim Einlösen im Ticket wird der neue Rabatt übernommen.",
            ),
            ephemeral=True,
        )


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(DiscountCodesCog(bot))
