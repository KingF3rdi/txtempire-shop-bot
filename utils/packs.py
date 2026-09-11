from __future__ import annotations

import re
from pathlib import Path

import discord

from config import DATA_DIR

PACKS_DIR = DATA_DIR / "packs"
PACKS_DIR.mkdir(parents=True, exist_ok=True)

PREVIEWS_DIR = DATA_DIR / "previews"
PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

MAX_PACK_BYTES = 100 * 1024 * 1024
MAX_PREVIEW_BYTES = 25 * 1024 * 1024
PREVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".webm"}


def _safe_filename(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE)
    return name[:180] or "pack.bin"


async def save_pack_attachment(
    item_id: int,
    attachment: discord.Attachment,
    *,
    scan: bool = True,
) -> str:
    """Speichert Anhang unter data/packs/ und gibt relativen Pfad zurück.

    Bei ZIP/RAR/JAR/7Z (und einzelnen .exe/.dll) wird auf RAT-/Malware-Indikatoren gescannt.
    """
    if attachment.size and attachment.size > MAX_PACK_BYTES:
        raise ValueError(f"Datei zu groß (max. {MAX_PACK_BYTES // (1024 * 1024)} MB).")

    filename = _safe_filename(attachment.filename or "pack.bin")
    rel = f"packs/{item_id}_{filename}"
    dest = DATA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    await attachment.save(dest)

    if scan:
        from utils.archive_scanner import is_scannable_filename, scan_archive_path

        if is_scannable_filename(filename):
            result = scan_archive_path(dest)
            if result.is_blocked:
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ValueError(
                    "Pack abgelehnt — verdächtige Inhalte (RAT/Malware):\n"
                    + result.summary(limit=8)
                )
    return rel.replace("\\", "/")


def resolve_pack_path(pack_file: str | None) -> Path | None:
    if not pack_file:
        return None
    path = DATA_DIR / pack_file
    if path.is_file():
        return path
    return None


async def save_preview_attachment(item_id: int, attachment: discord.Attachment) -> str:
    """Speichert ein Vorschau-Bild/-Video unter data/previews/ und gibt den relativen Pfad zurück."""
    filename = _safe_filename(attachment.filename or "preview.bin")
    ext = Path(filename).suffix.lower()
    if ext not in PREVIEW_EXTENSIONS:
        raise ValueError(
            "Nur Bilder (png/jpg/gif/webp) oder Videos (mp4/mov/webm) erlaubt."
        )
    if attachment.size and attachment.size > MAX_PREVIEW_BYTES:
        raise ValueError(f"Datei zu groß (max. {MAX_PREVIEW_BYTES // (1024 * 1024)} MB).")

    rel = f"previews/{item_id}_{filename}"
    dest = DATA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    await attachment.save(dest)
    return rel.replace("\\", "/")


def resolve_preview_path(preview_file: str | None) -> Path | None:
    if not preview_file:
        return None
    path = DATA_DIR / preview_file
    if path.is_file():
        return path
    return None
