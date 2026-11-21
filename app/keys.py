"""Groq key resolution: saved key first (local mode only), then the environment."""
import re

from . import db
from .config import PLACEHOLDER_KEYS, S


def is_usable_key(k) -> bool:
    if not k or not isinstance(k, str):
        return False
    if k in PLACEHOLDER_KEYS or re.search(r"\s", k):
        return False
    return k.startswith("gsk_") and len(k) > 8


def resolve_key():
    """Return (key, source) where source is 'ui', 'env' or 'none'."""
    if not S.DEMO_MODE:
        try:
            saved = db.get_saved_key()
        except Exception:
            saved = ""
        if is_usable_key(saved):
            return saved, "ui"
    if is_usable_key(S.GROQ_API_KEY):
        return S.GROQ_API_KEY, "env"
    return None, "none"


def mask(k: str) -> str:
    return f"gsk_...{k[-4:]}" if k else ""
