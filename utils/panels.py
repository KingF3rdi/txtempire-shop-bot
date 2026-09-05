from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord

from config import DEFAULT_PAYEE, PAYMENT_NOTICE
from utils.embeds import base_embed

if TYPE_CHECKING:
    from bot import ShopBot


@dataclass(frozen=True)
class PanelFilter:
    """Kategorie-Filter für ein Buy-Panel."""

    mode: str  # all | include | exclude | single
    category_ids: tuple[int, ...] = ()

    @classmethod
    def all_categories(cls) -> PanelFilter:
        return cls(mode="all")

    @classmethod
    def single(cls, category_id: int) -> PanelFilter:
        return cls(mode="single", category_ids=(category_id,))

    @classmethod
    def from_slot_row(cls, row: dict[str, Any] | None) -> PanelFilter:
        if not row:
            return cls.all_categories()
        mode = str(row.get("filter_mode") or "all")
        try:
            ids = tuple(int(i) for i in json.loads(row.get("category_ids") or "[]"))
        except (json.JSONDecodeError, TypeError, ValueError):
            ids = ()
        if mode not in ("all", "include", "exclude"):
            mode = "all"
        if mode == "include" and not ids:
            mode = "all"
        return cls(mode=mode, category_ids=ids)


def apply_panel_filter(
    categories: list[dict], panel_filter: PanelFilter
) -> list[dict]:
    if panel_filter.mode == "all":
        return categories
    id_set = set(panel_filter.category_ids)
    if panel_filter.mode == "single":
        if len(id_set) != 1:
            return []
        only = next(iter(id_set))
        return [c for c in categories if int(c["id"]) == only]
    if panel_filter.mode == "include":
        return [c for c in categories if int(c["id"]) in id_set]
    if panel_filter.mode == "exclude":
        return [c for c in categories if int(c["id"]) not in id_set]
    return categories


def panel_filter_summary(panel_filter: PanelFilter) -> str:
    if panel_filter.mode == "all":
        return "Alle Kategorien"
    if panel_filter.mode == "single":
        return "Eine Kategorie"
    if panel_filter.mode == "include":
        n = len(panel_filter.category_ids)
        return f"Nur {n} Kategorie(n)" if n else "Keine Kategorien (Include leer)"
    if panel_filter.mode == "exclude":
        n = len(panel_filter.category_ids)
        return f"Alle außer {n} Kategorie(n)" if n else "Alle Kategorien"
    return "Alle Kategorien"


def buy_panel_suffix(category_id: int | None) -> str:
    return str(category_id) if category_id is not None else "all"


def buy_panel_slot_suffix(slot: int) -> str:
    return f"slot:{slot}"


def is_valid_buy_panel_message(message: discord.Message, slot: int) -> bool:
    """Prüft ob eine Nachricht die Buy-Panel-Buttons für den Slot hat.

    Credits-Button ist optional (abhängig von Panel-Config).
    """
    required = {f"buy:{action}:slot:{slot}" for action in ("start", "cart", "info")}
    found: set[str] = set()
    for row in message.components:
        for item in row.children:
            cid = getattr(item, "custom_id", None)
            if cid:
                found.add(cid)
    return required.issubset(found)


def parse_buy_custom_id(custom_id: str | None) -> tuple[int | None, int | None]:
    """Liest Panel-Slot oder Legacy-Kategorie aus einem Buy-Button-custom_id."""
    if not custom_id or not custom_id.startswith("buy:"):
        return None, None
    parts = custom_id.split(":")
    if len(parts) >= 4 and parts[2] == "slot":
        try:
            return int(parts[3]), None
        except ValueError:
            return None, None
    if len(parts) >= 3 and parts[2] == "all":
        return None, None
    if len(parts) >= 3:
        try:
            return None, int(parts[2])
        except ValueError:
            return None, None
    return None, None


def parse_buy_panel_custom_id(
    custom_id: str | None,
) -> tuple[str | None, int | None, int | None]:
    """Liest Aktion (start/cart/info), Slot und Legacy-Kategorie aus custom_id."""
    if not custom_id or not custom_id.startswith("buy:"):
        return None, None, None
    parts = custom_id.split(":")
    action = parts[1] if len(parts) > 1 else None
    if len(parts) >= 4 and parts[2] == "slot":
        try:
            return action, int(parts[3]), None
        except ValueError:
            return action, None, None
    if len(parts) >= 3 and parts[2] == "all":
        return action, None, None
    if len(parts) >= 3:
        try:
            return action, None, int(parts[2])
        except ValueError:
            return action, None, None
    return action, None, None


def build_buy_panel_embed(
    *,
    categories: list[dict],
    settings: dict,
    category: dict | None = None,
    title: str | None = None,
    panel_filter: PanelFilter | None = None,
    slot: int | None = None,
    credits_enabled: bool = False,
) -> discord.Embed:
    filtered = apply_panel_filter(categories, panel_filter or PanelFilter.all_categories())

    credits_line = (
        "\n• **Credits** — Credits kaufen (1 Credit = 100k)"
        if credits_enabled
        else ""
    )

    if category:
        panel_title = title or category["name"]
        description = (
            (category.get("description") or "").strip()
            or f"Kaufe Artikel aus **{category['name']}**.\n\n"
            "• **Kaufen** — Items dieser Kategorie wählen\n"
            "• **Warenkorb** — Überblick & Checkout\n"
            "• **Info** — Zahlungsablauf"
            f"{credits_line}"
        )
        embed = base_embed(panel_title, description)
        emoji = (category.get("emoji") or "").strip() or "•"
        embed.add_field(
            name="Kategorie",
            value=f"{emoji} **{category['name']}**",
            inline=False,
        )
    else:
        default_title = f"Buy Panel {slot}" if slot else "Buy Panel"
        panel_title = title or default_title
        description = (
            "Hier kannst du Artikel kaufen.\n\n"
            "• **Kaufen** — Kategorie & Item wählen, in den Warenkorb legen\n"
            "• **Warenkorb** — Überblick, Gesamtpreis, Checkout\n"
            "• **Info** — Zahlungsablauf"
            f"{credits_line}"
        )
        embed = base_embed(panel_title, description)
        pf = panel_filter or PanelFilter.all_categories()
        if pf.mode != "all" or slot:
            embed.add_field(
                name="Filter",
                value=f"**{panel_filter_summary(pf)}**",
                inline=False,
            )
        if filtered:
            embed.add_field(
                name="Kategorien",
                value="\n".join(
                    f"{c.get('emoji') or '•'} **{c['name']}**" for c in filtered[:20]
                )
                + (f"\n_…und {len(filtered) - 20} weitere_" if len(filtered) > 20 else ""),
                inline=False,
            )
        else:
            embed.add_field(
                name="Hinweis",
                value="Keine Kategorien für dieses Panel — Admin: `/buypanelconfig`.",
                inline=False,
            )

    if credits_enabled:
        embed.add_field(
            name="Credits",
            value=(
                "**Aktiv** — 1 Credit = **100k**\n"
                "Nach dem Checkout: **Quick Buy** im Ticket mit Credits bezahlen."
            ),
            inline=False,
        )

    name = settings.get("payee_a_label") or DEFAULT_PAYEE
    embed.add_field(name="Zahlung", value=f"**{PAYMENT_NOTICE}**", inline=False)
    embed.set_footer(text=f"{PAYMENT_NOTICE} · Zahlung an {name}")
    return embed


async def get_panel_filter_for_slot(
    bot: "ShopBot", guild_id: int, slot: int
) -> tuple[PanelFilter, str | None]:
    row = await bot.db.ensure_buy_panel_slot(guild_id, slot)
    return PanelFilter.from_slot_row(row), row.get("title")


def register_slot_panel_views(bot: "ShopBot", *, force: bool = False) -> None:
    """Registriert Slot-Panel-Views (1/2) und Legacy-All-Panel.

    Slot-Views werden inkl. Credits-Button registriert, damit custom_ids
    auch nach Bot-Neustart funktionieren (Button erscheint nur wenn aktiv).
    """
    registered: set[str] = getattr(bot, "_buy_panel_registered", set())
    from views.shop_views import BuyPanelView

    def _register_slot(slot: int) -> None:
        suffix = buy_panel_slot_suffix(slot)
        if not force and suffix in registered:
            return
        bot.add_view(BuyPanelView(bot, panel_slot=slot, credits_enabled=True))
        registered.add(suffix)
        print(f"[BuyPanel] View registriert: slot {slot} (buy:start:slot:{slot})")

    legacy = buy_panel_suffix(None)
    if force or legacy not in registered:
        bot.add_view(BuyPanelView(bot, category_id=None))
        registered.add(legacy)
        print("[BuyPanel] View registriert: all (buy:start:all)")

    _register_slot(1)
    _register_slot(2)
    bot._buy_panel_registered = registered


async def register_category_panel_views(bot: "ShopBot", *, force: bool = False) -> None:
    """Registriert Kategorie-Panels (nach Katalog-Sync)."""
    registered: set[str] = getattr(bot, "_buy_panel_registered", set())
    from views.shop_views import BuyPanelView

    rows = await bot.db.list_all_categories()
    for row in rows:
        category_id = int(row["id"])
        suffix = buy_panel_suffix(category_id)
        if not force and suffix in registered:
            continue
        bot.add_view(BuyPanelView(bot, category_id=category_id))
        registered.add(suffix)

    bot._buy_panel_registered = registered


async def register_buy_panel_views(bot: "ShopBot", *, force: bool = False) -> None:
    """Registriert alle Buy-Panel-Views (Slots + Kategorien)."""
    register_slot_panel_views(bot)
    await register_category_panel_views(bot, force=force)


async def refresh_slot_panel(bot: "ShopBot", guild: discord.Guild, slot: int) -> str:
    """Aktualisiert die gespeicherte Panel-Nachricht per Edit (kein Neu-Posten)."""
    row = await bot.db.ensure_buy_panel_slot(guild.id, slot)
    channel_id = row.get("channel_id")
    message_id = row.get("message_id")
    if not channel_id or not message_id:
        return f"Panel {slot}: keine gespeicherte Nachricht — bitte `/panelsetup`"
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return f"Panel {slot}: Channel nicht gefunden"

    cats = await bot.db.list_categories(guild.id)
    settings = await bot.db.ensure_guild(guild.id)
    panel_filter, stored_title = await get_panel_filter_for_slot(bot, guild.id, slot)
    filtered = apply_panel_filter(cats, panel_filter)
    credits_on = bool(int(row.get("credits_enabled") or 0))
    embed = build_buy_panel_embed(
        categories=filtered,
        settings=settings,
        title=stored_title,
        panel_filter=panel_filter,
        slot=slot,
        credits_enabled=credits_on,
    )
    await ensure_buy_panel_slot_view(bot, slot)
    from views.shop_views import BuyPanelView

    view = BuyPanelView(bot, panel_slot=slot, credits_enabled=credits_on)
    try:
        msg = await channel.fetch_message(int(message_id))
    except discord.NotFound:
        # Nur wenn die alte Nachricht fehlt: neu posten
        new_msg = await channel.send(embed=embed, view=view)
        await bot.db.update_buy_panel_message(
            guild.id, slot, channel_id=channel.id, message_id=new_msg.id
        )
        return f"Panel {slot}: Nachricht fehlte — neu gepostet in {channel.mention}"

    try:
        await msg.edit(embed=embed, view=view)
    except discord.HTTPException as e:
        return f"Panel {slot}: Edit fehlgeschlagen ({e})"
    return f"Panel {slot}: aktualisiert in {channel.mention}"


async def refresh_all_saved_buy_panels(bot: "ShopBot", guild_id: int | None = None) -> list[str]:
    """Aktualisiert gespeicherte Buy Panels nach Bot-Start oder Config."""
    if guild_id is not None:
        guild = bot.get_guild(guild_id)
        if guild is None:
            return [f"Guild {guild_id} nicht gefunden"]
        return [await refresh_slot_panel(bot, guild, slot) for slot in (1, 2)]

    results: list[str] = []
    guild_ids = await bot.db.list_guilds_with_panel_messages()
    for gid in guild_ids:
        guild = bot.get_guild(gid)
        if guild is None:
            continue
        for slot in (1, 2):
            results.append(await refresh_slot_panel(bot, guild, slot))
    return results


async def ensure_buy_panel_view(bot: "ShopBot", category_id: int | None) -> None:
    registered: set[str] = getattr(bot, "_buy_panel_registered", set())
    suffix = buy_panel_suffix(category_id)
    if suffix in registered:
        return
    from views.shop_views import BuyPanelView

    bot.add_view(BuyPanelView(bot, category_id=category_id))
    registered.add(suffix)
    bot._buy_panel_registered = registered


async def ensure_buy_panel_slot_view(bot: "ShopBot", slot: int) -> None:
    registered: set[str] = getattr(bot, "_buy_panel_registered", set())
    suffix = buy_panel_slot_suffix(slot)
    if suffix in registered:
        return
    from views.shop_views import BuyPanelView

    bot.add_view(BuyPanelView(bot, panel_slot=slot, credits_enabled=True))
    registered.add(suffix)
    bot._buy_panel_registered = registered
