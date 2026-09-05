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
