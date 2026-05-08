import sqlite3
import base64
import hashlib
from cryptography.fernet import Fernet
from app.config import DB_PATH, ENCRYPTION_KEY, SECRET_KEY


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )


def _settings_fernet() -> Fernet:
    raw = ENCRYPTION_KEY or SECRET_KEY
    try:
        return Fernet(raw.encode("utf-8"))
    except Exception:
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        return Fernet(key)


def set_setting_encrypted(key: str, value: str) -> None:
    if not value:
        return
    token = _settings_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    set_setting(key, f"enc::{token}")


def clear_setting(key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def get_setting_decrypted(key: str, default: str = "") -> str:
    value = get_setting(key, default)
    if not value:
        return default
    if not value.startswith("enc::"):
        # Backward compatibility: migrate old plaintext settings on first read.
        set_setting_encrypted(key, value)
        return value
    try:
        return _settings_fernet().decrypt(value[5:].encode("utf-8")).decode("utf-8")
    except Exception:
        return default
