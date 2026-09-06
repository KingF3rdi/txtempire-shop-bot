from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from enum import Enum

import httpx

# Nur klar bestätigte FREE-Treffer werden dem User als verfügbar gezeigt.
MAX_NAMES_PER_RUN = 50
MAX_LENGTH_NAMES = 50
MAX_LENGTH_CANDIDATES = MAX_LENGTH_NAMES  # Alias: intern = max. freie Names / Lauf
DEFAULT_DELAY = 0.25
FIND_BATCH_SIZE = 12
FIND_MAX_CHECKS = 400


class Status(str, Enum):
    AVAILABLE = "available"
    TAKEN = "taken"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CheckResult:
    platform: str
    username: str
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    emoji: str
    min_len: int
    max_len: int
    charset: str
    clean_charset: str = "abcdefghijklmnopqrstuvwxyz"


_MC_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
_RBX_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
_DC_RE = re.compile(r"^[a-z0-9_.]{2,32}$")

PLATFORMS: dict[str, PlatformSpec] = {
    "minecraft": PlatformSpec(
        "minecraft", "Minecraft", "🟩", 3, 16, "abcdefghijklmnopqrstuvwxyz0123456789_"
    ),
    "roblox": PlatformSpec(
        "roblox", "Roblox", "🟥", 3, 20, "abcdefghijklmnopqrstuvwxyz0123456789_"
    ),
    "discord": PlatformSpec(
        "discord", "Discord", "🟦", 2, 32, "abcdefghijklmnopqrstuvwxyz0123456789_."
    ),
}


def validate_local(platform: str, username: str) -> str | None:
    if platform == "minecraft":
        if not _MC_RE.fullmatch(username):
            return "Minecraft: 3–16 Zeichen, nur A–Z, 0–9, _"
        return None
    if platform == "roblox":
        if not _RBX_RE.fullmatch(username):
            return "Roblox: 3–20 Zeichen, nur A–Z, 0–9, _"
        if username.startswith("_") or username.endswith("_"):
            return "Roblox: darf nicht mit _ beginnen/enden"
        if "__" in username:
            return "Roblox: keine doppelten Unterstriche"
        return None
    if platform == "discord":
        name = username.lower()
        if username != name:
            return "Discord: nur Kleinbuchstaben"
        if not _DC_RE.fullmatch(name):
            return "Discord: 2–32 Zeichen, a–z, 0–9, _ ."
        if name.startswith(".") or name.endswith("."):
            return "Discord: darf nicht mit . beginnen/enden"
        if ".." in name:
            return "Discord: keine doppelten Punkte"
        return None
    return "Unbekannte Plattform"


def generate_candidates(
    platform: str,
    length: int,
    *,
    count: int,
    prefix: str = "",
    suffix: str = "",
    exclude: set[str] | None = None,
    hard_cap: int | None = None,
    clean: bool = False,
) -> list[str]:
    """
    clean=True: nur reine Buchstaben-Kerne (keine Ziffern/Unterstriche/Punkte) —
    "clean" Usernames statt zufälligem Zeichen-Mix.
    """
    spec = PLATFORMS[platform]
    cap = hard_cap if hard_cap is not None else MAX_LENGTH_NAMES
    count = max(1, min(count, cap))
    if not (spec.min_len <= length <= spec.max_len):
        raise ValueError(f"Länge für {spec.label}: {spec.min_len}–{spec.max_len}")
    core_len = length - len(prefix) - len(suffix)
    if core_len < 0:
        raise ValueError("Prefix+Suffix länger als gewünschte Länge")
    charset = spec.clean_charset if clean else spec.charset
    if core_len == 0:
        name = f"{prefix}{suffix}"
        if platform == "discord":
            name = name.lower()
        if exclude and name in exclude:
            return []
        return [name] if validate_local(platform, name) is None else []

    out: list[str] = []
    seen: set[str] = set(exclude or ())
    # Roblox: Prefix/Suffix dürfen die _-Regeln nicht brechen — Validate filtert später
    while len(out) < count:
        core = "".join(random.choice(charset) for _ in range(core_len))
        name = f"{prefix}{core}{suffix}"
        if platform == "discord":
            name = name.lower()
        if name in seen:
            continue
        seen.add(name)
        if validate_local(platform, name) is None:
            out.append(name)
        if len(seen) > count * 40 + len(exclude or ()):
            break
    return out


async def check_minecraft(client: httpx.AsyncClient, username: str) -> CheckResult:
    err = validate_local("minecraft", username)
    if err:
        return CheckResult("minecraft", username, Status.INVALID, err)
    try:
        r = await client.get(
            f"https://api.mojang.com/users/profiles/minecraft/{username}"
        )
    except httpx.HTTPError as e:
        return CheckResult("minecraft", username, Status.UNKNOWN, f"Netzwerk: {e}")

    if r.status_code == 200 and r.content:
        return CheckResult("minecraft", username, Status.TAKEN, "Profil existiert")
    if r.status_code in (204, 404):
        return CheckResult("minecraft", username, Status.AVAILABLE, "Mojang: frei")
    if r.status_code == 429:
        return CheckResult("minecraft", username, Status.UNKNOWN, "Rate-Limit")
    return CheckResult(
        "minecraft", username, Status.UNKNOWN, f"HTTP {r.status_code}"
    )


async def check_roblox(client: httpx.AsyncClient, username: str) -> CheckResult:
    err = validate_local("roblox", username)
    if err:
        return CheckResult("roblox", username, Status.INVALID, err)
    try:
        r = await client.get(
            "https://auth.roblox.com/v1/usernames/validate",
            params={
                "username": username,
                "birthday": "2000-01-01T00:00:00.000Z",
                "context": "Signup",
            },
        )
    except httpx.HTTPError as e:
        return CheckResult("roblox", username, Status.UNKNOWN, f"Netzwerk: {e}")

    if r.status_code == 429:
        return CheckResult("roblox", username, Status.UNKNOWN, "Rate-Limit")
    if r.status_code != 200:
        return CheckResult("roblox", username, Status.UNKNOWN, f"HTTP {r.status_code}")
    try:
        data = r.json()
    except Exception:
        return CheckResult("roblox", username, Status.UNKNOWN, "Ungültiges JSON")

    code = data.get("code")
    msg = str(data.get("message") or "")
    if code == 0:
        return CheckResult("roblox", username, Status.AVAILABLE, msg or "frei")
    if code in (1, 2, 3, 4, 5, 6, 7, 10):
        return CheckResult("roblox", username, Status.TAKEN, msg or f"code={code}")
    return CheckResult(
        "roblox", username, Status.UNKNOWN, f"code={code} {msg}".strip()
    )


async def check_discord_name(client: httpx.AsyncClient, username: str) -> CheckResult:
    err = validate_local("discord", username)
    if err:
        return CheckResult("discord", username, Status.INVALID, err)
    name = username.lower()
    try:
        r = await client.post(
            "https://discord.com/api/v9/unique-username/username-attempt-unauthed",
            json={"username": name},
            headers={
                "Content-Type": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            },
        )
    except httpx.HTTPError as e:
        return CheckResult("discord", name, Status.UNKNOWN, f"Netzwerk: {e}")

    if r.status_code == 429:
        return CheckResult("discord", name, Status.UNKNOWN, "Rate-Limit")
    if r.status_code in (401, 403):
        return CheckResult(
            "discord",
            name,
            Status.UNKNOWN,
            f"Blockiert HTTP {r.status_code}",
        )
    if r.status_code != 200:
        return CheckResult("discord", name, Status.UNKNOWN, f"HTTP {r.status_code}")
    try:
        data = r.json()
    except Exception:
        return CheckResult("discord", name, Status.UNKNOWN, "Ungültiges JSON")

    if "taken" not in data:
        return CheckResult("discord", name, Status.UNKNOWN, "Kein taken-Feld")
    if data["taken"] is False:
        return CheckResult("discord", name, Status.AVAILABLE, "taken=false")
    if data["taken"] is True:
        return CheckResult("discord", name, Status.TAKEN, "taken=true")
    return CheckResult("discord", name, Status.UNKNOWN, f"taken={data['taken']!r}")


async def check_one(
    client: httpx.AsyncClient, platform: str, username: str
) -> CheckResult:
    if platform == "minecraft":
        return await check_minecraft(client, username)
    if platform == "roblox":
        return await check_roblox(client, username)
    if platform == "discord":
        return await check_discord_name(client, username)
    return CheckResult(platform, username, Status.INVALID, "Unbekannte Plattform")


def _clean_names(platform: str, names: list[str], *, limit: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        n = raw.strip()
        if platform == "discord":
            n = n.lower()
        if not n or n in seen:
            continue
        seen.add(n)
        cleaned.append(n)
        if len(cleaned) >= limit:
            break
    return cleaned


async def check_many(
    platform: str,
    names: list[str],
    *,
    delay: float = DEFAULT_DELAY,
    limit: int | None = None,
) -> list[CheckResult]:
    """Prüft Namen sequentiell. UNKNOWN wird nie als verfügbar gewertet."""
    cleaned = _clean_names(platform, names, limit=limit or MAX_NAMES_PER_RUN)
    results: list[CheckResult] = []
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=20.0,
        headers={
            "User-Agent": "TxtEmpireUsernameSniper/1.0",
            "Accept": "application/json",
        },
    ) as client:
        for i, name in enumerate(cleaned):
            results.append(await check_one(client, platform, name))
            if i + 1 < len(cleaned) and delay > 0:
                await asyncio.sleep(delay)
    return results


async def find_available_names(
    platform: str,
    length: int,
    want: int,
    *,
    prefix: str = "",
    suffix: str = "",
    delay: float = DEFAULT_DELAY,
    max_checks: int | None = None,
    clean: bool = False,
) -> tuple[list[CheckResult], int]:
    """
    Sucht so lange, bis `want` bestätigte freie Names gefunden sind
    oder max_checks erreicht ist.
    Returns (available_results, checks_done).
    """
    want = max(1, min(int(want), MAX_LENGTH_NAMES))
    cap = max_checks if max_checks is not None else min(
        FIND_MAX_CHECKS, max(want * 25, 80)
    )
    tried: set[str] = set()
    free: list[CheckResult] = []
    checks = 0

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=20.0,
        headers={
            "User-Agent": "TxtEmpireUsernameSniper/1.0",
            "Accept": "application/json",
        },
    ) as client:
        while len(free) < want and checks < cap:
            batch_n = min(FIND_BATCH_SIZE, cap - checks, (want - len(free)) * 8)
            batch = generate_candidates(
                platform,
                length,
                count=batch_n,
                prefix=prefix,
                suffix=suffix,
                exclude=tried,
                hard_cap=FIND_MAX_CHECKS,
                clean=clean,
            )
            if not batch:
                break
            for i, name in enumerate(batch):
                tried.add(name)
                result = await check_one(client, platform, name)
                checks += 1
                if result.status == Status.AVAILABLE:
                    free.append(result)
                    if len(free) >= want:
                        break
                if i + 1 < len(batch) and delay > 0:
                    await asyncio.sleep(delay)
                if checks >= cap:
                    break
    return free[:want], checks


def only_available(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.status == Status.AVAILABLE]


def format_results_embed_body(
    platform: str,
    results: list[CheckResult],
    *,
    show_all: bool = False,
) -> tuple[str, list[str]]:
    """
    Returns (description, available_names).
    Standard: nur bestätigte FREE Namen prominent.
    """
    spec = PLATFORMS[platform]
    free = only_available(results)
    taken = sum(1 for r in results if r.status == Status.TAKEN)
    invalid = sum(1 for r in results if r.status == Status.INVALID)
    unknown = sum(1 for r in results if r.status == Status.UNKNOWN)

    lines = [
        f"{spec.emoji} **{spec.label}** — {len(results)} geprüft",
        f"✅ Frei: **{len(free)}** · ❌ Vergeben: {taken} · "
        f"⚠ Ungültig: {invalid} · ❔ Unklar: {unknown}",
        "",
    ]
    if free:
        lines.append("**Bestätigt verfügbar:**")
        for r in free:
            lines.append(f"✅ `{r.username}`")
    else:
        lines.append("_Keine bestätigten freien Names._")

    if show_all and (taken or invalid or unknown):
        lines.append("")
        lines.append("**Andere:**")
        for r in results:
            if r.status == Status.AVAILABLE:
                continue
            icon = {
                Status.TAKEN: "❌",
                Status.INVALID: "⚠",
                Status.UNKNOWN: "❔",
            }.get(r.status, "·")
            lines.append(f"{icon} `{r.username}` — {r.detail[:60]}")

    lines.append("")
    lines.append(
        "_Nur API-bestätigte Treffer gelten als frei. "
        "Unklar/Rate-Limit = nicht verfügbar._"
    )
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3890] + "\n_…gekürzt_"
    return text, [r.username for r in free]
