from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from utils.embeds import error_embed, success_embed
from utils.packs import resolve_pack_path, save_pack_attachment

if TYPE_CHECKING:
    from bot import ShopBot


async def apply_pack_attachment(
    bot: ShopBot,
    item_id: int,
    attachment: discord.Attachment,
    *,
    channel: discord.abc.Messageable | None = None,
) -> tuple[str, str]:
    """
    Speichert Pack lokal. Kein öffentlicher Channel-Post (Leak-Schutz).

    Optional: Archiv-Kopie nur in eine DM (nicht in Guild-Channels).
    Gibt (pack_file_rel, pack_link_url) zurück — pack_link oft leer,
    Lieferung läuft über die lokale Datei.
    """
    rel = await save_pack_attachment(item_id, attachment)
    # Kein öffentlicher Discord-CDN-Link speichern — sonst Leak im Ticket/Channel
    pack_link = ""

    # Nur in DMs re-hosten (Admin-Archiv), nie in Server-Channels
    if channel is not None and isinstance(channel, discord.DMChannel):
        path = resolve_pack_path(rel)
        if path is not None:
            try:
                posted = await channel.send(
                    content=f"📦 Pack-Archiv Item `{item_id}` (privat).",
                    file=discord.File(path, filename=path.name),
                )
                if posted.attachments:
                    # Nur für Admin-DM; Delivery nutzt trotzdem pack_file lokal
                    pack_link = posted.attachments[0].url
            except discord.HTTPException:
                pass

    await bot.db.update_item(item_id, pack_file=rel, pack_link=pack_link[:500])
    return rel, pack_link


async def collect_pack_from_user(
    bot: ShopBot,
    interaction: discord.Interaction,
    item_id: int,
) -> None:
    """
    Pack per Drag & Drop im Channel (ohne DM) oder Fallback per DM / Slash.
    Channel-Drops werden nach dem Speichern gelöscht (kein Leak).
    """
    user = interaction.user
    timeout_s = 120.0
    guild_channel = interaction.channel

    # 1) Channel-Drop (Message Content Intent) — Datei speichern, Nachricht löschen
    if isinstance(guild_channel, discord.TextChannel):
        await interaction.followup.send(
            embed=success_embed(
                f"Pack für Item `{item_id}`",
                f"{user.mention}: Droppe die Pack-Datei **hier** "
                f"(innerhalb {int(timeout_s)} Sekunden).\n"
                "Die Nachricht wird danach **gelöscht** — Pack bleibt nur intern.\n"
                "Alternative: DM an den Bot oder `/item setpack` mit Anhang.",
            ),
            ephemeral=True,
        )

        def ch_check(message: discord.Message) -> bool:
            return (
                message.author.id == user.id
                and message.channel.id == guild_channel.id
                and bool(message.attachments)
            )

        try:
            message = await bot.wait_for(
                "message", check=ch_check, timeout=timeout_s
            )
            attachment = message.attachments[0]
            # Nicht in Guild-Channel posten
            _rel, _pack_link = await apply_pack_attachment(
                bot, item_id, attachment, channel=None
            )
            try:
                await message.delete()
            except discord.HTTPException:
                try:
                    await message.add_reaction("✅")
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                embed=success_embed(
                    "Pack gespeichert",
                    f"**{attachment.filename}** → Item `{item_id}` "
                    "(lokal, nicht öffentlich).",
                ),
                ephemeral=True,
            )
            return
        except asyncio.TimeoutError:
            await interaction.followup.send(
                embed=error_embed(
                    "Zeit abgelaufen",
                    "Keine Datei im Channel erhalten — versuche DM…",
                ),
                ephemeral=True,
            )

    # 2) DM-Fallback
    try:
        dm = await user.create_dm()
        await dm.send(
            embed=success_embed(
                f"Pack für Item `{item_id}`",
                "Ziehe die Pack-Datei per **Drag & Drop** in diesen Chat "
                f"und sende sie (innerhalb {int(timeout_s)} Sekunden).",
            )
        )
        await interaction.followup.send(
            embed=success_embed(
                "DM geöffnet",
                "Schau in deine DMs und sende die Pack-Datei.",
            ),
            ephemeral=True,
        )

        def dm_check(message: discord.Message) -> bool:
            return (
                message.author.id == user.id
                and isinstance(message.channel, discord.DMChannel)
                and bool(message.attachments)
            )

        message = await bot.wait_for("message", check=dm_check, timeout=timeout_s)
        attachment = message.attachments[0]
        _rel, pack_link = await apply_pack_attachment(
            bot, item_id, attachment, channel=dm
        )
        await dm.send(
            embed=success_embed(
                "Pack gespeichert",
                f"**{attachment.filename}** intern gespeichert."
                + (f"\nArchiv-Link (nur DM): {pack_link}" if pack_link else ""),
            )
        )
        await interaction.followup.send(
            embed=success_embed(
                "Pack gespeichert",
                f"**{attachment.filename}** → Item `{item_id}` (nicht öffentlich).",
            ),
            ephemeral=True,
        )
        return
    except discord.Forbidden:
        pass
    except asyncio.TimeoutError:
        await interaction.followup.send(
            embed=error_embed(
                "Zeit abgelaufen",
                "Keine Datei erhalten.\n"
                f"Alternative: `/item setpack item_id:{item_id}` und Datei anhängen.",
            ),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=error_embed(
            "Upload nicht möglich",
            "Channel-Drop und DM fehlgeschlagen.\n\n"
            f"**So geht's:** `/item setpack` → Item wählen → Datei anhängen.\n"
            f"Item-ID: `{item_id}`",
        ),
        ephemeral=True,
    )


class PackUploadView(discord.ui.View):
    """Ein Klick startet den Pack-Upload (Channel-Drop oder DM)."""

    def __init__(self, bot: ShopBot, item_id: int, user_id: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.item_id = item_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Nur der Admin, der das Item angelegt hat.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Pack hier droppen",
        style=discord.ButtonStyle.success,
        emoji="📎",
    )
    async def upload(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await collect_pack_from_user(self.bot, interaction, self.item_id)
        self.stop()
