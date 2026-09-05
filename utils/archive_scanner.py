from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# Bekannte RAT / Stealer / Loader Namen (Dateinamen & Pfade)
_MALWARE_NAME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"asyncrat",
        r"quasar(\s)?rat",
        r"\bnjrat\b",
        r"remcos",
        r"darkcomet",
        r"nanocore",
        r"revengerat",
        r"xworm",
        r"venomrat",
        r"orcus",
        r"warzone(\s)?rat",
        r"spynote",
        r"lime\s?rat",
        r"imminent(\s)?monitor",
        r"blacknet",
        r"stormkitty",
        r"redline",
        r"raccoon(\s)?stealer",
        r"\bvidar\b",
        r"lumma(stealer)?",
        r"risepro",
        r"stealc",
        r"metastealer",
        r"mars(\s)?stealer",
        r"aurora(\s)?stealer",
        r"blank(\s)?grabber",
        r"empyrean",
        r"creal(\s)?stealer",
        r"phoenix(\s)?stealer",
        r"atomic(\s)?stealer",
        r"meduza(\s)?stealer",
        r"snake(\s)?keylogger",
        r"agent\s?tesla",
        r"formbook",
        r"lokibot",
        r"ave.?maria",
        r"guloader",
        r"smoke\s?loader",
        r"privateloader",
        r"amadie",
        r"dc.?rat",
        r"bitrat",
        r"netwire",
        r"poison\s?ivy",
        r"cypher\s?rat",
        r"pulsar(\s)?rat",
        r"parallax(\s)?rat",
        r"extreme(\s)?rat",
        r"\brat\b.*\.(exe|dll|jar|scr)",
        r"(keylogger|clipper|stealer|grabber|hvnc).*\.(exe|dll|jar|scr|bat|ps1)",
        r"(hack|crack|inject).*\.(exe|dll|bat|ps1|vbs)",
    )
)

# Verdächtige Endungen in Minecraft-Packs / Client-Zips
_DANGEROUS_EXTS = {
    ".exe",
    ".dll",
    ".scr",
    ".com",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".js",
    ".jse",
    ".wsf",
    ".hta",
    ".msi",
    ".msp",
    ".lnk",
    ".pif",
    ".reg",
    ".iso",
    ".img",
    ".apk",
}

# Doppelte Erweiterungen (z.B. texture.png.exe)
_DOUBLE_EXT_RE = re.compile(
    r"\.(png|jpg|jpeg|gif|webp|txt|json|mcmeta|ogg|zip|rar|jar)\.(exe|dll|scr|bat|cmd|ps1|vbs|js)$",
    re.IGNORECASE,
)

_SUSPICIOUS_PATH_RE = re.compile(
    r"(appdata|startup|system32|syswow64|windows[/\\]temp|programdata|"
    r"autorun|persistence|inject|payload|shellcode)",
    re.IGNORECASE,
)

# Strings in kleinen Textdateien / Configs
_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"asyncrat",
        r"quasarrat",
        r"telegram\.me/bot",
        r"discord\.com/api/webhooks",
        r"webhook\.site",
        r"pastebin\.com/raw",
        r"HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
        r"Add-MpPreference\s+-ExclusionPath",
        r"amsiInitFailed",
        r"System\.Reflection\.Assembly\.Load",
        r"FromBase64String",
        r"DownloadString\s*\(",
        r"Invoke-Expression|IEX\s*\(",
        r"keylog",
        r"clipper",
        r"steal(er|ing)?.*(cookie|token|password|wallet)",
    )
)

ARCHIVE_EXTS = {".zip", ".rar", ".jar", ".apk"}  # jar/apk = zip-basiert
MAX_ENTRIES = 8000
MAX_NAME_LEN = 512
MAX_CONTENT_PEEK = 64 * 1024  # 64 KB Text-Peek pro Datei
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024


@dataclass
class Finding:
    severity: str  # critical | high | medium
    path: str
    reason: str


@dataclass
class ScanResult:
    filename: str
    archive_type: str
    findings: list[Finding] = field(default_factory=list)
    entry_count: int = 0
    error: str | None = None

    @property
    def is_clean(self) -> bool:
        return self.error is None and not self.findings

    @property
    def is_blocked(self) -> bool:
        return any(f.severity in ("critical", "high") for f in self.findings)

    def summary(self, *, limit: int = 15) -> str:
        if self.error:
            return f"Scan-Fehler: {self.error}"
        if not self.findings:
            return f"✅ Sauber — {self.entry_count} Einträge in `{self.filename}` ({self.archive_type})"
        lines = [
            f"⚠️ **{len(self.findings)} Treffer** in `{self.filename}` "
            f"({self.archive_type}, {self.entry_count} Einträge):"
        ]
        for f in self.findings[:limit]:
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(
                f.severity, "⚪"
            )
            lines.append(f"{icon} `{f.path[:80]}` — {f.reason}")
        if len(self.findings) > limit:
            lines.append(f"_…und {len(self.findings) - limit} weitere_")
        return "\n".join(lines)


def _check_entry_name(name: str, findings: list[Finding]) -> None:
    raw = name.replace("\\", "/")
    lower = raw.lower()
    base = Path(lower).name

    if len(raw) > MAX_NAME_LEN:
        findings.append(
            Finding("medium", raw[:80] + "…", "Extrem langer Pfad (Obfuscation?)")
        )

    if ".." in Path(raw).parts or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        findings.append(Finding("high", raw, "Pfad-Traversal / absoluter Pfad"))

    if _DOUBLE_EXT_RE.search(base):
        findings.append(Finding("critical", raw, "Doppelte Dateiendung (Tarnung)"))

    ext = Path(base).suffix
    if ext in _DANGEROUS_EXTS:
        findings.append(
            Finding("critical", raw, f"Gefährliche Dateiendung ({ext})")
        )

    for pat in _MALWARE_NAME_PATTERNS:
        if pat.search(raw):
            findings.append(
                Finding("critical", raw, f"Verdächtiger Name (Muster: {pat.pattern[:40]})")
            )
            break

    if _SUSPICIOUS_PATH_RE.search(raw):
        findings.append(Finding("high", raw, "Verdächtiger Pfad / Persistence-Hinweis"))


def _is_probably_text(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:2048]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            sample.decode("latin-1")
            return True
        except UnicodeDecodeError:
            return False


def _check_content(path: str, data: bytes, findings: list[Finding]) -> None:
    # PE-Header in „harmloser“ Endung
    lower = path.lower()
    if data[:2] == b"MZ" and not lower.endswith((".exe", ".dll", ".scr")):
        findings.append(
            Finding("critical", path, "Windows-Executable (MZ) unter anderer Endung")
        )

    if not _is_probably_text(data):
        return
    try:
        text = data[:MAX_CONTENT_PEEK].decode("utf-8", errors="ignore")
    except Exception:
        return
    for pat in _CONTENT_PATTERNS:
        if pat.search(text):
            findings.append(
                Finding(
                    "high",
                    path,
                    f"Verdächtiger Inhalt (Muster: {pat.pattern[:48]})",
                )
            )
            break


def _scan_zip_bytes(data: bytes, filename: str) -> ScanResult:
    result = ScanResult(filename=filename, archive_type="zip")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            result.entry_count = len(infos)
            if len(infos) > MAX_ENTRIES:
                result.findings.append(
                    Finding(
                        "medium",
                        filename,
                        f"Sehr viele Einträge ({len(infos)}) — Zip-Bomb-Verdacht",
                    )
                )
            for info in infos[:MAX_ENTRIES]:
                name = info.filename or ""
                _check_entry_name(name, result.findings)
                # Keine Ordner / riesige Binaries peeken
                if name.endswith("/") or info.is_dir():
                    continue
                if info.file_size > MAX_CONTENT_PEEK * 4:
                    continue
                ext = Path(name.lower()).suffix
                if ext in _DANGEROUS_EXTS or ext in {
                    ".txt",
                    ".json",
                    ".xml",
                    ".yml",
                    ".yaml",
                    ".ini",
                    ".cfg",
                    ".conf",
                    ".ps1",
                    ".bat",
                    ".cmd",
                    ".vbs",
                    ".js",
                    ".properties",
                }:
                    try:
                        raw = zf.read(info)[:MAX_CONTENT_PEEK]
                    except Exception:
                        continue
                    _check_content(name, raw, result.findings)
    except zipfile.BadZipFile:
        result.error = "Keine gültige ZIP/JAR-Datei"
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    return result


def _scan_rar_bytes(data: bytes, filename: str) -> ScanResult:
    result = ScanResult(filename=filename, archive_type="rar")
    try:
        import rarfile  # type: ignore
    except ImportError:
        # Ohne rarfile: nur Outer-Filename prüfen
        _check_entry_name(filename, result.findings)
        result.error = (
            "RAR-Inhaltsscan braucht Paket `rarfile` (+ UnRAR). "
            "Nur Dateiname geprüft."
        )
        return result

    try:
        # rarfile kann BytesIO mit passendem Backend
        rf = rarfile.RarFile(io.BytesIO(data))
        try:
            names = rf.namelist()
            result.entry_count = len(names)
            for name in names[:MAX_ENTRIES]:
                _check_entry_name(name, result.findings)
                try:
                    info = rf.getinfo(name)
                    if getattr(info, "isdir", lambda: False)():
                        continue
                    size = getattr(info, "file_size", 0) or 0
                    if size > MAX_CONTENT_PEEK * 4:
                        continue
                    ext = Path(name.lower()).suffix
                    if ext in _DANGEROUS_EXTS or ext in {
                        ".txt",
                        ".json",
                        ".xml",
                        ".ini",
                        ".ps1",
                        ".bat",
                        ".cmd",
                        ".vbs",
                        ".js",
                    }:
                        raw = rf.read(name)[:MAX_CONTENT_PEEK]
                        _check_content(name, raw, result.findings)
                except Exception:
                    continue
        finally:
            rf.close()
    except Exception as e:
        # Outer name + Fehler
        _check_entry_name(filename, result.findings)
        result.error = f"RAR-Scan: {type(e).__name__}: {e}"
    return result


def scan_archive_bytes(data: bytes, filename: str) -> ScanResult:
    """Scannt ZIP/JAR/RAR-Bytes auf RAT-/Malware-Indikatoren."""
    if len(data) > MAX_ARCHIVE_BYTES:
        return ScanResult(
            filename=filename,
            archive_type="unknown",
            error=f"Datei zu groß für Scan (max. {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB)",
        )

    name = (filename or "file").lower()
    # Outer-Filename immer prüfen
    outer_findings: list[Finding] = []
    _check_entry_name(Path(filename).name, outer_findings)

    if name.endswith((".zip", ".jar", ".apk")) or data[:2] == b"PK":
        result = _scan_zip_bytes(data, filename)
    elif name.endswith(".rar") or data[:4] == b"Rar!":
        result = _scan_rar_bytes(data, filename)
    else:
        result = ScanResult(
            filename=filename,
            archive_type="unknown",
            error="Kein ZIP/RAR — nur Archive werden gescannt",
        )

    # Outer findings mergen (ohne Duplikate)
    seen = {(f.path, f.reason) for f in result.findings}
    for f in outer_findings:
        key = (f.path, f.reason)
        if key not in seen:
            result.findings.append(f)
    return result


def scan_archive_path(path: Path) -> ScanResult:
    data = path.read_bytes()
    return scan_archive_bytes(data, path.name)


def is_scannable_filename(filename: str | None) -> bool:
    if not filename:
        return False
    return Path(filename).suffix.lower() in ARCHIVE_EXTS
