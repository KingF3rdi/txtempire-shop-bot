from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

import discord

from utils.embeds import base_embed, error_embed, success_embed, warn_embed
from views.ticket_views import is_staff

if TYPE_CHECKING:
    from bot import ShopBot

PanelType = Literal["support", "application", "partner", "texturepack"]
TicketType = Literal[
    "support",
    "application",
    "partner",
    "texture_ankauf",
    "texture_tausch",
]

_SERVICE_PANEL_TYPES = frozenset({"support", "application", "partner", "texturepack"})


def _panel_title(panel_type: str) -> str:
    return {
        "support": "Support",
        "application": "Media-Creator",
        "partner": "Partner",
        "texturepack": "Texturepack",
        "texture_ankauf": "Texturepack Ankauf",
        "texture_tausch": "Texturepack Tausch",
    }.get(panel_type, panel_type)


def build_service_panel_embed(
    panel_type: PanelType, *, role_mention: str | None = None
) -> discord.Embed:
    if panel_type == "support":
        return base_embed(
            "🛟 Support",
            "Brauchst du Hilfe (Kauf, Credits, Scanner, Zugang)?\n\n"
            "Klicke **Support öffnen** — es wird ein privates Ticket erstellt.\n"
            "Beschreibe dein Anliegen möglichst klar.",
        )
    if panel_type == "partner":
        return base_embed(
            "🤝 Discord Partner",
            "Du möchtest eine **Server-Partnerschaft** mit uns?\n\n"
            "Klicke **Partnerschaft anfragen** und gib Invite, Member-Zahl "
            "und euer Angebot an.\n"
            "Wir melden uns im Ticket.",
        )
    if panel_type == "texturepack":
        role_line = (
            f"\n**Nur mit Rolle {role_mention}** zugänglich.\n"
            if role_mention
            else "\n⚠️ Exklusiv-Rolle noch nicht gesetzt (`/texturepackrole`).\n"
        )
        return base_embed(
            "📦 Texturepack Ankauf & Tausch",
            "Du willst Texturepacks **an den Server verkaufen** oder **tauschen**?\n\n"
            "• **Ankauf** — Verkauf an den Server (Preis-Vorschlag)\n"
            "• **Tausch** — Gegen Texturepacks / Guthaben / anderes\n"
            f"{role_line}\n"
            "Im Formular: Pack-Namen, Anzahl, vorgestellter Preis/Tausch.",
        )
    return base_embed(
        "🎬 Media / Creator Bewerbung",
        "Du erstellst Content und möchtest **Media / Creator** bei uns werden?\n\n"
        "Klicke **Als Creator bewerben** und fülle das Formular aus "
        "(Plattform, Links, Reichweite, Angebot).\n"
        "Wir melden uns im Ticket.",
    )


async def _texturepack_role_gate(
    bot: ShopBot, interaction: discord.Interaction
) -> bool:
    """True = darf fortfahren. Antwortet ephemeral bei Ablehnung."""
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=error_embed("Nur auf dem Server"), ephemeral=True
        )
        return False
    if await is_staff(bot, interaction):
        return True
    settings = await bot.db.ensure_guild(interaction.guild.id)
    role_id = settings.get("texturepack_role_id")
    if not role_id:
        await interaction.response.send_message(
            embed=error_embed(
                "Nicht konfiguriert",
                "Die Exklusiv-Rolle für Texturepack ist noch nicht gesetzt. "
                "Staff: `/texturepackrole`.",
            ),
            ephemeral=True,
        )
        return False
    role = interaction.guild.get_role(int(role_id))
    member = interaction.user
    if not isinstance(member, discord.Member) or role is None or role not in member.roles:
        mention = role.mention if role else f"`{role_id}`"
        await interaction.response.send_message(
            embed=error_embed(
                "Keine Berechtigung",
                f"Nur Mitglieder mit der Rolle {mention} können "
                "Texturepack Ankauf/Tausch öffnen.",
            ),
            ephemeral=True,
        )
        return False
    return True


class ServiceCloseView(discord.ui.View):
    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Ticket schließen",
        style=discord.ButtonStyle.danger,
        custom_id="serviceticket:close",
        emoji="🔒",
    )
    async def close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None or not isinstance(
            interaction.channel, discord.TextChannel
        ):
            await interaction.response.send_message(
                embed=error_embed("Nur im Ticket"), ephemeral=True
            )
            return
        ticket = await self.bot.db.get_service_ticket_by_channel(
            interaction.channel.id
        )
        if not ticket:
            await interaction.response.send_message(
                embed=error_embed("Kein Service-Ticket"), ephemeral=True
            )
            return
        staff = await is_staff(self.bot, interaction)
        is_owner = interaction.user.id == int(ticket["user_id"])
        if not staff and not is_owner:
            await interaction.response.send_message(
                embed=error_embed("Keine Berechtigung"), ephemeral=True
            )
            return
        await interaction.response.defer()
        await self.bot.db.update_service_ticket(
            int(ticket["id"]), status="closed"
        )
        await interaction.followup.send(
            embed=warn_embed(
                "Ticket wird geschlossen",
                f"Geschlossen von {interaction.user.mention}. "
                "Channel wird in 5 Sekunden gelöscht.",
            )
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(
                reason=f"Service-Ticket geschlossen von {interaction.user}"
            )
        except discord.HTTPException:
            pass


class ApplicationModal(discord.ui.Modal, title="Media / Creator Bewerbung"):
    platforms = discord.ui.TextInput(
        label="Plattform(en) & Name",
        placeholder="z.B. YouTube: TxTClips · TikTok: @txtempire",
        max_length=150,
        required=True,
    )
    links = discord.ui.TextInput(
        label="Links (Kanal / Socials)",
        style=discord.TextStyle.paragraph,
        placeholder="https://youtube.com/…\nhttps://tiktok.com/@…",
        max_length=500,
        required=True,
    )
    reach = discord.ui.TextInput(
        label="Reichweite / Stats",
        placeholder="z.B. 5k Follower, Ø 2k Views, Upload 3×/Woche",
        max_length=200,
        required=True,
    )
    content = discord.ui.TextInput(
        label="Content-Art",
        placeholder="z.B. Minecraft Clips, Shorts, Streams, Edits…",
        max_length=200,
        required=True,
    )
    offer = discord.ui.TextInput(
        label="Was bietest du an?",
        style=discord.TextStyle.paragraph,
        placeholder="Promo, Videos, Codes, Community… Warum Media/Creator?",
        max_length=800,
        required=True,
    )

    def __init__(self, bot: ShopBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        body = (
            f"**Plattform(en):** {self.platforms.value}\n\n"
            f"**Links:**\n{self.links.value}\n\n"
            f"**Reichweite:** {self.reach.value}\n\n"
            f"**Content:** {self.content.value}\n\n"
            f"**Angebot / Motivation:**\n{self.offer.value}"
        )
        await create_service_ticket_channel(
            self.bot,
            interaction,
            ticket_type="application",
            subject=body,
        )


class SupportPanelView(discord.ui.View):
    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Support öffnen",
        style=discord.ButtonStyle.primary,
        custom_id="servicepanel:support",
        emoji="🛟",
    )
    async def open_support(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await create_service_ticket_channel(
            self.bot, interaction, ticket_type="support"
        )


class ApplicationPanelView(discord.ui.View):
    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Als Creator bewerben",
        style=discord.ButtonStyle.success,
        custom_id="servicepanel:application",
        emoji="🎬",
    )
    async def open_application(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        await interaction.response.send_modal(ApplicationModal(self.bot))


class PartnerModal(discord.ui.Modal, title="Discord Partnerschaft"):
    server_name = discord.ui.TextInput(
        label="Server-Name",
        placeholder="Name eures Discord-Servers",
        max_length=100,
        required=True,
    )
    invite = discord.ui.TextInput(
        label="Invite-Link",
        placeholder="https://discord.gg/…",
        max_length=200,
        required=True,
    )
    members = discord.ui.TextInput(
        label="Ungefähre Member-Zahl",
        placeholder="z.B. 2500",
        max_length=40,
        required=True,
    )
    offer = discord.ui.TextInput(
        label="Was bietet ihr an?",
        style=discord.TextStyle.paragraph,
        placeholder="Shoutouts, Events, Cross-Promo, …",
        max_length=800,
        required=True,
    )
    expect = discord.ui.TextInput(
        label="Was erwartet ihr von uns?",
        style=discord.TextStyle.paragraph,
        placeholder="Kurz eure Erwartungen…",
        max_length=800,
        required=True,
    )

    def __init__(self, bot: ShopBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        body = (
            f"**Server:** {self.server_name.value}\n"
            f"**Invite:** {self.invite.value}\n"
            f"**Member:** {self.members.value}\n\n"
            f"**Angebot:**\n{self.offer.value}\n\n"
            f"**Erwartung:**\n{self.expect.value}"
        )
        await create_service_ticket_channel(
            self.bot,
            interaction,
            ticket_type="partner",
            subject=body,
        )


class PartnerPanelView(discord.ui.View):
    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Partnerschaft anfragen",
        style=discord.ButtonStyle.primary,
        custom_id="servicepanel:partner",
        emoji="🤝",
    )
    async def open_partner(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
            return
        await interaction.response.send_modal(PartnerModal(self.bot))


class TexturepackModal(discord.ui.Modal):
    pack_names = discord.ui.TextInput(
        label="Name der Packs",
        style=discord.TextStyle.paragraph,
        placeholder="z.B. Faithful 32x, PvP Pack XYZ…",
        max_length=500,
        required=True,
    )
    quantity = discord.ui.TextInput(
        label="Anzahl",
        placeholder="z.B. 3",
        max_length=40,
        required=True,
    )
    offer = discord.ui.TextInput(
        label="Vorgestellter Preis / Tausch",
        style=discord.TextStyle.paragraph,
        placeholder="z.B. 800k Guthaben · oder Tausch gegen …",
        max_length=500,
        required=True,
    )

    def __init__(self, bot: ShopBot, *, kind: Literal["ankauf", "tausch"]) -> None:
        title = (
            "Texturepack Ankauf"
            if kind == "ankauf"
            else "Texturepack Tausch"
        )
        super().__init__(title=title)
        self.bot = bot
        self.kind = kind
        if kind == "ankauf":
            self.offer.label = "Vorgestellter Ankaufspreis"
            self.offer.placeholder = "z.B. 500k Guthaben / 5 Credits"
        else:
            self.offer.label = "Vorgestellter Tausch"
            self.offer.placeholder = "z.B. gegen Pack X · oder 300k + Pack Y"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _texturepack_role_gate(self.bot, interaction):
            return
        kind_label = "Ankauf (Verkauf an Server)" if self.kind == "ankauf" else "Tausch"
        body = (
            f"**Art:** {kind_label}\n"
            f"**Pack-Namen:**\n{self.pack_names.value}\n\n"
            f"**Anzahl:** {self.quantity.value}\n\n"
            f"**Vorschlag:**\n{self.offer.value}"
        )
        ticket_type: TicketType = (
            "texture_ankauf" if self.kind == "ankauf" else "texture_tausch"
        )
        await create_service_ticket_channel(
            self.bot,
            interaction,
            ticket_type=ticket_type,
            subject=body,
        )


class TexturepackPanelView(discord.ui.View):
    def __init__(self, bot: ShopBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Ankauf",
        style=discord.ButtonStyle.success,
        custom_id="servicepanel:texture_ankauf",
        emoji="💰",
        row=0,
    )
    async def open_ankauf(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await _texturepack_role_gate(self.bot, interaction):
            return
        await interaction.response.send_modal(
            TexturepackModal(self.bot, kind="ankauf")
        )

    @discord.ui.button(
        label="Tausch",
        style=discord.ButtonStyle.primary,
        custom_id="servicepanel:texture_tausch",
        emoji="🔄",
        row=0,
    )
    async def open_tausch(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await _texturepack_role_gate(self.bot, interaction):
            return
        await interaction.response.send_modal(
            TexturepackModal(self.bot, kind="tausch")
        )


async def create_service_ticket_channel(
    bot: ShopBot,
    interaction: discord.Interaction,
    *,
    ticket_type: str,
    subject: str = "",
) -> None:
    if interaction.guild is None:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error_embed("Nur auf dem Server"), ephemeral=True
            )
        return

    guild = interaction.guild
    open_n = await bot.db.count_open_service_tickets(
        guild.id, interaction.user.id, ticket_type
    )
    if open_n >= 1:
        msg = error_embed(
            "Bereits offen",
            f"Du hast schon ein offenes **{_panel_title(ticket_type)}**-Ticket.",
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    settings = await bot.db.ensure_guild(guild.id)
    category_id = settings.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None

    staff_role_id = settings.get("staff_role_id")
    staff_role = guild.get_role(int(staff_role_id)) if staff_role_id else None
    me = guild.me
    if me is None:
        await interaction.followup.send(
            embed=error_embed("Bot-Mitgliedschaft fehlt"), ephemeral=True
        )
        return

    bot_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        manage_channels=True,
        manage_messages=True,
    )
    user_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        attach_files=True,
        embed_links=True,
        read_message_history=True,
    )
    staff_perms = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        attach_files=True,
        embed_links=True,
        read_message_history=True,
        manage_messages=True,
    )
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: bot_perms,
    }
    if isinstance(interaction.user, discord.Member):
        overwrites[interaction.user] = user_perms
    if staff_role:
        overwrites[staff_role] = staff_perms

    ticket_id = await bot.db.create_service_ticket(
        guild.id,
        interaction.user.id,
        ticket_type,
        subject=subject[:500],
    )
    prefix = {
        "support": "support",
        "application": "creator",
        "partner": "partner",
        "texture_ankauf": "tp-ankauf",
        "texture_tausch": "tp-tausch",
    }.get(ticket_type, "ticket")
    safe = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in interaction.user.name.lower()
    )[:18]
    name = f"{prefix}-{ticket_id:04d}-{safe}"[:100]

    try:
        channel = await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason=f"{_panel_title(ticket_type)} von {interaction.user}",
        )
    except discord.HTTPException as e:
        await bot.db.update_service_ticket(ticket_id, status="closed")
        await interaction.followup.send(
            embed=error_embed("Channel fehlgeschlagen", str(e)[:400]),
            ephemeral=True,
        )
        return

    await bot.db.update_service_ticket(ticket_id, channel_id=channel.id)

    title = _panel_title(ticket_type)
    intro = {
        "support": "Beschreibe dein Anliegen — Staff meldet sich.",
        "application": "Media-/Creator-Bewerbung unten. Staff prüft sie.",
        "partner": "Partnerschafts-Anfrage unten. Staff meldet sich.",
        "texture_ankauf": "Ankauf-Anfrage unten. Staff prüft Preis & Packs.",
        "texture_tausch": "Tausch-Anfrage unten. Staff prüft den Vorschlag.",
    }.get(ticket_type, "Staff meldet sich.")
    embed = success_embed(
        f"{title}-Ticket #{ticket_id}",
        f"Erstellt von {interaction.user.mention}.\n{intro}",
    )
    mention = staff_role.mention if staff_role else "Staff"
    content = f"{mention} · {interaction.user.mention}"
    await channel.send(content=content, embed=embed, view=ServiceCloseView(bot))
    if subject:
        label = {
            "application": "Media/Creator Angaben",
            "partner": "Partner-Angaben",
            "texture_ankauf": "Ankauf-Angaben",
            "texture_tausch": "Tausch-Angaben",
        }.get(ticket_type, "Angaben")
        await channel.send(embed=base_embed(label, subject[:3900]))

    await interaction.followup.send(
        embed=success_embed(
            f"{title} geöffnet",
            f"Dein Ticket: {channel.mention}",
        ),
        ephemeral=True,
    )


def _view_for(bot: ShopBot, panel_type: PanelType) -> discord.ui.View:
    if panel_type == "support":
        return SupportPanelView(bot)
    if panel_type == "partner":
        return PartnerPanelView(bot)
    if panel_type == "texturepack":
        return TexturepackPanelView(bot)
    return ApplicationPanelView(bot)


async def _role_mention_for_texturepack(
    bot: ShopBot, guild: discord.Guild
) -> str | None:
    settings = await bot.db.ensure_guild(guild.id)
    role_id = settings.get("texturepack_role_id")
    if not role_id:
        return None
    role = guild.get_role(int(role_id))
    return role.mention if role else f"`{role_id}`"


async def post_or_refresh_service_panel(
    bot: ShopBot,
    guild: discord.Guild,
    channel: discord.TextChannel,
    panel_type: PanelType,
    *,
    force_new: bool = False,
) -> discord.Message:
    role_mention = None
    if panel_type == "texturepack":
        role_mention = await _role_mention_for_texturepack(bot, guild)
    embed = build_service_panel_embed(panel_type, role_mention=role_mention)
    view = _view_for(bot, panel_type)
    row = await bot.db.get_service_panel(guild.id, panel_type)
    if (
        not force_new
        and row
        and row.get("channel_id")
        and row.get("message_id")
        and int(row["channel_id"]) == channel.id
    ):
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            await msg.edit(embed=embed, view=view)
            return msg
        except (discord.NotFound, discord.HTTPException):
            pass
    msg = await channel.send(embed=embed, view=view)
    await bot.db.set_service_panel(
        guild.id, panel_type, channel_id=channel.id, message_id=msg.id
    )
    return msg


async def refresh_service_panels_on_ready(bot: ShopBot) -> list[str]:
    lines: list[str] = []
    for row in await bot.db.list_service_panels():
        guild = bot.get_guild(int(row["guild_id"]))
        if guild is None:
            continue
        panel_type = str(row["panel_type"])
        if panel_type not in _SERVICE_PANEL_TYPES:
            continue
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            role_mention = None
            if panel_type == "texturepack":
                role_mention = await _role_mention_for_texturepack(bot, guild)
            await msg.edit(
                embed=build_service_panel_embed(
                    panel_type, role_mention=role_mention  # type: ignore[arg-type]
                ),
                view=_view_for(bot, panel_type),  # type: ignore[arg-type]
            )
            lines.append(f"{panel_type}-Panel aktualisiert in {channel.mention}")
        except discord.NotFound:
            lines.append(f"{panel_type}-Panel Nachricht fehlt — kein Auto-Post")
        except discord.HTTPException as e:
            lines.append(f"{panel_type}-Panel Edit fehlgeschlagen: {e}")
    return lines
