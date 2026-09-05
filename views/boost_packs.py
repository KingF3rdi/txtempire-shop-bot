from __future__ import annotations

import json
from typing import TYPE_CHECKING

import discord

import config
from utils.embeds import error_embed, success_embed
from utils.packs import resolve_pack_path

if TYPE_CHECKING:
    from bot import ShopBot


def item_has_pack(item: dict) -> bool:
    return bool(
        (item.get("pack_file") or "").strip()
        or (item.get("pack_link") or "").strip()
        or (item.get("pack_dm_text") or "").strip()
    )


async def deliver_boost_packs_dm(
    user: discord.abc.User,
    items: list[dict],
) -> tuple[int, bool]:
    """Liefert gewählte Packs per DM. Returns (sent_count, dm_ok)."""
    sent = 0
    try:
        await user.send(
            embed=success_embed(
                "🚀 Boost-Packs",
                f"Hier sind deine **{len(items)}** gewählten Pack(s) — danke fürs Boosten!",
            )
        )
    except discord.HTTPException:
        return 0, False

    for item in items:
        name = item.get("name") or "Pack"
        parts: list[str] = [f"**{name}**"]
        dm_text = (item.get("pack_dm_text") or "").strip()
        link = (item.get("pack_link") or "").strip()
        if dm_text:
            parts.append(dm_text)
        if link:
            parts.append(f"Link: {link}")
        path = resolve_pack_path(item.get("pack_file"))
        try:
            if path is not None:
                await user.send(
                    content="\n".join(parts),
                    file=discord.File(path, filename=path.name),
                )
            else:
                await user.send(content="\n".join(parts))
            sent += 1
        except discord.HTTPException:
            break
    return sent, True


class BoostPackSelect(discord.ui.Select):
    def __init__(
        self,
        bot: ShopBot,
        guild_id: int,
        options: list[discord.SelectOption],
        *,
        max_values: int,
    ) -> None:
        super().__init__(
            placeholder=f"Bis zu {max_values} Pack(s) wählen…",
            min_values=1,
            max_values=max(1, max_values),
            options=options,
            custom_id=f"boostpacks:select:{guild_id}",
        )
        self.bot = bot
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await finalize_boost_pack_selection(
            self.bot,
            interaction,
            guild_id=self.guild_id,
            item_ids=[int(v) for v in self.values],
        )


class BoostPackPickView(discord.ui.View):
    """Ephemeral/DM View zum Auswählen der Boost-Packs."""

    def __init__(
        self,
        bot: ShopBot,
        guild_id: int,
        options: list[discord.SelectOption],
        *,
        max_values: int,
    ) -> None:
        super().__init__(timeout=600)
        self.add_item(
            BoostPackSelect(bot, guild_id, options, max_values=max_values)
        )


class BoostThanksView(discord.ui.View):
    """Persistenter Button in der Dankes-DM."""

    def __init__(self, bot: ShopBot | None = None) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Packs auswählen",
        style=discord.ButtonStyle.success,
        custom_id="boost:pickpacks",
        emoji="📦",
    )
    async def pick(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        bot = self.bot or interaction.client  # type: ignore[assignment]
        guild_id = config.GUILD_ID
        # Versuche Guild aus Claim-DB
        if not guild_id:
            # Suche erste Claim-Zeile des Users
            rows = await bot.db.fetchall(
                "SELECT guild_id FROM boost_claims WHERE user_id = ? LIMIT 1",
                (interaction.user.id,),
            )
            if rows:
                guild_id = int(rows[0]["guild_id"])
        if not guild_id:
            await interaction.response.send_message(
                embed=error_embed(
                    "Server unbekannt",
                    "Nutze `/boostpacks` auf dem Discord-Server.",
                ),
                ephemeral=True,
            )
            return
        await open_boost_pack_picker(bot, interaction, guild_id)


async def open_boost_pack_picker(
    bot: ShopBot,
    interaction: discord.Interaction,
    guild_id: int,
) -> None:
    claim = await bot.db.get_boost_claim(guild_id, interaction.user.id)
    if not claim:
        msg = error_embed(
            "Kein Boost-Claim",
            "Booste den Server zuerst — dann kannst du Packs wählen.\n"
            "Oder Staff: `/boostgrant`.",
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
        return

    allowed = int(claim["packs_allowed"])
    try:
        already = set(json.loads(claim.get("claimed_item_ids") or "[]"))
    except json.JSONDecodeError:
        already = set()
    remaining = max(0, allowed - len(already))
    if remaining <= 0:
        msg = success_embed(
            "Alles abgeholt",
            f"Du hast bereits **{allowed}** Pack(s) für deinen Boost erhalten.",
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
        return

    items = await bot.db.list_items(guild_id, active_only=True)
    pack_items = [
        i
        for i in items
        if item_has_pack(i) and int(i["id"]) not in already
    ][:25]
    if not pack_items:
        msg = error_embed(
            "Keine Packs",
            "Aktuell sind keine wählbaren Packs hinterlegt.",
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
        return

    max_v = min(remaining, len(pack_items), 25)
    options = [
        discord.SelectOption(
            label=(i["name"] or f"Item {i['id']}")[:100],
            value=str(i["id"]),
            description=f"#{i['id']}"[:100],
        )
        for i in pack_items
    ]
    view = BoostPackPickView(bot, guild_id, options, max_values=max_v)
    body = (
        f"Du darfst noch **{remaining}** Pack(s) wählen "
        f"(Kontingent: {allowed}, bereits: {len(already)}).\n"
        f"Wähle bis zu **{max_v}** aus der Liste."
    )
    if interaction.response.is_done():
        await interaction.followup.send(
            embed=success_embed("Boost-Packs wählen", body),
            view=view,
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            embed=success_embed("Boost-Packs wählen", body),
            view=view,
            ephemeral=True,
        )


async def finalize_boost_pack_selection(
    bot: ShopBot,
    interaction: discord.Interaction,
    *,
    guild_id: int,
    item_ids: list[int],
) -> None:
    claim = await bot.db.get_boost_claim(guild_id, interaction.user.id)
    if not claim:
        await interaction.response.send_message(
            embed=error_embed("Kein Claim"), ephemeral=True
        )
        return
    try:
        already = set(json.loads(claim.get("claimed_item_ids") or "[]"))
    except json.JSONDecodeError:
        already = set()
    remaining = max(0, int(claim["packs_allowed"]) - len(already))
    chosen = [i for i in item_ids if i not in already][:remaining]
    if not chosen:
        await interaction.response.send_message(
            embed=error_embed("Nichts Neues gewählt"), ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    items_out: list[dict] = []
    for iid in chosen:
        row = await bot.db.get_item(iid)
        if row and int(row["guild_id"]) == guild_id and item_has_pack(row):
            items_out.append(dict(row))

    if not items_out:
        await interaction.followup.send(
            embed=error_embed("Packs ungültig"), ephemeral=True
        )
        return

    sent, dm_ok = await deliver_boost_packs_dm(interaction.user, items_out)
    await bot.db.add_boost_claimed_items(
        guild_id, interaction.user.id, [int(i["id"]) for i in items_out]
    )
    if not dm_ok:
        await interaction.followup.send(
            embed=error_embed(
                "DM geschlossen",
                "Öffne DMs und nutze erneut `/boostpacks`.",
            ),
            ephemeral=True,
        )
        return
    left_claim = await bot.db.get_boost_claim(guild_id, interaction.user.id)
    left = 0
    if left_claim:
        try:
            c = json.loads(left_claim.get("claimed_item_ids") or "[]")
        except json.JSONDecodeError:
            c = []
        left = max(0, int(left_claim["packs_allowed"]) - len(c))
    await interaction.followup.send(
        embed=success_embed(
            "Packs gesendet",
            f"**{sent}** Pack(s) per DM.\n"
            + (f"Noch wählbar: **{left}**." if left else "Kontingent voll."),
        ),
        ephemeral=True,
    )


async def send_boost_thanks_dm(
    bot: ShopBot,
    member: discord.Member,
    *,
    packs_allowed: int,
    tier: int,
    upgrade: bool = False,
) -> bool:
    if upgrade:
        title = "💜 Danke für den 2. Boost!"
        body = (
            f"Wow — du boostest **{member.guild.name}** jetzt doppelt!\n\n"
            f"Als Dankeschön darfst du dir **{packs_allowed} Packs** "
            f"aussuchen (Shop-Artikel mit Pack).\n\n"
            "Klicke unten **Packs auswählen** oder nutze `/boostpacks` auf dem Server."
        )
    else:
        title = "💜 Danke für den Boost!"
        body = (
            f"Vielen Dank, dass du **{member.guild.name}** boostest!\n\n"
            f"Als Dankeschön darfst du dir **{packs_allowed} Packs** "
            f"frei aussuchen.\n\n"
            "Klicke **Packs auswählen** oder `/boostpacks` auf dem Server.\n"
            f"_Bei einem 2. Boost steigen die Packs auf "
            f"**{config.BOOST_PACKS_TIER2}**._"
        )
    embed = success_embed(title, body)
    embed.set_footer(text=f"Boost-Tier {tier} · TxTEmpire")
    try:
        await member.send(embed=embed, view=BoostThanksView(bot))
        return True
    except discord.HTTPException:
        return False
