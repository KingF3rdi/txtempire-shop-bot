"""Eigenes Support-System für Tier-Boost — getrennt von utils/boost_support.py
(das jetzt exklusiv Account-Buy gehört). Eigene Support-Rolle plus eine Liste
einzelner Support-Mitglieder ohne diese Rolle.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot import ShopBot


async def ensure_table(bot: "ShopBot") -> None:
    await bot.db.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tier_support_settings (
            guild_id INTEGER PRIMARY KEY,
            staff_role_id INTEGER,
            extra_staff_ids TEXT NOT NULL DEFAULT ''
        );
        """
    )
    await bot.db.db.commit()


async def _get_settings(bot: "ShopBot", guild_id: int) -> dict:
    row = await bot.db.fetchone(
        "SELECT * FROM tier_support_settings WHERE guild_id = ?", (guild_id,)
    )
    if row:
        return dict(row)
    return {"guild_id": guild_id, "staff_role_id": None, "extra_staff_ids": ""}


async def get_staff_role_id(bot: "ShopBot", guild_id: int) -> int | None:
    settings = await _get_settings(bot, guild_id)
    return int(settings["staff_role_id"]) if settings.get("staff_role_id") else None


async def set_staff_role_id(bot: "ShopBot", guild_id: int, role_id: int) -> None:
    await bot.db.db.execute(
        """
        INSERT INTO tier_support_settings (guild_id, staff_role_id) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET staff_role_id = excluded.staff_role_id
        """,
        (guild_id, role_id),
    )
    await bot.db.db.commit()


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


async def get_extra_staff_ids(bot: "ShopBot", guild_id: int) -> list[int]:
    settings = await _get_settings(bot, guild_id)
    return _parse_ids(str(settings.get("extra_staff_ids") or ""))


async def add_extra_staff(bot: "ShopBot", guild_id: int, user_id: int) -> None:
    ids = set(await get_extra_staff_ids(bot, guild_id))
    ids.add(user_id)
    await bot.db.db.execute(
        """
        INSERT INTO tier_support_settings (guild_id, extra_staff_ids) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET extra_staff_ids = excluded.extra_staff_ids
        """,
        (guild_id, ",".join(str(i) for i in sorted(ids))),
    )
    await bot.db.db.commit()


async def remove_extra_staff(bot: "ShopBot", guild_id: int, user_id: int) -> None:
    ids = set(await get_extra_staff_ids(bot, guild_id))
    ids.discard(user_id)
    await bot.db.db.execute(
        """
        INSERT INTO tier_support_settings (guild_id, extra_staff_ids) VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET extra_staff_ids = excluded.extra_staff_ids
        """,
        (guild_id, ",".join(str(i) for i in sorted(ids))),
    )
    await bot.db.db.commit()


async def is_tier_staff(bot: "ShopBot", interaction: discord.Interaction) -> bool:
    member = interaction.user
    if isinstance(member, discord.Member) and member.guild_permissions.administrator:
        return True
    assert interaction.guild is not None
    guild_id = interaction.guild.id
    if member.id in await get_extra_staff_ids(bot, guild_id):
        return True
    role_id = await get_staff_role_id(bot, guild_id)
    if role_id and isinstance(member, discord.Member):
        return any(r.id == role_id for r in member.roles)
    return False


async def tier_staff_role(bot: "ShopBot", guild: discord.Guild) -> discord.Role | None:
    role_id = await get_staff_role_id(bot, guild.id)
    return guild.get_role(role_id) if role_id else None
