"""Settings read from environment variables / .env. Every setting has a default."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PLACEHOLDER_KEYS = {"your_groq_key_here", "your-groq-key-here", "gsk_your_key_here"}


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def _str(name: str, default: str) -> str:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


class S:
    VERSION = "1.0.0"
    PROJECT_NAME = _str("PROJECT_NAME", "Real Time River Discharge Forecasting and Landslide Early Warning System Using FastAPI and Groq LLM")
    PROJECT_SLUG = _str("PROJECT_SLUG", "flood-landslide-risk")
    PROJECT_NUMBER = _int("PROJECT_NUMBER", 6)
    HOST = _str("HOST", "127.0.0.1")
    PORT = _int("PORT", 8006)
    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 8)
    DEMO_MAX_UPLOAD_MB = _int("DEMO_MAX_UPLOAD_MB", 3)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = _str("GROQ_MODEL", "openai/gpt-oss-120b")
    GROQ_FALLBACK_MODEL = _str("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
    DEMO_MODE = _bool("DEMO_MODE", False)
    DEMO_TICKET_SECRET = os.getenv("DEMO_TICKET_SECRET", "").strip()
    DEMO_DAILY_CAP = _int("DEMO_DAILY_CAP", 200)
    TRUST_PROXY = _bool("TRUST_PROXY", False)

    HOURLY_SCANS = 15
    FLOOD_URL = _str("OPEN_METEO_FLOOD_URL", "https://flood-api.open-meteo.com/v1/flood")
    FORECAST_URL = _str("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
    ELEVATION_URL = _str("OPEN_METEO_ELEVATION_URL", "https://api.open-meteo.com/v1/elevation")
    GEOCODING_URL = _str("OPEN_METEO_GEOCODING_URL", "https://geocoding-api.open-meteo.com/v1/search")

    DATA_DIR = ROOT / "data"
    DB_PATH = ROOT / "data" / "flood_risk.db"
    SAMPLES_DIR = ROOT / "samples"
    STATIC_DIR = ROOT / "app" / "static"
