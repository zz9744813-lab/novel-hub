import json
from pathlib import Path
from fastapi.templating import Jinja2Templates
from app.config import BASE_DIR, feature_enabled
from app.labels import status_label, kind_label


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
    templates.env.filters["from_json"] = lambda s: json.loads(s or "{}")
    templates.env.filters["basename"] = lambda s: Path(s).name if s else ""

    templates.env.globals["feature_enabled"] = feature_enabled
    templates.env.globals["status_label"] = status_label
    templates.env.globals["kind_label"] = kind_label
    return templates
