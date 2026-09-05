from __future__ import annotations

import discord
from discord.ext import commands

import config
from db.database import Database


class ShopBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.dm_messages = True
        # Message Content Intent nicht nötig: Pack-Upload läuft über DM / Slash-Anhang.
        super().__init__(command_prefix="!", intents=intents)
        self.db = Database(config.DATABASE_PATH)

    async def setup_hook(self) -> None:
        self.tree.on_error = self._on_app_command_error
        await self.db.connect()
        for ext in (
            "cogs.setup",
            "cogs.admin_panel",
            "cogs.shop",
            "cogs.daily_deals",
            "cogs.tickets",
            "cogs.vouch",
        ):
            await self.load_extension(ext)

        # Persistent views
        from utils.panels import register_slot_panel_views, register_category_panel_views
        from views.daily_deal_views import register_daily_deal_views
        from views.shop_views import ShopPanelView
        from views.ticket_views import TicketOrderView

        self.add_view(ShopPanelView(self))
        register_slot_panel_views(self)
        await register_category_panel_views(self)
        self.add_view(TicketOrderView(self))
        n_deals = await register_daily_deal_views(self)
        if n_deals:
            print(f"[DailyDeal] {n_deals} aktive Deal-View(s) registriert")

        try:
            if config.GUILD_ID:
                guild = discord.Object(id=config.GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"Slash-Commands synced für Guild {config.GUILD_ID}")
                # Global sync damit /vouch auch per DM funktioniert
                await self.tree.sync()
                print("Slash-Commands global synced (DM: /vouch)")
            else:
                await self.tree.sync()
                print("Slash-Commands global synced")
        except discord.Forbidden:
            print(
                "WARNUNG: Guild-Command-Sync fehlgeschlagen (Missing Access).\n"
                "  - Bot muss auf dem Server sein\n"
                "  - Invite mit Scope applications.commands\n"
                "  - GUILD_ID prüfen oder leer lassen\n"
                "Fallback: global sync…"
            )
            try:
                await self.tree.sync()
                print("Slash-Commands global synced (Fallback)")
            except Exception as e:
                print(f"Command-Sync fehlgeschlagen: {e}")
        except Exception as e:
            print(f"Command-Sync fehlgeschlagen: {e}")

    async def _on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        import traceback

        print(f"[Command-Fehler] /{interaction.command}: {error!r}")
        traceback.print_exception(type(error), error, error.__traceback__)
        from utils.embeds import error_embed

        embed = error_embed(
            "Fehler",
            "Da ist etwas schiefgelaufen. Bitte versuch es erneut.",
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass

    async def close(self) -> None:
        await self.db.close()
        await super().close()

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Fallback für Buy-Panel- / Daily-Deal-Buttons ohne registrierte View."""
        if interaction.type != discord.InteractionType.component:
            return
        data = interaction.data or {}
        custom_id = data.get("custom_id") or ""

        if custom_id.startswith("deal:buy:"):
            component_type = data.get("component_type")
            key = (component_type, custom_id)
            store = self._connection._view_store
            message_id = interaction.message.id if interaction.message else None
            if message_id is not None and store._views.get(message_id, {}).get(key):
                return
            if store._views.get(None, {}).get(key):
                return
            from views.daily_deal_views import handle_daily_deal_interaction

            try:
                await handle_daily_deal_interaction(self, interaction)
            except Exception as exc:
                import traceback

                print(f"[DailyDeal] Fallback fehlgeschlagen ({custom_id}): {exc!r}")
                traceback.print_exc()
            return

        if not custom_id.startswith("buy:"):
            return

        component_type = data.get("component_type")
        key = (component_type, custom_id)
        store = self._connection._view_store
        message_id = interaction.message.id if interaction.message else None
        if message_id is not None and store._views.get(message_id, {}).get(key):
            return
        if store._views.get(None, {}).get(key):
            return

        from views.shop_views import handle_buy_panel_interaction

        try:
            await handle_buy_panel_interaction(self, interaction)
        except Exception as exc:
            import traceback

            print(f"[BuyPanel] Fallback-Handler fehlgeschlagen ({custom_id}): {exc!r}")
            traceback.print_exc()
            from utils.embeds import error_embed

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        embed=error_embed(
                            "Fehler",
                            "Diese Aktion ist fehlgeschlagen. "
                            "Bitte `/panelsetup` oder Bot neu starten.",
                        ),
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        embed=error_embed(
                            "Fehler",
                            "Diese Aktion ist fehlgeschlagen. "
                            "Bitte `/panelsetup` oder Bot neu starten.",
                        ),
                        ephemeral=True,
                    )
            except discord.HTTPException:
                pass

    async def on_ready(self) -> None:
        print(f"Eingeloggt als {self.user} (ID: {self.user.id})")  # type: ignore[union-attr]
        if config.GUILD_ID:
            guild = self.get_guild(config.GUILD_ID)
            if guild is None:
                print(
                    f"WARNUNG: GUILD_ID={config.GUILD_ID} — Bot ist nicht auf diesem Server."
                )
            else:
                print(f"Guild OK: {guild.name}")
                from integrations.catalog_sync import sync_shop_catalog
                from integrations.shop_api import shop_api

                if shop_api.enabled:
                    result = await sync_shop_catalog(self, config.GUILD_ID)
                    if result.get("error"):
                        print("[Shop API] Katalog-Sync beim Start fehlgeschlagen")
                    elif not result.get("skipped"):
                        print(
                            f"[Shop API] Katalog synchronisiert: "
                            f"{result.get('categories', 0)} Kategorien, "
                            f"{result.get('items', 0)} Produkte"
                        )
                from utils.panels import (
                    refresh_all_saved_buy_panels,
                    register_category_panel_views,
                    register_slot_panel_views,
                )

                register_slot_panel_views(self, force=True)
                await register_category_panel_views(self, force=True)
                for line in await refresh_all_saved_buy_panels(self, config.GUILD_ID):
                    print(f"[BuyPanel] {line}")
        else:
            from utils.panels import (
                refresh_all_saved_buy_panels,
                register_category_panel_views,
                register_slot_panel_views,
            )

            register_slot_panel_views(self, force=True)
            await register_category_panel_views(self, force=True)
            for line in await refresh_all_saved_buy_panels(self):
                print(f"[BuyPanel] {line}")
        print("Bot ist bereit.")


def main() -> None:
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN fehlt. Bitte .env aus .env.example erstellen.")
    bot = ShopBot()
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
