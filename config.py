import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
DATABASE_PATH = DATA_DIR / "shop.db"

# Website-Shop API (optional)
SHOP_API_URL = os.getenv("SHOP_API_URL", "")
BOT_API_KEY = os.getenv("BOT_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Embed accent color (blue-ish, not purple)
EMBED_COLOR = 0x2B6CB0
EMBED_SUCCESS = 0x38A169
EMBED_ERROR = 0xE53E3E
EMBED_WARN = 0xD69E2E

DEFAULT_PAYEE = "TxtEmpire"
PAYMENT_NOTICE = "Das gesamte Geld geht an TxtEmpire."

# File-Scanner Limits
SCAN_FREE_DAILY = int(os.getenv("SCAN_FREE_DAILY", "1") or "1")
SCAN_PREMIUM_DAILY = int(os.getenv("SCAN_PREMIUM_DAILY", "15") or "15")
# Preise für Scan-Premium (Shop-Währung, 1 Credit = 100k)
SCAN_PREMIUM_14_PRICE = float(os.getenv("SCAN_PREMIUM_14_PRICE", "500000") or "500000")
SCAN_PREMIUM_30_PRICE = float(os.getenv("SCAN_PREMIUM_30_PRICE", "900000") or "900000")
# Credits-Preis (leer = aus Währungspreis / 100k ableiten)
_SCAN_14_CREDITS = os.getenv("SCAN_PREMIUM_14_CREDITS", "").strip()
_SCAN_30_CREDITS = os.getenv("SCAN_PREMIUM_30_CREDITS", "").strip()
SCAN_PREMIUM_14_CREDITS = (
    float(_SCAN_14_CREDITS) if _SCAN_14_CREDITS else SCAN_PREMIUM_14_PRICE / 100_000
)
SCAN_PREMIUM_30_CREDITS = (
    float(_SCAN_30_CREDITS) if _SCAN_30_CREDITS else SCAN_PREMIUM_30_PRICE / 100_000
)

# Server-Boost Belohnungen (Pack-Auswahl)
BOOST_PACKS_TIER1 = int(os.getenv("BOOST_PACKS_TIER1", "5") or "5")
BOOST_PACKS_TIER2 = int(os.getenv("BOOST_PACKS_TIER2", "15") or "15")

# Payback / Daily XP
PAYBACK_DAILY_XP = int(os.getenv("PAYBACK_DAILY_XP", "10") or "10")
PAYBACK_REWARD_XP = int(os.getenv("PAYBACK_REWARD_XP", "100") or "100")
# Kunden (Customer-Rolle) bekommen X% mehr Daily-XP
PAYBACK_CUSTOMER_BONUS_PCT = int(
    os.getenv("PAYBACK_CUSTOMER_BONUS_PCT", "20") or "20"
)
# 500k Shop-Guthaben = 5 Credits (1 Credit = 100k)
PAYBACK_REWARD_CURRENCY = float(
    os.getenv("PAYBACK_REWARD_CURRENCY", "500000") or "500000"
)

# Creator-Code Provision (% vom Bestellpreis vor Code-Rabatt), Stats resetten monatlich
CREATOR_COMMISSION_PCT = float(
    os.getenv("CREATOR_COMMISSION_PCT", "10") or "10"
)

# Invite-Rewards: Meilenstein → Shop-Währung (→ Credits / 100k)
# 5→500k, 10→1.5m, 25→5m, 100→25m
INVITE_REWARDS: tuple[tuple[int, float], ...] = (
    (5, 500_000.0),
    (10, 1_500_000.0),
    (25, 5_000_000.0),
    (100, 25_000_000.0),
)

# Minecraft Account-Link + Chat-Watcher Mod API
# Bot-Hosting/Pterodactyl setzt SERVER_PORT (= freigegebener Port, z.B. 26026).
# Der Prozess MUSS genau auf SERVER_PORT lauschen, sonst ist er von außen unerreichbar.
MC_API_HOST = os.getenv("MC_API_HOST", "0.0.0.0")
if os.getenv("SERVER_PORT"):
    MC_API_PORT = int(os.getenv("SERVER_PORT") or "8765")
else:
    MC_API_PORT = int(os.getenv("MC_API_PORT") or "8765")
MC_API_KEY = os.getenv("MC_API_KEY", "").strip()
MC_LINK_CODE_TTL_MINUTES = int(os.getenv("MC_LINK_CODE_TTL_MINUTES", "10") or "10")
# Ingame-Account, dem User den Link-Code per /msg schicken
MC_LINK_IGN = (os.getenv("MC_LINK_IGN", "TxTEmpire") or "TxTEmpire").strip()


def mc_link_command(code: str) -> str:
    """Ingame-Befehl zum Verlinken (Private Message)."""
    return f"/msg {MC_LINK_IGN} !link {code.strip().upper()}"


