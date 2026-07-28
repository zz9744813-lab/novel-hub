"""Prompt Studio API skeleton (v8.0 Phase 5 partial)."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tables import PromptTemplateVersion, PromptTestRun
from app.prompts import PROMPTS, AGENT_MODELS

router = APIRouter(prefix="/api/prompt-studio", tags=["prompt-studio"])


def gen_uuid():
    return uuid.uuid4()


def _hash_template(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n---\n{user}".encode()).hexdigest()


def _vars_in_template(text: str) -> set[str]:
    return set(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", text or ""))


@router.get("/agents")
async def list_agents():
    roles = sorted(set(list(AGENT_MODELS.keys()) + list(PROMPTS.keys())))
    items = []
    for r in roles:
        items.append(
            {
                "agent_role": r,
                "label": r,
                "has_prompt": r in PROMPTS,
                "default_model": AGENT_MODELS.get(r),
            }
        )
    return {"agents": items}


@router.get("/templates")
async def list_templates(db: AsyncSession = Depends(get_db), agent_role: str | None = None):
    q = select(PromptTemplateVersion).order_by(
        PromptTemplateVersion.agent_role, PromptTemplateVersion.version.desc()
    )
    if agent_role:
        q = q.where(PromptTemplateVersion.agent_role == agent_role)
    rows = (await db.execute(q.limit(200))).scalars().all()
    return {
        "templates": [
            {
                "id": str(t.id),
                "template_key": t.template_key,
                "agent_role": t.agent_role,
                "version": t.version,
                "status": t.status,
                "name": t.name,
                "scope_type": t.scope_type,
                "last_test_passed": t.last_test_passed,
                "template_hash": t.template_hash,
            }
            for t in rows
        ]
    }


class TemplateCreate(BaseModel):
    agent_role: str
    name: str
    system_prompt: str = ""
    user_prompt_template: str = ""
    scope_type: str = "system"
    scope_id: str | None = None
    input_contract_key: str | None = None
    output_contract_key: str | None = None
    allowed_context_kinds: list[str] = Field(default_factory=list)
    required_context_kinds: list[str] = Field(default_factory=list)
    forbidden_context_kinds: list[str] = Field(default_factory=list)
    description: str | None = None


@router.post("/templates")
async def create_template(body: TemplateCreate, db: AsyncSession = Depends(get_db)):
    key = f"{body.agent_role}:{body.scope_type}:{body.scope_id or 'global'}"
    # next version
    existing = (
        await db.execute(
            select(PromptTemplateVersion)
            .where(
                PromptTemplateVersion.template_key == key,
                PromptTemplateVersion.scope_type == body.scope_type,
            )
            .order_by(PromptTemplateVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ver = (existing.version + 1) if existing else 1
    vars_found = sorted(_vars_in_template(body.system_prompt) | _vars_in_template(body.user_prompt_template))
    t = PromptTemplateVersion(
        id=gen_uuid(),
        template_key=key,
        agent_role=body.agent_role,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        version=ver,
        status="draft",
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        user_prompt_template=body.user_prompt_template,
        input_contract_key=body.input_contract_key,
        output_contract_key=body.output_contract_key,
        allowed_context_kinds=body.allowed_context_kinds,
        required_context_kinds=body.required_context_kinds,
        forbidden_context_kinds=body.forbidden_context_kinds,
        variables=vars_found,
        template_hash=_hash_template(body.system_prompt, body.user_prompt_template),
        created_by="admin",
    )
    db.add(t)
    await db.commit()
    return {"id": str(t.id), "version": ver, "status": "draft", "variables": vars_found}


@router.get("/templates/{template_id}")
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)):
    t = (
        await db.execute(
            select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(template_id))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "not found")
    return {
        "id": str(t.id),
        "template_key": t.template_key,
        "agent_role": t.agent_role,
        "version": t.version,
        "status": t.status,
        "name": t.name,
        "description": t.description,
        "system_prompt": t.system_prompt,
        "user_prompt_template": t.user_prompt_template,
        "input_contract_key": t.input_contract_key,
        "output_contract_key": t.output_contract_key,
        "allowed_context_kinds": t.allowed_context_kinds,
        "required_context_kinds": t.required_context_kinds,
        "forbidden_context_kinds": t.forbidden_context_kinds,
        "variables": t.variables,
        "template_hash": t.template_hash,
        "last_test_passed": t.last_test_passed,
    }


@router.get("/templates/{template_id}/compatibility")
async def compatibility(template_id: str, db: AsyncSession = Depends(get_db)):
    t = (
        await db.execute(
            select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(template_id))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "not found")
    errors: list[str] = []
    if t.agent_role not in AGENT_MODELS and t.agent_role not in PROMPTS:
        errors.append(f"unknown agent_role: {t.agent_role}")
    req = set(t.required_context_kinds or [])
    forb = set(t.forbidden_context_kinds or [])
    if req & forb:
        errors.append(f"required∩forbidden context: {sorted(req & forb)}")
    # unknown jinja-like vars without declared variables list is ok if we list them
    vars_found = _vars_in_template(t.system_prompt) | _vars_in_template(t.user_prompt_template)
    declared = set(t.variables or [])
    unknown = vars_found - declared
    if unknown:
        errors.append(f"undeclared variables: {sorted(unknown)}")
    if t.status == "active" and t.last_test_passed is False:
        errors.append("last test failed — cannot keep active without retest")
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "variables_found": sorted(vars_found),
        "can_activate": len(errors) == 0 and t.last_test_passed is not False,
    }


@router.post("/templates/{template_id}/test")
async def test_template(template_id: str, db: AsyncSession = Depends(get_db)):
    """Dry-run: no LLM, only compile + contract structural check. Does not write novel data."""
    t = (
        await db.execute(
            select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(template_id))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "not found")
    compat = await compatibility(template_id, db)
    ok = bool(compat["ok"])
    run = PromptTestRun(
        id=gen_uuid(),
        template_version_id=t.id,
        fixture_name="structural_dry_run",
        model=None,
        provider=None,
        input_json={"note": "no LLM in phase5 skeleton"},
        output_json={"compatibility": compat},
        contract_ok=ok,
        leak_ok=True,
        latency_ms=0,
        error=None if ok else "; ".join(compat["errors"]),
        status="completed",
    )
    db.add(run)
    t.last_test_passed = ok
    await db.commit()
    return {"test_run_id": str(run.id), "passed": ok, "compatibility": compat}


@router.post("/templates/{template_id}/activate")
async def activate_template(template_id: str, db: AsyncSession = Depends(get_db)):
    t = (
        await db.execute(
            select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(template_id))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "not found")
    compat = await compatibility(template_id, db)
    if not compat["ok"]:
        raise HTTPException(400, detail={"code": "INCOMPATIBLE", "errors": compat["errors"]})
    if t.last_test_passed is not True:
        raise HTTPException(400, detail={"code": "TEST_REQUIRED", "message": "run test before activate"})
    # archive previous active same key
    prevs = (
        await db.execute(
            select(PromptTemplateVersion).where(
                PromptTemplateVersion.template_key == t.template_key,
                PromptTemplateVersion.status == "active",
            )
        )
    ).scalars().all()
    for p in prevs:
        p.status = "archived"
    t.status = "active"
    t.activated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": str(t.id), "status": "active", "version": t.version}


@router.get("/templates/{template_id}/compiled-preview")
async def compiled_preview(template_id: str, db: AsyncSession = Depends(get_db)):
    t = (
        await db.execute(
            select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(template_id))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "not found")
    sample_vars = {v: f"<{v}>" for v in (t.variables or [])}
    sys_out = t.system_prompt
    user_out = t.user_prompt_template
    for k, v in sample_vars.items():
        sys_out = sys_out.replace("{{" + k + "}}", v).replace("{{ " + k + " }}", v)
        user_out = user_out.replace("{{" + k + "}}", v).replace("{{ " + k + " }}", v)
    return {
        "system": sys_out,
        "user": user_out,
        "variables": sample_vars,
        "template_hash": t.template_hash,
    }
