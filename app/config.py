import os
from pathlib import Path
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
VAULT_ROOT = Path(os.getenv("NOVELHUB_VAULT_ROOT", "/root/ObsidianVault")).expanduser()
NOVELS_ROOT = VAULT_ROOT / "Novels"
BACKUP_ROOT = Path(os.getenv("NOVELHUB_BACKUP_ROOT", str(VAULT_ROOT / ".novelhub-backups"))).expanduser()
DB_PATH = Path(os.getenv("NOVELHUB_DB_PATH", str(BASE_DIR / "novelhub.db"))).expanduser()
ADMIN_PASSWORD = os.getenv("NOVELHUB_PASSWORD", "")
SECRET_KEY = os.getenv("NOVELHUB_SECRET_KEY", "change-me")
ENCRYPTION_KEY = os.getenv("NOVELHUB_ENCRYPTION_KEY", "")
APP_ENV = os.getenv("NOVELHUB_APP_ENV", "development").lower()
DAILY_GOAL_WORDS = int(os.getenv("NOVELHUB_DAILY_GOAL", "2000"))
PROJECT_GOAL_WORDS = int(os.getenv("NOVELHUB_PROJECT_GOAL", "100000"))


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


FEATURES = {
    "ai": env_bool("NOVELHUB_FEATURE_AI", False),
    "ai_check": env_bool("NOVELHUB_FEATURE_AI_CHECK", False),
    "graph": env_bool("NOVELHUB_FEATURE_GRAPH", False),
    "timeline": env_bool("NOVELHUB_FEATURE_TIMELINE", False),
    "scenes": env_bool("NOVELHUB_FEATURE_SCENES", False),
    "threads": env_bool("NOVELHUB_FEATURE_THREADS", False),
}


def feature_enabled(name: str) -> bool:
    return bool(FEATURES.get(name, False))


def require_feature(name: str) -> None:
    if not feature_enabled(name):
        raise HTTPException(status_code=404, detail=f"feature disabled: {name}")


def validate_runtime_config() -> None:
    if APP_ENV != "production":
        return
    missing = []
    if not ADMIN_PASSWORD:
        missing.append("NOVELHUB_PASSWORD")
    if not SECRET_KEY or SECRET_KEY == "change-me":
        missing.append("NOVELHUB_SECRET_KEY")
    if not ENCRYPTION_KEY:
        missing.append("NOVELHUB_ENCRYPTION_KEY")
    if missing:
        raise RuntimeError("Missing required production config: " + ", ".join(missing))
