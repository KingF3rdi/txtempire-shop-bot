"""Klassifikation & Logging von Scan-Ergebnissen für /scan stats."""

from __future__ import annotations

from typing import TYPE_CHECKING

from utils.archive_scanner import Finding, ScanResult

if TYPE_CHECKING:
    from bot import ShopBot


def classify_findings(findings: list[Finding]) -> str:
    """Haupt-Kategorie für Treffer (eine pro Scan)."""
    if not findings:
        return "clean"
    blob = " ".join(f"{f.reason} {f.path}" for f in findings).lower()
    if any(
        x in blob
        for x in (
            "rat",
            "stealer",
            "grabber",
            "keylogger",
            "malware",
            "verdächtiger name",
        )
    ):
        return "malware"
    if "gefährliche dateiendung" in blob or "doppelte dateiendung" in blob:
        return "dangerous_ext"
    if "mz" in blob or "executable" in blob:
        return "disguised_exe"
    if "traversal" in blob or "pfad" in blob:
        return "path_issue"
    if "obfuscation" in blob or "langer pfad" in blob:
        return "obfuscation"
    return "other_suspicious"


def outcome_from_result(result: ScanResult) -> str:
    if result.error and not result.findings:
        return "error"
    if result.is_clean:
        return "clean"
    if result.is_blocked:
        return "blocked"
    return "suspicious"


async def log_scan_result(
    bot: ShopBot,
    guild_id: int,
    user_id: int,
    result: ScanResult,
) -> None:
    outcome = outcome_from_result(result)
    if outcome == "clean":
        category = "clean"
    elif outcome == "error":
        category = "error"
    else:
        category = classify_findings(result.findings)
    await bot.db.record_scan_result(
        guild_id,
        user_id,
        filename=result.filename,
        outcome=outcome,
        category=category,
        finding_count=len(result.findings),
    )
