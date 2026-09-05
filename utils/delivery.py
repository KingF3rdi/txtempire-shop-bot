from __future__ import annotations

import discord

from utils.packs import resolve_pack_path


async def deliver_packs(
    member: discord.Member,
    ticket_channel: discord.TextChannel,
    order_items: list[dict],
    bot=None,
) -> dict[str, list[str]]:
    """Sendet Pack-Inhalte per DM (Text/Datei) und/oder Link im Ticket."""
    dm_sent: list[str] = []
    links_posted: list[str] = []
    files_sent: list[str] = []
    dm_failed = False

    dm_parts: list[str] = []
    link_parts: list[str] = []
    pack_paths = []

    for item in order_items:
        name = item["name_snapshot"]
        qty = int(item["qty"])
        dm_text = (item.get("pack_dm_text") or "").strip()
        link = (item.get("pack_link") or "").strip()
        path = resolve_pack_path(item.get("pack_file"))

        if dm_text:
            dm_parts.append(f"**{name}** × {qty}\n{dm_text}")
            dm_sent.append(name)
        if link:
            link_parts.append(f"**{name}** × {qty}: {link}")
            links_posted.append(name)
        if path is not None:
            pack_paths.append(path)
            files_sent.append(name)

    header = "**Dein Kauf – Pack-Lieferung**"
    body = "\n\n".join(dm_parts) if dm_parts else (
        "Deine Pack-Datei(en) sind angehängt." if pack_paths else ""
    )

    async def send_to(
        dest: discord.abc.Messageable, *, mention: str | None = None
    ) -> None:
        text: str | None = header
        if mention:
            text = f"{mention}\n{text}"
        if body:
            text = f"{text}\n\n{body}"
        if pack_paths:
            for i in range(0, len(pack_paths), 10):
                chunk = [
                    discord.File(p, filename=p.name) for p in pack_paths[i : i + 10]
                ]
                await dest.send(content=text if i == 0 else None, files=chunk)
                text = None
        elif body:
            await dest.send(text)

    if dm_parts or pack_paths:
        try:
            await send_to(member)
            # Bewertung direkt nach Pack-DM (Sterne-Buttons)
            if bot is not None:
                from utils.vouch_request import VouchRatingView

                try:
                    embed = discord.Embed(
                        title="⭐ Bewertung",
                        description=(
                            "Wie war dein Pack? Tippe auf die Sterne und schreib "
                            "kurz dein Feedback — oder nutze `/vouch`."
                        ),
                        color=0x2B6CB0,
                    )
                    await member.send(embed=embed, view=VouchRatingView(bot))
                except discord.HTTPException:
                    pass
        except discord.HTTPException:
            dm_failed = True
            await ticket_channel.send(
                f"{member.mention} Pack-DM fehlgeschlagen (DMs geschlossen). "
                "Inhalt folgt hier:"
            )
            await send_to(ticket_channel, mention=member.mention)

    if link_parts:
        await ticket_channel.send(
            f"**Pack-Links für {member.mention}:**\n" + "\n".join(link_parts)
        )

    return {
        "dm_sent": dm_sent,
        "links_posted": links_posted,
        "files_sent": files_sent,
        "dm_failed": ["1"] if dm_failed else [],
    }
