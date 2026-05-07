"""
Workflow stage definitions and status calculation.

The fixed order is: premise -> worldview -> characters -> outline ->
chapter_outline -> writing. Each stage resolves to todo / in_progress / done.
"""
from __future__ import annotations

from pathlib import Path


STAGES: list[tuple[str, str, str]] = [
    ("premise", "立意", "确定题材与核心创意"),
    ("worldview", "世界观", "建立世界规则与背景"),
    ("characters", "人物", "建立人物档案"),
    ("outline", "总纲", "拆出卷与主线"),
    ("chapter_outline", "细纲", "为每章写大意"),
    ("writing", "写作", "动笔写正文"),
]

STAGE_KEYS: list[str] = [s[0] for s in STAGES]
STAGE_LABELS: dict[str, str] = {s[0]: s[1] for s in STAGES}
STAGE_HINTS: dict[str, str] = {s[0]: s[2] for s in STAGES}


def is_valid_stage(stage: str) -> bool:
    return stage in STAGE_KEYS


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


def workflow_dir(novels_root: Path, project: str) -> Path:
    """Stage content storage directory: Vault/Novels/{project}/.workflow/."""
    return novels_root / project / ".workflow"


def workflow_file(novels_root: Path, project: str, stage: str) -> Path:
    """Markdown file path for content stages such as premise/worldview."""
    return workflow_dir(novels_root, project) / f"{stage}.md"


def has_premise_content(novels_root: Path, project: str) -> bool:
    f = workflow_file(novels_root, project, "premise")
    return f.exists() and len(f.read_text(encoding="utf-8").strip()) > 0


def has_worldview_content(novels_root: Path, project: str) -> bool:
    f = workflow_file(novels_root, project, "worldview")
    return f.exists() and len(f.read_text(encoding="utf-8").strip()) > 0


def compute_stage_status(
    stage: str,
    project: str,
    novels_root: Path,
    db_conn,
    done_marker: bool,
) -> str:
    """Return todo / in_progress / done for one project stage."""
    if done_marker:
        return "done"

    if stage == "premise":
        return "in_progress" if has_premise_content(novels_root, project) else "todo"
    if stage == "worldview":
        return "in_progress" if has_worldview_content(novels_root, project) else "todo"
    if stage == "characters":
        n = db_conn.execute(
            "SELECT COUNT(*) FROM entities WHERE project=? AND kind='character'",
            (project,),
        ).fetchone()[0]
        return "in_progress" if n >= 1 else "todo"
    if stage == "outline":
        n = db_conn.execute(
            "SELECT COUNT(*) FROM volumes WHERE project=?",
            (project,),
        ).fetchone()[0]
        return "in_progress" if n >= 1 else "todo"
    if stage == "chapter_outline":
        n = db_conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE project=? AND synopsis IS NOT NULL AND length(synopsis) > 0",
            (project,),
        ).fetchone()[0]
        return "in_progress" if n >= 1 else "todo"
    if stage == "writing":
        n = db_conn.execute(
            "SELECT COUNT(*) FROM file_index WHERE project=? AND status IN ('draft','rewrite','polish','done','published') AND word_count > 50",
            (project,),
        ).fetchone()[0]
        return "in_progress" if n >= 1 else "todo"

    return "todo"


def next_actionable_stage(
    project: str,
    novels_root: Path,
    db_conn,
    done_lookup,
) -> tuple[str, str]:
    """Return the first non-done stage, or writing/continue when all are done."""
    for stage in STAGE_KEYS:
        st = compute_stage_status(
            stage,
            project,
            novels_root,
            db_conn,
            done_lookup(stage),
        )
        if st != "done":
            return stage, stage_label(stage)
    return "writing", "继续写作"
