"""Archiv von URL laden (Scan-Panel /scan url)."""

from __future__ import annotations

import re
from email.message import Message
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from utils.archive_scanner import ARCHIVE_EXTS, MAX_ARCHIVE_BYTES, is_scannable_filename

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_DRIVE_FILE_RE = re.compile(
    r"drive\.google\.com/(?:file/d/|open\?id=)([a-zA-Z0-9_-]+)",
    re.I,
)
_DRIVE_ID_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]+)", re.I)


def normalize_download_url(url: str) -> str:
    """Share-Links in direkte Download-URLs umwandeln (Drive/Dropbox)."""
    raw = (url or "").strip().strip("<>").strip()
    if not raw:
        return raw

    # Google Drive
    m = _DRIVE_FILE_RE.search(raw) or _DRIVE_ID_RE.search(raw)
    if m and "drive.google.com" in raw:
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    # Dropbox: dl=0 → dl=1 / www → dl
    if "dropbox.com" in raw:
        raw = raw.replace("www.dropbox.com", "dl.dropboxusercontent.com")
        if "dl=0" in raw:
            raw = raw.replace("dl=0", "dl=1")
        elif "dl=" not in raw and "?" in raw:
            raw = raw + "&dl=1"
        elif "?" not in raw:
            raw = raw + "?dl=1"

    # MediaFire: oft schon direkter Link; sonst lassen
    return raw


def _filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    # email.message kann Content-Disposition parsen
    msg = Message()
    msg["content-disposition"] = header
    name = msg.get_filename()
    if name:
        return Path(unquote(name)).name
    m = re.search(
        r"filename\*=UTF-8''([^;]+)|filename=\"([^\"]+)\"|filename=([^;]+)",
        header,
        re.I,
    )
    if not m:
        return None
    raw = m.group(1) or m.group(2) or m.group(3) or ""
    return Path(unquote(raw.strip().strip('"'))).name or None


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    if name and name not in (".", "/"):
        return name
    # Query-Hints
    qs = parse_qs(parsed.query)
    for key in ("filename", "file", "name", "response-content-disposition"):
        if key in qs and qs[key]:
            cand = Path(unquote(qs[key][0])).name
            if cand:
                return cand
    return "download.bin"


def _ext_from_content_type(ct: str | None) -> str | None:
    if not ct:
        return None
    base = ct.split(";")[0].strip().lower()
    mapping = {
        "application/zip": ".zip",
        "application/x-zip-compressed": ".zip",
        "application/java-archive": ".jar",
        "application/x-rar-compressed": ".rar",
        "application/vnd.rar": ".rar",
        "application/octet-stream": None,
    }
    return mapping.get(base)


def ensure_scannable_name(filename: str, content_type: str | None = None) -> str:
    """Dateiname mit gültiger Archiv-Endung sicherstellen."""
    name = Path(filename or "download.bin").name
    if is_scannable_filename(name):
        return name
    ext = _ext_from_content_type(content_type)
    if ext:
        stem = name.rsplit(".", 1)[0] if "." in name else name
        return f"{stem}{ext}"
    # ZIP magic oft ohne Endung — als .zip behandeln
    if not Path(name).suffix:
        return f"{name}.zip"
    return name


async def download_archive_from_url(url: str) -> tuple[bytes, str]:
    """
    Lädt Archiv von http(s)-URL.
    Returns (bytes, filename). Raises ValueError bei Fehlern.
    """
    raw = normalize_download_url(url)
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Nur http(s)-Links erlaubt.")

    headers = {
        "User-Agent": _UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(90.0, connect=20.0),
            follow_redirects=True,
            headers=headers,
        ) as client:
            async with client.stream("GET", raw) as resp:
                # Google Drive Virus-Scan-Zwischenseite (HTML)
                ct = (resp.headers.get("content-type") or "").lower()
                if resp.status_code >= 400:
                    raise ValueError(
                        f"HTTP {resp.status_code} — Link ungültig oder blockiert."
                    )

                # Größe aus Header wenn vorhanden
                cl = resp.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > MAX_ARCHIVE_BYTES:
                    raise ValueError(
                        f"Datei zu groß (max. {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB)."
                    )

                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError(
                            f"Datei zu groß (max. {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB)."
                        )
                    chunks.append(chunk)
                data = b"".join(chunks)

                if not data:
                    raise ValueError("Leere Antwort — kein Download.")

                # HTML-Fehlerseite (Drive confirm etc.)
                if data[:20].lstrip().lower().startswith(
                    (b"<!doctype", b"<html", b"<head")
                ):
                    raise ValueError(
                        "Server lieferte HTML statt Datei. "
                        "Direkten Download-Link nutzen "
                        "(Discord-CDN, Dropbox `dl=1`, Drive „uc?export=download“)."
                    )

                fname = _filename_from_content_disposition(
                    resp.headers.get("content-disposition")
                ) or _filename_from_url(str(resp.url) if resp.url else raw)

                fname = ensure_scannable_name(
                    fname, resp.headers.get("content-type")
                )

                # ZIP/JAR magic PK\x03\x04 — wenn Endung fehlt/falsch
                if data[:2] == b"PK" and not is_scannable_filename(fname):
                    fname = ensure_scannable_name(
                        Path(fname).stem + ".zip", "application/zip"
                    )

                if not is_scannable_filename(fname):
                    # RAR magic Rar!
                    if data[:4] == b"Rar!":
                        fname = Path(fname).stem + ".rar"
                    else:
                        raise ValueError(
                            "Kein erkennbares Archiv (.zip / .rar / .jar). "
                            "URL muss auf eine Archiv-Datei zeigen."
                        )

                return data, fname
    except ValueError:
        raise
    except httpx.TimeoutException as e:
        raise ValueError("Timeout — Download dauerte zu lange.") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Download fehlgeschlagen: {e}") from e
