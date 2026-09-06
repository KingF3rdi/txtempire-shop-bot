from __future__ import annotations

import hashlib
import io
import math
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ENGINE_NAME = "TxtEmpire AV"
ENGINE_VERSION = "3.0"

# ---------------------------------------------------------------------------
# Name / Path Heuristics (klassische RAT-/Stealer-Namen)
# ---------------------------------------------------------------------------
_MALWARE_NAME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), threat)
    for p, threat in (
        (r"asyncrat", "Trojan.Win32.AsyncRAT"),
        (r"quasar(\s)?rat", "Trojan.Win32.QuasarRAT"),
        (r"\bnjrat\b", "Trojan.Win32.NjRAT"),
        (r"remcos", "Trojan.Win32.Remcos"),
        (r"darkcomet", "Trojan.Win32.DarkComet"),
        (r"nanocore", "Trojan.Win32.NanoCore"),
        (r"revengerat", "Trojan.Win32.RevengeRAT"),
        (r"xworm", "Trojan.Win32.XWorm"),
        (r"venomrat", "Trojan.Win32.VenomRAT"),
        (r"orcus", "Trojan.Win32.Orcus"),
        (r"warzone(\s)?rat", "Trojan.Win32.WarzoneRAT"),
        (r"spynote", "Trojan.Android.SpyNote"),
        (r"lime\s?rat", "Trojan.Win32.LimeRAT"),
        (r"imminent(\s)?monitor", "Trojan.Win32.Imminent"),
        (r"blacknet", "Trojan.Win32.BlackNet"),
        (r"stormkitty", "Trojan.Win32.StormKitty"),
        (r"redline", "Trojan.Win32.RedLine"),
        (r"raccoon(\s)?stealer", "Trojan.Win32.Raccoon"),
        (r"\bvidar\b", "Trojan.Win32.Vidar"),
        (r"lumma(stealer)?", "Trojan.Win32.Lumma"),
        (r"risepro", "Trojan.Win32.RisePro"),
        (r"stealc", "Trojan.Win32.Stealc"),
        (r"metastealer", "Trojan.Win32.MetaStealer"),
        (r"mars(\s)?stealer", "Trojan.Win32.Mars"),
        (r"aurora(\s)?stealer", "Trojan.Win32.Aurora"),
        (r"blank(\s)?grabber", "Trojan.Win32.BlankGrabber"),
        (r"empyrean", "Trojan.Win32.Empyrean"),
        (r"creal(\s)?stealer", "Trojan.Win32.Creal"),
        (r"phoenix(\s)?stealer", "Trojan.Win32.Phoenix"),
        (r"atomic(\s)?stealer", "Trojan.Win32.Atomic"),
        (r"meduza(\s)?stealer", "Trojan.Win32.Meduza"),
        (r"snake(\s)?keylogger", "Trojan.Win32.SnakeKeylogger"),
        (r"agent\s?tesla", "Trojan.Win32.AgentTesla"),
        (r"formbook", "Trojan.Win32.Formbook"),
        (r"lokibot", "Trojan.Win32.LokiBot"),
        (r"ave.?maria", "Trojan.Win32.AveMaria"),
        (r"guloader", "Trojan.Win32.GuLoader"),
        (r"smoke\s?loader", "Trojan.Win32.SmokeLoader"),
        (r"privateloader", "Trojan.Win32.PrivateLoader"),
        (r"amadie", "Trojan.Win32.Amadey"),
        (r"dc.?rat", "Trojan.Win32.DCRAT"),
        (r"bitrat", "Trojan.Win32.BitRAT"),
        (r"netwire", "Trojan.Win32.NetWire"),
        (r"poison\s?ivy", "Trojan.Win32.PoisonIvy"),
        (r"cypher\s?rat", "Trojan.Win32.CypherRAT"),
        (r"pulsar(\s)?rat", "Trojan.Win32.PulsarRAT"),
        (r"parallax(\s)?rat", "Trojan.Win32.Parallax"),
        (r"extreme(\s)?rat", "Trojan.Win32.ExtremeRAT"),
        (r"\brat\b.*\.(exe|dll|jar|scr)", "Trojan.Generic.RAT"),
        (
            r"(keylogger|clipper|stealer|grabber|hvnc).*\.(exe|dll|jar|scr|bat|ps1)",
            "Trojan.Generic.Stealer",
        ),
        (
            r"(hack|crack|inject).*\.(exe|dll|bat|ps1|vbs)",
            "Heur.Suspicious.CrackInject",
        ),
    )
)

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

_DOUBLE_EXT_RE = re.compile(
    r"\.(png|jpg|jpeg|gif|webp|txt|json|mcmeta|ogg|zip|rar|jar)\."
    r"(exe|dll|scr|bat|cmd|ps1|vbs|js)$",
    re.IGNORECASE,
)

_SUSPICIOUS_PATH_RE = re.compile(
    r"(appdata|startup|system32|syswow64|windows[/\\]temp|programdata|"
    r"autorun|persistence|inject|payload|shellcode)",
    re.IGNORECASE,
)

# Text-/Config-Inhalte
_CONTENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), threat)
    for p, threat in (
        (r"asyncrat", "Trojan.Config.AsyncRAT"),
        (r"quasarrat", "Trojan.Config.QuasarRAT"),
        (r"telegram\.me/bot", "Heur.Exfil.TelegramBot"),
        (r"discord\.com/api/webhooks", "Heur.Exfil.DiscordWebhook"),
        (r"webhook\.site", "Heur.Exfil.WebhookSite"),
        (r"pastebin\.com/raw", "Heur.Downloader.Pastebin"),
        (
            r"HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
            "Heur.Persistence.RunKey",
        ),
        (r"Add-MpPreference\s+-ExclusionPath", "Heur.DefenseEvasion.AVExclude"),
        (r"amsiInitFailed", "Heur.DefenseEvasion.AMSI"),
        (r"System\.Reflection\.Assembly\.Load", "Heur.Loader.Reflection"),
        (r"FromBase64String", "Heur.Obfuscation.Base64"),
        (r"DownloadString\s*\(", "Heur.Downloader.PowerShell"),
        (r"Invoke-Expression|IEX\s*\(", "Heur.Execution.IEX"),
        (r"keylog", "Heur.Spyware.Keylog"),
        (r"clipper", "Heur.Spyware.Clipper"),
        (
            r"steal(er|ing)?.*(cookie|token|password|wallet)",
            "Heur.Spyware.CredentialTheft",
        ),
        (r"bitcoin|btc.?address|electrum|exodus|metamask", "Heur.Spyware.CryptoWallet"),
        (r"schtasks\s+/create", "Heur.Persistence.ScheduledTask"),
        (r"New-Object\s+Net\.WebClient", "Heur.Downloader.WebClient"),
        (r"bitsadmin\s+/transfer", "Heur.Downloader.Bitsadmin"),
        (r"certutil\s+-decode|certutil\s+-urlcache", "Heur.Downloader.Certutil"),
        (r"Start-Process\s+-WindowStyle\s+Hidden", "Heur.Execution.HiddenProcess"),
    )
)

# ---------------------------------------------------------------------------
# Erweitert (v3.0): IP-Logger/Exfil-Domains, Discord-Token-Diebstahl,
# Java-Agent-Injection — typisch bei Minecraft-/Discord-gezielter Malware,
# die klassische Signatur-Scanner oft übersehen.
# ---------------------------------------------------------------------------
_IP_LOGGER_DOMAINS_RE = re.compile(
    r"(grabify\.link|iplogger\.(org|com|ru)|2no\.co|blasze\.(com|io)|"
    r"yip\.su|whatstheirip\.com|ps3cfw\.com|stopmodreposts\.org|"
    r"ipgrabber\.ru|canarytokens\.com|dnslog\.cn)",
    re.IGNORECASE,
)

_ONION_RE = re.compile(r"\b[a-z2-7]{16,56}\.onion\b", re.IGNORECASE)

# Rohe IP:Port-Literale (typisches C2-Callback-Muster in Configs/Scripts)
_RAW_IP_PORT_RE = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b"
)

# Discord-Account-Token-Muster (Bot- und User-Token-Formate)
_DISCORD_TOKEN_RE = re.compile(
    r"\b[MNO][A-Za-z\d_-]{23,27}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,40}\b|"
    r"\bmfa\.[A-Za-z\d_-]{80,}\b"
)

_DISCORD_STEALER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), threat)
    for p, threat in (
        (r"leveldb.*discord|discord.*leveldb", "Trojan.Win32.DiscordTokenGrabber"),
        (r"local storage.*discord|discord.*local storage",
         "Trojan.Win32.DiscordTokenGrabber"),
        (r"\\discord\\local storage\\leveldb", "Trojan.Win32.DiscordTokenGrabber"),
        (r"injection\.js|betterdiscord.*inject", "Heur.Discord.ClientInjection"),
        (r"discord_desktop_core", "Heur.Discord.ClientPatch"),
    )
)

# JAR/Java-Agent-Injection — legitime Mods nutzen selten Premain/Agent-Classes
_JAVA_AGENT_MANIFEST_RE = re.compile(
    r"^(Premain-Class|Agent-Class|Launcher-Agent-Class)\s*:",
    re.IGNORECASE | re.MULTILINE,
)

# Binär-Signaturen (ASCII/UTF-16LE Marker in PE/Scripts)
_BINARY_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"AsyncRAT", "critical", "Trojan.Win32.AsyncRAT"),
    (b"Quasar Client", "critical", "Trojan.Win32.QuasarRAT"),
    (b"Quasar.", "critical", "Trojan.Win32.QuasarRAT"),
    (b"Remcos", "critical", "Trojan.Win32.Remcos"),
    (b"njRAT", "critical", "Trojan.Win32.NjRAT"),
    (b"NanoCore", "critical", "Trojan.Win32.NanoCore"),
    (b"XWorm", "critical", "Trojan.Win32.XWorm"),
    (b"RedLine", "critical", "Trojan.Win32.RedLine"),
    (b"StealC", "critical", "Trojan.Win32.Stealc"),
    (b"LummaC", "critical", "Trojan.Win32.Lumma"),
    (b"Raccoon Stealer", "critical", "Trojan.Win32.Raccoon"),
    (b"HVNC", "high", "Trojan.Win32.HVNC"),
    (b"amsiInitFailed", "critical", "Heur.DefenseEvasion.AMSI"),
    (b"AmsiScanBuffer", "high", "Heur.DefenseEvasion.AMSIBypass"),
    (b"VirtualAllocEx", "medium", "Heur.Injection.VirtualAllocEx"),
    (b"WriteProcessMemory", "medium", "Heur.Injection.WriteProcessMemory"),
    (b"CreateRemoteThread", "high", "Heur.Injection.CreateRemoteThread"),
    (b"ReflectiveLoader", "critical", "Trojan.Generic.ReflectiveDLL"),
    (b"powershell -enc", "high", "Heur.Execution.EncodedPowerShell"),
    (b"powershell -e ", "high", "Heur.Execution.EncodedPowerShell"),
    (b"-EncodedCommand", "high", "Heur.Execution.EncodedPowerShell"),
    (b"FromBase64String", "medium", "Heur.Obfuscation.Base64"),
    (b"discord.com/api/webhooks", "high", "Heur.Exfil.DiscordWebhook"),
    (b"api.telegram.org/bot", "high", "Heur.Exfil.TelegramBot"),
    # UTF-16LE Varianten häufiger .NET-RAT Strings
    ("AsyncRAT".encode("utf-16le"), "critical", "Trojan.Win32.AsyncRAT"),
    ("Quasar".encode("utf-16le"), "critical", "Trojan.Win32.QuasarRAT"),
    ("Remcos".encode("utf-16le"), "critical", "Trojan.Win32.Remcos"),
)

# Bekannte schädliche SHA-256 (leer startend — erweiterbar)
_KNOWN_BAD_SHA256: frozenset[str] = frozenset(
    {
        # Platzhalter-Beispiele aus öffentlichen Samples (EICAR-ähnlich / Demo)
        # EICAR-Testfile SHA256:
        "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f",
    }
)

ARCHIVE_EXTS = {".zip", ".rar", ".jar", ".apk", ".war", ".7z"}
NESTED_ARCHIVE_EXTS = {".zip", ".jar", ".apk", ".war", ".7z"}
# Einzeldateien, die auch OHNE Archiv direkt hochgeladen/gescannt werden dürfen
# (z. B. eine nackte .exe, die jemand vor dem Ausführen prüfen will).
SINGLE_FILE_SCAN_EXTS = frozenset(_DANGEROUS_EXTS)
MAX_ENTRIES = 8000
MAX_NAME_LEN = 512
MAX_CONTENT_PEEK = 256 * 1024  # Text-Heuristik: erste 256 KB
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_NESTED_DEPTH = 3
# Deep-Scan: jede Datei streamen (Hash + Signaturen), nicht nur Dateianfang
MAX_FILE_SCAN_BYTES = 12 * 1024 * 1024  # bis 12 MB Inhalt / Datei
MAX_TOTAL_SCAN_BYTES = 96 * 1024 * 1024  # Budget über gesamtes Archiv
CHUNK_SIZE = 256 * 1024
MAX_BINARY_SCAN = MAX_FILE_SCAN_BYTES  # Kompatibilität für Imports
ENTROPY_SAMPLE = 64 * 1024
HIGH_ENTROPY_THRESHOLD = 7.2

# Vorbereitete Signaturen für Chunk-Scan (Overlap = längste Nadel)
_PREP_SIGNATURES: tuple[tuple[bytes, bool, str, str], ...] = tuple(
    (
        (sig, False, sev, threat)
        if b"\x00" in sig
        else (sig.lower(), True, sev, threat)
    )
    for sig, sev, threat in _BINARY_SIGNATURES
)
_MAX_SIG_LEN = max((len(sig) for sig, *_ in _PREP_SIGNATURES), default=32)

TEXTISH_EXTS = {
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
    ".cs",
    ".java",
    ".py",
    ".sh",
    ".html",
    ".htm",
    ".php",
    ".asp",
    ".aspx",
    ".manifest",
    ".config",
    ".log",
    ".md",
    ".csv",
}

SCAN_DISCLAIMER = (
    "⚠️ **Keine 100 %-Garantie:** Multi-Engine-Heuristik "
    f"({ENGINE_NAME} {ENGINE_VERSION}: ZIP/RAR/JAR/7Z + einzelne .exe/.dll, "
    "Namen, Hashes, Signaturen, Inhalt, "
    "Discord-Token-Grabber, IP-Logger/C2-Muster, Java-Agent-Injection, "
    "Kompressions-/Entropie-Analyse, Kombinations-Scoring). "
    "Kann Threats übersehen oder Fehlalarme erzeugen — "
    "**kein Ersatz** für Windows Defender / ClamAV / VirusTotal."
)


@dataclass
class Finding:
    severity: str  # critical | high | medium
    path: str
    reason: str
    threat_name: str = ""


def explain_finding(finding: Finding) -> str:
    """Klartext: was erkannt wurde und warum das relevant ist."""
    r = finding.reason.lower()
    path = finding.path
    threat = finding.threat_name or finding.reason

    if "doppelte dateiendung" in r:
        detail = (
            "Die Datei nutzt eine **doppelte Endung** (z.B. `bild.png.exe`). "
            "So wirken gefährliche Dateien oft harmlos."
        )
    elif "gefährliche dateiendung" in r:
        detail = (
            "Im Archiv liegt eine Datei mit riskanter Endung. "
            "In Minecraft-/Client-Packs sind `.exe`, `.dll`, `.bat`, `.ps1` usw. "
            "meist **kein normaler Inhalt** — oft Malware/Loader."
        )
    elif "windows-executable" in r or "mz-header" in r or "pe-header" in r:
        detail = (
            "Die Datei beginnt mit einem **Windows-PE-Header (MZ)**, obwohl die "
            "Endung etwas anderes vorgibt. Inhalt wurde als Executable getarnt."
        )
    elif "sha256" in r or "bekannter hash" in r:
        detail = (
            "Der **Datei-Hash** stimmt mit einer bekannten Malware-Signatur überein."
        )
    elif "binärsignatur" in r or "signature" in r:
        detail = (
            "Im Dateiinhalt wurde eine **bekannte Malware-Signatur** gefunden "
            f"(`{threat}`)."
        )
    elif "entropie" in r:
        detail = (
            "Sehr hohe Entropie — typisch für **gepackte/verschlüsselte** "
            "Payloads (Packer/Crypter)."
        )
    elif "verdächtiger name" in r or "namensmuster" in r:
        detail = (
            "Der **Dateiname/Pfad** matcht bekannte RAT-/Stealer-/Loader-Muster "
            f"(`{threat}`)."
        )
    elif "verdächtiger inhalt" in r or "inhaltssignatur" in r:
        detail = (
            "Im **Dateiinhalt** wurden verdächtige Strings/Muster gefunden "
            f"(`{threat}`)."
        )
    elif "pfad-traversal" in r or "absoluter pfad" in r:
        detail = (
            "Der Eintrag nutzt `..` oder einen absoluten Pfad — typisch für "
            "Archives, die Dateien **außerhalb** des Zielordners schreiben wollen."
        )
    elif "persistence" in r or "verdächtiger pfad" in r:
        detail = (
            "Der Pfad deutet auf **Autostart/Persistence** hin "
            "(z.B. Startup-Ordner). Ungewöhnlich in normalen Packs."
        )
    elif "zip-bomb" in r or "viele einträge" in r:
        detail = (
            "Ungewöhnlich viele Archiv-Einträge — kann auf **Zip-Bomb** "
            "oder Obfuscation hindeuten."
        )
    elif "obfuscation" in r or "langer pfad" in r:
        detail = (
            "Extrem langer Dateipfad — oft eingesetzt, um Scans/Viewer "
            "zu erschweren (Obfuscation)."
        )
    elif "elf-header" in r or "mach-o" in r:
        detail = (
            "Unerwartetes natives Binary (ELF/Mach-O) in einem Client-Pack."
        )
    elif "discord-token-diebstahl" in r:
        detail = (
            "Die Datei greift gezielt auf den **Discord-Client-Speicher** "
            "(LevelDB/Local Storage) zu — klassisches Muster für "
            "**Discord-Token-Grabber**, die dein Konto übernehmen können."
        )
    elif "ip-logger" in r or "exfil-domain" in r:
        detail = (
            "Der Inhalt verweist auf einen bekannten **IP-Logger-Dienst** — "
            "wird genutzt, um beim Öffnen der Datei heimlich deine IP-Adresse "
            "und ggf. Standortdaten zu erfassen."
        )
    elif "onion-adresse" in r:
        detail = (
            "Eine **Tor-.onion-Adresse** im Inhalt kann auf einen versteckten "
            "Command-&-Control-Server (C2) hindeuten, mit dem Malware "
            "kommuniziert."
        )
    elif "c2-callback" in r or "ip:port-adresse" in r:
        detail = (
            "Eine rohe **IP:Port-Kombination** im Code/Inhalt ist ein "
            "typisches Muster für hartkodierte **C2-Server-Adressen** "
            "in RATs/Backdoors."
        )
    elif "discord-token-format" in r:
        detail = (
            "Im Inhalt steht ein Text, der wie ein **echter Discord-Token** "
            "aussieht — entweder ein geleakter Token oder ein Hinweis, dass "
            "die Datei Tokens sammelt/exfiltriert."
        )
    elif "java-agent-injection" in r:
        detail = (
            "Das JAR deklariert eine **Java-Agent-Klasse** (Premain/Agent-Class) "
            "im Manifest — eine Technik, mit der Code **in andere laufende "
            "Java-Prozesse eingeschleust** wird. In normalen Mods/Texture-"
            "packs unüblich."
        )
    elif "kompressionsrate" in r:
        detail = (
            "Der Eintrag entpackt sich auf ein Vielfaches seiner komprimierten "
            "Größe — typisches Muster für eine **Zip-Bomb** (Denial-of-Service "
            "beim Entpacken/Scannen)."
        )
    elif "kombination mehrerer verdachtsmomente" in r:
        detail = (
            "**Mehrere unabhängige Warnsignale** treffen auf dieselbe Datei zu "
            "(z.B. Verschleierung + verdächtiger Inhalt + Netzwerk-Hinweise). "
            "Einzeln wäre keins davon eindeutig — in Kombination ist das "
            "ein starkes Indiz für **bisher unbekannte/neue Malware**, die "
            "keine klassische Signatur hat."
        )
    else:
        detail = (
            f"Erkannt: **{threat}**. Bitte Datei und Herkunft prüfen, "
            "bevor du sie öffnest oder ausführst."
        )

    sev = {
        "critical": "KRITISCH",
        "high": "HOCH",
        "medium": "MITTEL",
    }.get(finding.severity, finding.severity.upper())
    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(
        finding.severity, "⚪"
    )
    short_path = path if len(path) <= 90 else path[:87] + "…"
    threat_line = f" **Threat:** `{threat}`\n" if finding.threat_name else ""
    return (
        f"{icon} **{sev}** — `{short_path}`\n"
        f"{threat_line}"
        f" **Was:** {finding.reason}\n"
        f" **Bedeutung:** {detail}"
    )


@dataclass
class ScanResult:
    filename: str
    archive_type: str
    findings: list[Finding] = field(default_factory=list)
    entry_count: int = 0
    files_scanned: int = 0
    bytes_scanned: int = 0
    duration_ms: int = 0
    error: str | None = None
    engine: str = f"{ENGINE_NAME}/{ENGINE_VERSION}"

    @property
    def is_clean(self) -> bool:
        return self.error is None and not self.findings

    @property
    def is_blocked(self) -> bool:
        return any(f.severity in ("critical", "high") for f in self.findings)

    @property
    def verdict(self) -> str:
        if self.error and not self.findings:
            return "ERROR"
        if self.is_clean:
            return "CLEAN"
        if any(f.severity == "critical" for f in self.findings):
            return "INFECTED"
        if self.is_blocked:
            return "THREAT"
        return "SUSPICIOUS"

    def summary(self, *, limit: int = 12) -> str:
        if self.error and not self.findings:
            return f"Scan-Fehler: {self.error}\n\n{SCAN_DISCLAIMER}"

        mb = self.bytes_scanned / (1024 * 1024)
        meta = (
            f"🛡 **{self.engine}** · `{self.filename}` · {self.archive_type}\n"
            f"📂 {self.entry_count} Einträge · 🔬 {self.files_scanned} Dateien · "
            f"📦 {mb:.2f} MB Deep-Scan"
        )
        if self.duration_ms:
            meta += f" · ⏱ {self.duration_ms / 1000:.1f}s"

        if not self.findings:
            return (
                f"✅ **VERDICT: CLEAN**\n{meta}\n\n"
                f"Deep-Scan abgeschlossen — keine bekannten Malware-Indikatoren.\n\n"
                f"{SCAN_DISCLAIMER}"
            )

        crit = sum(1 for f in self.findings if f.severity == "critical")
        high = sum(1 for f in self.findings if f.severity == "high")
        med = sum(1 for f in self.findings if f.severity == "medium")
        verdict = self.verdict
        verdict_icon = {
            "INFECTED": "⛔",
            "THREAT": "⛔",
            "SUSPICIOUS": "⚠️",
        }.get(verdict, "⚠️")

        lines = [
            f"{verdict_icon} **VERDICT: {verdict}** — "
            f"**{len(self.findings)} Threat(s)**",
            meta,
            f"Aufschlüsselung: 🔴 {crit} kritisch · 🟠 {high} hoch · 🟡 {med} mittel",
            "",
            "**Erkannte Threats:**",
        ]
        for f in self.findings[:limit]:
            lines.append(explain_finding(f))
            lines.append("")
        if len(self.findings) > limit:
            lines.append(
                f"_…und {len(self.findings) - limit} weitere Treffer "
                f"(Liste gekürzt)._"
            )
            lines.append("")
        lines.append(
            "👉 **Empfehlung:** Datei **nicht** ausführen/öffnen, Herkunft prüfen, "
            "ggf. Staff informieren. Bei INFECTED: Datei löschen."
        )
        lines.append("")
        lines.append(SCAN_DISCLAIMER)
        text = "\n".join(lines).strip()
        if len(text) > 3900:
            text = text[:3890] + "\n_…gekürzt_"
        return text


def _add_finding(
    findings: list[Finding],
    *,
    severity: str,
    path: str,
    reason: str,
    threat_name: str = "",
    seen: set[tuple[str, str]] | None = None,
) -> None:
    key = (path, threat_name or reason)
    if seen is not None:
        if key in seen:
            return
        seen.add(key)
    findings.append(
        Finding(
            severity=severity,
            path=path,
            reason=reason,
            threat_name=threat_name,
        )
    )


def _escalate_combined_findings(
    findings: list[Finding],
    *,
    seen: set[tuple[str, str]] | None = None,
) -> list[Finding]:
    """
    Kombiniert mehrere schwache Indikatoren pro Datei zu einem starken Befund.

    Ein klassischer Signatur-Scanner bewertet jeden Treffer isoliert.
    Hier gilt: 3 unabhängige "medium/high"-Heuristiken auf **derselben Datei**
    (z.B. hohe Entropie + rohe IP:Port-Adresse + langer Obfuscation-Pfad)
    sind zusammen ein deutlich stärkeres Signal als jede für sich — das
    erkennt auch neue/unbekannte Malware-Varianten ohne bekannte Signatur.
    """
    weight = {"critical": 3, "high": 2, "medium": 1}
    by_path: dict[str, list[Finding]] = {}
    for f in findings:
        by_path.setdefault(f.path, []).append(f)

    extra: list[Finding] = []
    for path, items in by_path.items():
        if any(f.severity == "critical" for f in items):
            continue  # schon eindeutig — keine Eskalation nötig
        distinct_threats = {f.threat_name or f.reason for f in items}
        if len(distinct_threats) < 3:
            continue
        score = sum(weight.get(f.severity, 0) for f in items)
        if score < 4:
            continue
        names = ", ".join(sorted(t for t in distinct_threats if t)[:5])
        _add_finding(
            extra,
            severity="critical",
            path=path,
            reason=(
                f"Kombination mehrerer Verdachtsmomente ({len(distinct_threats)} "
                f"unabhängige Indikatoren, Score {score})"
            ),
            threat_name=f"Heur.Combined.MultipleIndicators[{names}]",
            seen=seen,
        )
    return findings + extra


def _check_entry_name(
    name: str,
    findings: list[Finding],
    *,
    seen: set[tuple[str, str]] | None = None,
) -> None:
    raw = name.replace("\\", "/")
    lower = raw.lower()
    base = Path(lower).name

    if len(raw) > MAX_NAME_LEN:
        _add_finding(
            findings,
            severity="medium",
            path=raw[:80] + "…",
            reason="Extrem langer Pfad (Obfuscation?)",
            threat_name="Heur.Obfuscation.LongPath",
            seen=seen,
        )

    if ".." in Path(raw).parts or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        _add_finding(
            findings,
            severity="high",
            path=raw,
            reason="Pfad-Traversal / absoluter Pfad",
            threat_name="Heur.Archive.PathTraversal",
            seen=seen,
        )

    if _DOUBLE_EXT_RE.search(base):
        _add_finding(
            findings,
            severity="critical",
            path=raw,
            reason="Doppelte Dateiendung (Tarnung)",
            threat_name="Heur.Disguise.DoubleExtension",
            seen=seen,
        )

    ext = Path(base).suffix
    if ext in _DANGEROUS_EXTS:
        _add_finding(
            findings,
            severity="critical",
            path=raw,
            reason=f"Gefährliche Dateiendung ({ext})",
            threat_name=f"Heur.DangerousExt{ext.upper()}",
            seen=seen,
        )

    for pat, threat in _MALWARE_NAME_PATTERNS:
        if pat.search(raw):
            _add_finding(
                findings,
                severity="critical",
                path=raw,
                reason="Verdächtiger Name / Namensmuster",
                threat_name=threat,
                seen=seen,
            )
            break

    if _SUSPICIOUS_PATH_RE.search(raw):
        _add_finding(
            findings,
            severity="high",
            path=raw,
            reason="Verdächtiger Pfad / Persistence-Hinweis",
            threat_name="Heur.Persistence.SuspiciousPath",
            seen=seen,
        )


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


def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    sample = data[:ENTROPY_SAMPLE]
    counts = [0] * 256
    for b in sample:
        counts[b] += 1
    length = len(sample)
    ent = 0.0
    for c in counts:
        if c:
            p = c / length
            ent -= p * math.log2(p)
    return ent


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _match_signatures_in_window(
    window: bytes,
    path: str,
    findings: list[Finding],
    *,
    seen: set[tuple[str, str]] | None,
    already: set[str],
) -> None:
    lowered = window.lower()
    for needle, casefold, severity, threat in _PREP_SIGNATURES:
        if threat in already:
            continue
        hay = lowered if casefold else window
        if needle in hay:
            already.add(threat)
            _add_finding(
                findings,
                severity=severity,
                path=path,
                reason="Binärsignatur / Malware-Marker",
                threat_name=threat,
                seen=seen,
            )


def _check_magic_headers(
    path: str,
    head: bytes,
    findings: list[Finding],
    *,
    seen: set[tuple[str, str]] | None = None,
) -> None:
    lower = path.lower()
    ext = Path(lower).suffix

    if head[:2] == b"MZ" and ext not in {".exe", ".dll", ".scr", ".sys", ".cpl", ".ocx"}:
        _add_finding(
            findings,
            severity="critical",
            path=path,
            reason="Windows-Executable (MZ-Header) unter anderer Endung",
            threat_name="Heur.Disguise.PEHeader",
            seen=seen,
        )
    elif head[:2] == b"MZ" and ext in _DANGEROUS_EXTS:
        ent = _shannon_entropy(head)
        if ent >= HIGH_ENTROPY_THRESHOLD:
            _add_finding(
                findings,
                severity="high",
                path=path,
                reason=f"Hohe Entropie ({ent:.2f}) — gepacktes/encrypted PE?",
                threat_name="Heur.Packer.HighEntropy",
                seen=seen,
            )

    if head[:4] == b"\x7fELF":
        _add_finding(
            findings,
            severity="high",
            path=path,
            reason="ELF-Binary im Archiv",
            threat_name="Heur.Native.ELF",
            seen=seen,
        )

    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe"):
        _add_finding(
            findings,
            severity="medium",
            path=path,
            reason="Mach-O Binary im Archiv",
            threat_name="Heur.Native.MachO",
            seen=seen,
        )


def _check_text_content(
    path: str,
    data: bytes,
    findings: list[Finding],
    *,
    seen: set[tuple[str, str]] | None = None,
) -> None:
    if not data:
        return
    if not _is_probably_text(data[:2048] if len(data) > 2048 else data):
        # .class / Scripts oft mit Nullbytes — trotzdem ASCII-Strings prüfen
        try:
            text = data[:MAX_CONTENT_PEEK].decode("utf-8", errors="ignore")
        except Exception:
            return
        if len(text) < 16:
            return
    else:
        try:
            text = data[:MAX_CONTENT_PEEK].decode("utf-8", errors="ignore")
        except Exception:
            return
    for pat, threat in _CONTENT_PATTERNS:
        if pat.search(text):
            _add_finding(
                findings,
                severity="high",
                path=path,
                reason="Verdächtiger Inhalt / Inhaltssignatur",
                threat_name=threat,
                seen=seen,
            )
            break

    for pat, threat in _DISCORD_STEALER_PATTERNS:
        if pat.search(text):
            _add_finding(
                findings,
                severity="critical",
                path=path,
                reason="Discord-Token-Diebstahl-Muster erkannt",
                threat_name=threat,
                seen=seen,
            )
            break

    if _IP_LOGGER_DOMAINS_RE.search(text):
        _add_finding(
            findings,
            severity="high",
            path=path,
            reason="IP-Logger/Exfil-Domain im Inhalt gefunden",
            threat_name="Heur.Exfil.IPLogger",
            seen=seen,
        )

    if _ONION_RE.search(text):
        _add_finding(
            findings,
            severity="medium",
            path=path,
            reason="Tor-.onion-Adresse im Inhalt (mögliche C2)",
            threat_name="Heur.Exfil.OnionAddress",
            seen=seen,
        )

    # Nur in Skript-/Code-Dateien prüfen — vermeidet False Positives bei
    # legitimen Server-IP-Erwähnungen in pack.mcmeta/README/Configs.
    _script_exts = {
        ".js", ".jse", ".ps1", ".bat", ".cmd", ".vbs", ".wsf",
        ".py", ".cs", ".java", ".php", ".sh",
    }
    if Path(path.lower()).suffix in _script_exts and _RAW_IP_PORT_RE.search(text):
        _add_finding(
            findings,
            severity="medium",
            path=path,
            reason="Rohe IP:Port-Adresse im Inhalt (mögliches C2-Callback)",
            threat_name="Heur.C2.RawIPPort",
            seen=seen,
        )

    if _DISCORD_TOKEN_RE.search(text):
        _add_finding(
            findings,
            severity="high",
            path=path,
            reason="Discord-Token-Format im Klartext gefunden",
            threat_name="Heur.Credential.DiscordToken",
            seen=seen,
        )

    if Path(path.lower()).name in ("manifest.mf",) or path.lower().endswith(
        "/manifest.mf"
    ):
        if _JAVA_AGENT_MANIFEST_RE.search(text):
            _add_finding(
                findings,
                severity="high",
                path=path,
                reason="Java-Agent-Injection im MANIFEST.MF (Premain/Agent-Class)",
                threat_name="Heur.Java.AgentInjection",
                seen=seen,
            )


def _scan_stream(
    path: str,
    stream,
    findings: list[Finding],
    *,
    seen: set[tuple[str, str]] | None,
    nested_depth: int,
    budget: list[int],
    size_hint: int = 0,
) -> tuple[int, int]:
    """
    Deep-Scan eines Dateistreams: voller SHA-256 (bis Limit),
    Signaturen über alle Chunks, Magic, Text-Heuristik.
    budget: ein-elementige Liste [remaining_bytes] (mutable).
    Returns (files_scanned, bytes_read).
    """
    if budget[0] <= 0:
        return 0, 0

    ext = Path(path.lower()).suffix
    hasher = hashlib.sha256()
    overlap = b""
    head = b""
    text_buf = bytearray()
    nested_buf: bytearray | None = None
    want_nested = (
        nested_depth < MAX_NESTED_DEPTH and ext in NESTED_ARCHIVE_EXTS
    )
    if want_nested:
        nested_buf = bytearray()

    total = 0
    sig_hits: set[str] = set()
    limit = min(MAX_FILE_SCAN_BYTES, budget[0])
    if size_hint > 0:
        limit = min(limit, max(size_hint, CHUNK_SIZE))

    while total < limit:
        to_read = min(CHUNK_SIZE, limit - total)
        try:
            chunk = stream.read(to_read)
        except Exception:
            break
        if not chunk:
            break

        if total == 0:
            head = chunk[:8192]
            _check_magic_headers(path, head, findings, seen=seen)

        hasher.update(chunk)
        window = overlap + chunk
        _match_signatures_in_window(
            window, path, findings, seen=seen, already=sig_hits
        )
        overlap = window[-_MAX_SIG_LEN:] if len(window) >= _MAX_SIG_LEN else window

        if len(text_buf) < MAX_CONTENT_PEEK:
            need = MAX_CONTENT_PEEK - len(text_buf)
            text_buf.extend(chunk[:need])

        if nested_buf is not None and len(nested_buf) < MAX_ARCHIVE_BYTES:
            need_n = MAX_ARCHIVE_BYTES - len(nested_buf)
            nested_buf.extend(chunk[:need_n])

        total += len(chunk)

    budget[0] = max(0, budget[0] - total)

    digest = hasher.hexdigest()
    if digest in _KNOWN_BAD_SHA256:
        _add_finding(
            findings,
            severity="critical",
            path=path,
            reason=f"Bekannter Malware-Hash (SHA256 {digest[:16]}…)",
            threat_name="Trojan.Hash.KnownBad",
            seen=seen,
        )

    if text_buf:
        _check_text_content(path, bytes(text_buf), findings, seen=seen)

    files = 1
    # Nested ZIP/JAR vollständig (soweit gelesen) nachscannen
    if (
        nested_buf is not None
        and len(nested_buf) >= 4
        and nested_buf[:2] == b"PK"
    ):
        nested = _scan_zip_bytes(
            bytes(nested_buf),
            path,
            nested_depth=nested_depth + 1,
            outer_seen=seen,
            budget=budget,
        )
        findings.extend(nested.findings)
        files += nested.files_scanned
        total += nested.bytes_scanned

    return files, total


def _scan_file_bytes(
    path: str,
    data: bytes,
    findings: list[Finding],
    *,
    seen: set[tuple[str, str]] | None = None,
    nested_depth: int = 0,
    budget: list[int] | None = None,
) -> tuple[int, int]:
    """Scannt Bytes (Wrapper). Returns (files_scanned, bytes_scanned)."""
    if not data:
        return 0, 0
    if budget is None:
        budget = [MAX_TOTAL_SCAN_BYTES]
    return _scan_stream(
        path,
        io.BytesIO(data),
        findings,
        seen=seen,
        nested_depth=nested_depth,
        budget=budget,
        size_hint=len(data),
    )


def _scan_zip_bytes(
    data: bytes,
    filename: str,
    *,
    nested_depth: int = 0,
    outer_seen: set[tuple[str, str]] | None = None,
    budget: list[int] | None = None,
) -> ScanResult:
    result = ScanResult(
        filename=filename,
        archive_type="zip" if nested_depth == 0 else "nested-zip",
    )
    seen: set[tuple[str, str]] = outer_seen if outer_seen is not None else set()
    if budget is None:
        budget = [MAX_TOTAL_SCAN_BYTES]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            result.entry_count = len(infos)
            if len(infos) > MAX_ENTRIES:
                _add_finding(
                    result.findings,
                    severity="medium",
                    path=filename,
                    reason=f"Sehr viele Einträge ({len(infos)}) — Zip-Bomb-Verdacht",
                    threat_name="Heur.Archive.ZipBomb",
                    seen=seen,
                )
            for info in infos[:MAX_ENTRIES]:
                if budget[0] <= 0:
                    _add_finding(
                        result.findings,
                        severity="medium",
                        path=filename,
                        reason="Scan-Budget erreicht — Rest übersprungen",
                        threat_name="Heur.Scan.BudgetCap",
                        seen=seen,
                    )
                    break
                name = info.filename or ""
                display = name if nested_depth == 0 else f"{filename}!/{name}"
                _check_entry_name(display, result.findings, seen=seen)
                if name.endswith("/") or info.is_dir():
                    continue
                size = int(info.file_size or 0)
                if size <= 0 and info.compress_size:
                    size = int(info.compress_size)
                comp = int(info.compress_size or 0)
                if comp > 4096 and size > 0:
                    ratio = size / comp
                    if ratio > 300:
                        _add_finding(
                            result.findings,
                            severity="high",
                            path=display,
                            reason=(
                                f"Extreme Kompressionsrate (1:{ratio:.0f}) "
                                "im Archiv-Eintrag"
                            ),
                            threat_name="Heur.Archive.CompressionBomb",
                            seen=seen,
                        )
                try:
                    with zf.open(info, "r") as entry:
                        files, nbytes = _scan_stream(
                            display,
                            entry,
                            result.findings,
                            seen=seen,
                            nested_depth=nested_depth,
                            budget=budget,
                            size_hint=size,
                        )
                except Exception:
                    continue
                result.files_scanned += files
                result.bytes_scanned += nbytes
    except zipfile.BadZipFile:
        result.error = "Keine gültige ZIP/JAR-Datei"
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    return result


def _scan_rar_bytes(data: bytes, filename: str) -> ScanResult:
    result = ScanResult(filename=filename, archive_type="rar")
    seen: set[tuple[str, str]] = set()
    budget = [MAX_TOTAL_SCAN_BYTES]
    try:
        import rarfile  # type: ignore
    except ImportError:
        _check_entry_name(filename, result.findings, seen=seen)
        result.error = (
            "RAR-Inhaltsscan braucht Paket `rarfile` (+ UnRAR). "
            "Nur Dateiname geprüft."
        )
        return result

    try:
        rf = rarfile.RarFile(io.BytesIO(data))
        try:
            names = rf.namelist()
            result.entry_count = len(names)
            for name in names[:MAX_ENTRIES]:
                if budget[0] <= 0:
                    break
                _check_entry_name(name, result.findings, seen=seen)
                try:
                    info = rf.getinfo(name)
                    if getattr(info, "isdir", lambda: False)():
                        continue
                    size = getattr(info, "file_size", 0) or 0
                    try:
                        entry = rf.open(name)
                    except Exception:
                        raw = rf.read(name)
                        files, nbytes = _scan_file_bytes(
                            name,
                            raw,
                            result.findings,
                            seen=seen,
                            nested_depth=0,
                            budget=budget,
                        )
                        result.files_scanned += files
                        result.bytes_scanned += nbytes
                        continue
                    try:
                        files, nbytes = _scan_stream(
                            name,
                            entry,
                            result.findings,
                            seen=seen,
                            nested_depth=0,
                            budget=budget,
                            size_hint=int(size),
                        )
                    finally:
                        try:
                            entry.close()
                        except Exception:
                            pass
                    result.files_scanned += files
                    result.bytes_scanned += nbytes
                except Exception:
                    continue
        finally:
            rf.close()
    except Exception as e:
        _check_entry_name(filename, result.findings, seen=seen)
        result.error = f"RAR-Scan: {type(e).__name__}: {e}"
    return result


_SEVENZ_MAGIC = b"7z\xbc\xaf\x27\x1c"


def _is_safe_relative_member(name: str) -> bool:
    """True nur für Pfade ohne Traversal/absolute Komponenten (Zip-Slip-Schutz)."""
    raw = name.replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return False
    return ".." not in Path(raw).parts


def _scan_7z_bytes(data: bytes, filename: str) -> ScanResult:
    """
    7z hat (anders als zip/rar) in py7zr keine Streaming-Read-API — Einträge
    müssen extrahiert werden. Wir entpacken deshalb nur namentlich geprüfte,
    traversal-sichere Einträge in ein isoliertes Temp-Verzeichnis und scannen
    sie von dort, statt dem Archiv blind zu vertrauen.
    """
    result = ScanResult(filename=filename, archive_type="7z")
    seen: set[tuple[str, str]] = set()
    budget = [MAX_TOTAL_SCAN_BYTES]
    try:
        import py7zr  # type: ignore
    except ImportError:
        _check_entry_name(filename, result.findings, seen=seen)
        result.error = (
            "7Z-Inhaltsscan braucht Paket `py7zr` (pip install py7zr). "
            "Nur Dateiname geprüft."
        )
        return result

    import shutil
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="scan7z_")
    try:
        with py7zr.SevenZipFile(io.BytesIO(data), mode="r") as zf:
            infos = zf.list()
            result.entry_count = len(infos)
            if len(infos) > MAX_ENTRIES:
                _add_finding(
                    result.findings,
                    severity="medium",
                    path=filename,
                    reason=f"Sehr viele Einträge ({len(infos)}) — Zip-Bomb-Verdacht",
                    threat_name="Heur.Archive.ZipBomb",
                    seen=seen,
                )

            names_wanted: list[str] = []
            for info in infos[:MAX_ENTRIES]:
                name = getattr(info, "filename", "") or ""
                is_dir = bool(getattr(info, "is_directory", False))
                _check_entry_name(name, result.findings, seen=seen)
                if is_dir or not name:
                    continue

                uncomp = int(getattr(info, "uncompressed", 0) or 0)
                comp = int(getattr(info, "compressed", 0) or 0)
                if comp and comp > 4096 and uncomp > 0:
                    ratio = uncomp / comp
                    if ratio > 300:
                        _add_finding(
                            result.findings,
                            severity="high",
                            path=name,
                            reason=(
                                f"Extreme Kompressionsrate (1:{ratio:.0f}) "
                                "im Archiv-Eintrag"
                            ),
                            threat_name="Heur.Archive.CompressionBomb",
                            seen=seen,
                        )

                if not _is_safe_relative_member(name):
                    # Bereits als Pfad-Traversal geflaggt — nicht extrahieren.
                    continue
                if budget[0] > 0:
                    names_wanted.append(name)

            if names_wanted:
                try:
                    zf.reset()
                    zf.extract(path=tmpdir, targets=names_wanted)
                except Exception as e:
                    _add_finding(
                        result.findings,
                        severity="medium",
                        path=filename,
                        reason=f"7Z-Entpacken fehlgeschlagen: {type(e).__name__}",
                        threat_name="Heur.Scan.ExtractError",
                        seen=seen,
                    )
                    names_wanted = []

            root = Path(tmpdir).resolve()
            for name in names_wanted:
                if budget[0] <= 0:
                    _add_finding(
                        result.findings,
                        severity="medium",
                        path=filename,
                        reason="Scan-Budget erreicht — Rest übersprungen",
                        threat_name="Heur.Scan.BudgetCap",
                        seen=seen,
                    )
                    break

                fp = (Path(tmpdir) / name).resolve()
                # Zip-Slip-Endkontrolle: extrahierte Datei muss im Temp-Root bleiben.
                if root not in fp.parents and fp != root:
                    continue
                if not fp.is_file():
                    continue

                try:
                    with open(fp, "rb") as fh:
                        files, nbytes = _scan_stream(
                            name,
                            fh,
                            result.findings,
                            seen=seen,
                            nested_depth=0,
                            budget=budget,
                            size_hint=fp.stat().st_size,
                        )
                    result.files_scanned += files
                    result.bytes_scanned += nbytes
                except Exception:
                    continue
    except Exception as e:
        _check_entry_name(filename, result.findings, seen=seen)
        result.error = f"7Z-Scan: {type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return result


def scan_archive_bytes(data: bytes, filename: str) -> ScanResult:
    """Antivirus Deep-Scan: jede Datei hashen + Signaturen über den Inhalt."""
    started = time.perf_counter()

    if len(data) > MAX_ARCHIVE_BYTES:
        return ScanResult(
            filename=filename,
            archive_type="unknown",
            error=f"Datei zu groß für Scan (max. {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB)",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    name = (filename or "file").lower()
    outer_findings: list[Finding] = []
    outer_seen: set[tuple[str, str]] = set()
    _check_entry_name(Path(filename).name, outer_findings, seen=outer_seen)

    if data[:2] == b"MZ":
        _add_finding(
            outer_findings,
            severity="critical",
            path=filename,
            reason="Datei ist ein Windows-Executable, kein Archiv",
            threat_name="Heur.Disguise.FakeArchive",
            seen=outer_seen,
        )

    if name.endswith((".zip", ".jar", ".apk", ".war")) or data[:2] == b"PK":
        result = _scan_zip_bytes(data, filename)
    elif name.endswith(".rar") or data[:4] == b"Rar!":
        result = _scan_rar_bytes(data, filename)
    elif name.endswith(".7z") or data[:6] == _SEVENZ_MAGIC:
        result = _scan_7z_bytes(data, filename)
    else:
        result = ScanResult(filename=filename, archive_type="file")
        result.entry_count = 1
        files, nbytes = _scan_file_bytes(
            filename, data, result.findings, seen=outer_seen
        )
        result.files_scanned = files
        result.bytes_scanned = nbytes
        if not result.findings and not outer_findings:
            if Path(name).suffix in SINGLE_FILE_SCAN_EXTS:
                result.error = (
                    "Einzeldatei deep-gescannt (Hash, Signaturen, Entropie, "
                    "PE-Header). Für vollständige Pack-Prüfung idealerweise "
                    "als Archiv (.zip/.rar/.jar/.7z) hochladen."
                )
            else:
                result.error = (
                    "Kein ZIP/RAR/7Z — Einzeldatei deep-gescannt. "
                    "Für Packs bitte Archiv (.zip/.rar/.jar/.7z) verwenden."
                )

    for f in outer_findings:
        key = (f.path, f.threat_name or f.reason)
        existing = {(x.path, x.threat_name or x.reason) for x in result.findings}
        if key not in existing:
            result.findings.append(f)

    result.findings = _escalate_combined_findings(result.findings)

    result.duration_ms = int((time.perf_counter() - started) * 1000)
    result.engine = f"{ENGINE_NAME}/{ENGINE_VERSION}"
    return result


async def scan_archive_bytes_async(data: bytes, filename: str) -> ScanResult:
    """Deep-Scan im Thread-Pool (blockiert den Bot-Event-Loop nicht)."""
    import asyncio

    return await asyncio.to_thread(scan_archive_bytes, data, filename)


def scan_archive_path(path: Path) -> ScanResult:
    data = path.read_bytes()
    return scan_archive_bytes(data, path.name)


def is_scannable_filename(filename: str | None) -> bool:
    if not filename:
        return False
    suffix = Path(filename).suffix.lower()
    return suffix in ARCHIVE_EXTS or suffix in SINGLE_FILE_SCAN_EXTS
