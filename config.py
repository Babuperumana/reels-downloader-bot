import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Load .env file
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "856833667").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None

DOWNLOAD_DIR = BASE_DIR / os.getenv("DOWNLOAD_DIR", "downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILESIZE_MB = int(os.getenv("MAX_FILESIZE_MB", "50"))
MAX_FILESIZE_BYTES = MAX_FILESIZE_MB * 1024 * 1024

COOKIES_FILE = BASE_DIR / "cookies.txt"
HAS_COOKIES = COOKIES_FILE.exists()

AUTO_CLEANUP_HOURS = int(os.getenv("AUTO_CLEANUP_HOURS", "6"))

# Watermark Settings
WATERMARK_FILE = BASE_DIR / "assets" / "watermark.png"
WATERMARK_ENABLED = os.getenv("WATERMARK_ENABLED", "True").lower() == "true"
WATERMARK_POSITION = os.getenv("WATERMARK_POSITION", "center")
WATERMARK_OPACITY = float(os.getenv("WATERMARK_OPACITY", "0.75"))
WATERMARK_WIDTH_RATIO = float(os.getenv("WATERMARK_WIDTH_RATIO", "0.45"))

