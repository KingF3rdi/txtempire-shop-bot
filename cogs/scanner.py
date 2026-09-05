from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.archive_scanner import is_scannable_filename, scan_archive_bytes
from utils.embeds import error_embed, success_embed, warn_embed
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot


class ScannerCog(commands.Cog):
    """ZIP/RAR-Scanner auf RATs, Stealer und verdächtige Dateien."""

    def __init__(self, bot: ShopBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="scan",
        description="ZIP/RAR/JAR auf RATs, Stealer und verdächtige Dateien scannen",
    )
    @app_commands.describe(file="Archiv-Datei (.zip / .rar / .jar)")
    async def scan(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
    ) -> None:
        if not is_scannable_filename(file.filename):
            await interaction.response.send_message(
                embed=error_embed(
                    "Falscher Dateityp",
                    "Bitte eine **.zip**, **.rar** oder **.jar** Datei anhängen.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            data = await file.read()
        except discord.HTTPException as e:
            await interaction.followup.send(
                embed=error_embed("Download fehlgeschlagen", str(e)[:500]),
                ephemeral=True,
            )
            return

        result = scan_archive_bytes(data, file.filename or "archive")
        if result.error and not result.findings:
            await interaction.followup.send(
                embed=warn_embed("Scan", result.summary()),
                ephemeral=True,
            )
            return
        if result.is_clean:
            await interaction.followup.send(
                embed=success_embed("Scan sauber", result.summary()),
                ephemeral=True,
            )
            return

        embed = warn_embed("Verdächtige Datei", result.summary())
        if result.is_blocked:
            embed.title = "⛔ Kritische Treffer (RAT / Malware-Indikatoren)"
            embed.color = discord.Color.dark_red()
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="scanpack",
        description="Gespeicherte Pack-Datei eines Items scannen (Staff)",
    )
    @app_commands.describe(item="Item-ID")
    @app_commands.default_permissions(manage_guild=True)
    async def scanpack(
        self, interaction: discord.Interaction, item: int
    ) -> None:
        assert interaction.guild is not None
        if not await is_staff(self.bot, interaction):
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        row = await self.bot.db.get_item(item)
        if not row or int(row["guild_id"]) != interaction.guild.id:
            await interaction.response.send_message(
                embed=error_embed("Item nicht gefunden"), ephemeral=True
            )
            return
        from utils.packs import resolve_pack_path

        path = resolve_pack_path(row.get("pack_file"))
        if path is None:
            await interaction.response.send_message(
                embed=error_embed("Kein Pack", "Dieses Item hat keine lokale Pack-Datei."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        from utils.archive_scanner import scan_archive_path

        result = scan_archive_path(path)
        if result.is_clean:
            await interaction.followup.send(
                embed=success_embed(
                    f"Pack Item `{item}` sauber", result.summary()
                ),
                ephemeral=True,
            )
            return
        embed = warn_embed(f"Pack Item `{item}`", result.summary())
        if result.is_blocked:
            embed.color = discord.Color.dark_red()
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: ShopBot) -> None:
    await bot.add_cog(ScannerCog(bot))
