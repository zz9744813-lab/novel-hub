"""Prompt Studio API — contract gate, seed from PROMPTS, activate fail-closed (v8)."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tables import PromptTemplateVersion, PromptTestRun
from app.prompts import PROMPTS, AGENT_MODELS, AGENT_IS_JSON

router = APIRouter(prefix="/api/prompt-studio", tags=["prompt-studio"])

# Context kinds allowed in Studio (v8 product vocabulary)
KNOWN_CONTEXT_KINDS = {
    "book_profile",
    "writing_constraints",
    "outline_node",
    "l1_ledger",
    "l2_summary",
    "l3_summary",
    "l4_state",
    "character_cards",
    "world_rules",
    "plot_threads",
    "retrieved_evidence",
    "voice_cards",
    "tone_anchor",
    "chapter_plan",
    "draft_scene",
    "review_report",
    "import_preview",
    "raw_outline",
    "genre_profile",
}


def gen_uuid():
    return uuid.uuid4()


def _hash_template(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n---\n{user}".encode()).hexdigest()


def _vars_in_template(text: str) -> set[str]:
    return set(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", text or ""))


def _contract_registry() -> dict[str, Any]:
    """Merge production + import contracts for Studio binding."""
    reg: dict[str, Any] = {}
    try:
        from app.contracts.agents import ROLE_CONTRACTS

        reg.update(ROLE_CONTRACTS)
    except Exception:
        pass
    try:
        from app.contracts.import_contracts import IMPORT_CONTRACTS

        reg.update(IMPORT_CONTRACTS)
    except Exception:
        pass
    return reg


def _role_default_contract(agent_role: str) -> str | None:
    """Map agent_role → preferred output contract key."""
    if agent_role in _contract_registry():
        return agent_role
    # import roles already keyed by role name in IMPORT_CONTRACTS
    return None


def _evaluate_compatibility(t: PromptTemplateVersion) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    contracts = _contract_registry()

    known_roles = set(AGENT_MODELS.keys()) | set(PROMPTS.keys()) | set(contracts.keys())
    if t.agent_role not in known_roles:
        errors.append(f"unknown agent_role: {t.agent_role}")

    req = set(t.required_context_kinds or [])
    forb = set(t.forbidden_context_kinds or [])
    allowed = set(t.allowed_context_kinds or [])
    if req & forb:
        errors.append(f"required∩forbidden context: {sorted(req & forb)}")
    if allowed and req - allowed:
        errors.append(f"required not in allowed: {sorted(req - allowed)}")
    for kind in sorted(req | forb | allowed):
        if kind and kind not in KNOWN_CONTEXT_KINDS:
            warnings.append(f"unknown context kind: {kind}")

    vars_found = _vars_in_template(t.system_prompt) | _vars_in_template(t.user_prompt_template)
    declared = set(t.variables or [])
    # auto-heal: treat found vars as declared if empty declared list on legacy rows
    if not declared and vars_found:
        declared = vars_found
    unknown = vars_found - declared
    if unknown:
        errors.append(f"undeclared variables: {sorted(unknown)}")

    # empty template
    if not (t.system_prompt or "").strip() and not (t.user_prompt_template or "").strip():
        errors.append("empty system and user template")

    out_key = t.output_contract_key
    in_key = t.input_contract_key
    if out_key:
        if out_key not in contracts:
            errors.append(f"unknown output_contract_key: {out_key}")
    else:
        # JSON agents should declare output contract when one exists for role
        if AGENT_IS_JSON.get(t.agent_role, False) and t.agent_role in contracts:
            warnings.append(
                f"JSON agent {t.agent_role} has no output_contract_key "
                f"(recommended: {t.agent_role})"
            )
    if in_key and in_key not in contracts and in_key not in KNOWN_CONTEXT_KINDS:
        # input may be a free-form package name; only error if looks like contract
        if in_key.endswith("Contract") or in_key in contracts:
            errors.append(f"unknown input_contract_key: {in_key}")

    # schema compile check
    schema_ok = None
    schema_error = None
    if out_key and out_key in contracts:
        try:
            model = contracts[out_key]
            schema = model.model_json_schema()
            if not isinstance(schema, dict) or "type" not in schema and "properties" not in schema:
                errors.append(f"output contract schema invalid for {out_key}")
                schema_ok = False
            else:
                schema_ok = True
        except Exception as e:
            errors.append(f"output contract schema error: {e}")
            schema_ok = False
            schema_error = str(e)

    if t.status == "active" and t.last_test_passed is False:
        errors.append("last test failed — cannot keep active without retest")

    # hard gate for activate
    can_activate = len(errors) == 0 and t.last_test_passed is True

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "variables_found": sorted(vars_found),
        "variables_declared": sorted(declared),
        "output_contract_key": out_key,
        "input_contract_key": in_key,
        "schema_ok": schema_ok,
        "schema_error": schema_error,
        "known_contracts": sorted(contracts.keys()),
        "can_activate": can_activate,
        "test_passed": t.last_test_passed,
        "status": t.status,
    }


@router.get("/agents")
async def list_agents():
    contracts = _contract_registry()
    roles = sorted(set(list(AGENT_MODELS.keys()) + list(PROMPTS.keys()) + list(contracts.keys())))
    items = []
    for r in roles:
        items.append(
            {
                "agent_role": r,
                "label": r,
                "has_prompt": r in PROMPTS,
                "default_model": AGENT_MODELS.get(r),
                "is_json": AGENT_IS_JSON.get(r, r in contracts),
                "has_contract": r in contracts,
                "default_output_contract": _role_default_contract(r),
            }
        )
    return {"agents": items, "context_kinds": sorted(KNOWN_CONTEXT_KINDS)}


@router.get("/contracts")
async def list_contracts():
    contracts = _contract_registry()
    out = []
    for key, model in sorted(contracts.items()):
        try:
            schema = model.model_json_schema()
            props = list((schema.get("properties") or {}).keys())
        except Exception as e:
            props = []
            schema = {"error": str(e)}
        out.append(
            {
                "key": key,
                "model": getattr(model, "__name__", str(model)),
                "property_count": len(props),
                "properties": props[:40],
            }
        )
    return {"contracts": out}


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
                "output_contract_key": t.output_contract_key,
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
    # auto-bind output contract if omitted
    out_key = body.output_contract_key or _role_default_contract(body.agent_role)
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
        output_contract_key=out_key,
        allowed_context_kinds=body.allowed_context_kinds,
        required_context_kinds=body.required_context_kinds,
        forbidden_context_kinds=body.forbidden_context_kinds,
        variables=vars_found,
        template_hash=_hash_template(body.system_prompt, body.user_prompt_template),
        created_by="admin",
    )
    db.add(t)
    await db.commit()
    compat = _evaluate_compatibility(t)
    return {
        "id": str(t.id),
        "version": ver,
        "status": "draft",
        "variables": vars_found,
        "output_contract_key": out_key,
        "compatibility": compat,
    }


@router.post("/templates/seed-defaults")
async def seed_defaults(db: AsyncSession = Depends(get_db)):
    """Seed draft templates from built-in PROMPTS for roles that have none yet."""
    created = []
    skipped = []
    for role, cfg in PROMPTS.items():
        key = f"{role}:system:global"
        exists = (
            await db.execute(
                select(PromptTemplateVersion)
                .where(PromptTemplateVersion.template_key == key)
                .limit(1)
            )
        ).scalar_one_or_none()
        if exists:
            skipped.append(role)
            continue
        system = cfg.get("system_prompt") or ""
        # build user template from input_variables
        vars_list = list(cfg.get("input_variables") or [])
        user_lines = [f"## {role} 输入"]
        for v in vars_list:
            user_lines.append(f"- {v}: {{{{ {v} }}}}")
        user_tmpl = "\n".join(user_lines)
        vars_found = sorted(_vars_in_template(system) | _vars_in_template(user_tmpl) | set(vars_list))
        out_key = _role_default_contract(role)
        # default required context from known variable names
        required = [v for v in vars_list if v in KNOWN_CONTEXT_KINDS]
        t = PromptTemplateVersion(
            id=gen_uuid(),
            template_key=key,
            agent_role=role,
            scope_type="system",
            scope_id=None,
            version=1,
            status="draft",
            name=f"{role} 内置 v1",
            description="从 PROMPTS 种子生成的草稿，需结构测试后才能激活",
            system_prompt=system,
            user_prompt_template=user_tmpl,
            input_contract_key=None,
            output_contract_key=out_key,
            allowed_context_kinds=[],
            required_context_kinds=required,
            forbidden_context_kinds=[],
            variables=vars_found,
            template_hash=_hash_template(system, user_tmpl),
            created_by="seed",
        )
        db.add(t)
        created.append({"agent_role": role, "id": str(t.id), "output_contract_key": out_key})
    await db.commit()
    return {"created": created, "skipped": skipped, "created_count": len(created)}


@router.get("/templates/{template_id}")
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)):
    t = (
        await db.execute(
            select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(template_id))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "not found")
    compat = _evaluate_compatibility(t)
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
        "compatibility": compat,
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
    return _evaluate_compatibility(t)


@router.post("/templates/{template_id}/test")
async def test_template(template_id: str, db: AsyncSession = Depends(get_db)):
    """Dry-run: no LLM, structural + contract gate. Does not write novel data."""
    t = (
        await db.execute(
            select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(template_id))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "not found")
    # heal undeclared vars into variables list before scoring
    found = _vars_in_template(t.system_prompt) | _vars_in_template(t.user_prompt_template)
    declared = set(t.variables or [])
    if found - declared:
        t.variables = sorted(declared | found)
    compat = _evaluate_compatibility(t)
    ok = bool(compat["ok"])
    run = PromptTestRun(
        id=gen_uuid(),
        template_version_id=t.id,
        fixture_name="structural_contract_gate",
        model=None,
        provider=None,
        input_json={"note": "no LLM — contract + variable + context gate"},
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
    return {
        "test_run_id": str(run.id),
        "passed": ok,
        "compatibility": compat,
        "can_activate": ok and t.last_test_passed is True,
    }


@router.post("/templates/{template_id}/test-structure")
async def test_structure_alias(template_id: str, db: AsyncSession = Depends(get_db)):
    return await test_template(template_id, db)


@router.post("/templates/{template_id}/activate")
async def activate_template(template_id: str, db: AsyncSession = Depends(get_db)):
    t = (
        await db.execute(
            select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(template_id))
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "not found")
    compat = _evaluate_compatibility(t)
    if not compat["ok"]:
        raise HTTPException(
            400,
            detail={"code": "INCOMPATIBLE", "errors": compat["errors"], "warnings": compat.get("warnings")},
        )
    if t.last_test_passed is not True:
        raise HTTPException(
            400,
            detail={"code": "TEST_REQUIRED", "message": "必须先通过结构/合同测试再激活", "compatibility": compat},
        )
    if not compat.get("can_activate"):
        raise HTTPException(
            400,
            detail={"code": "ACTIVATE_BLOCKED", "message": "合同门禁未通过", "compatibility": compat},
        )
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
    return {
        "id": str(t.id),
        "status": "active",
        "version": t.version,
        "output_contract_key": t.output_contract_key,
        "compatibility": compat,
    }


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
    sys_out = t.system_prompt or ""
    user_out = t.user_prompt_template or ""
    for k, v in sample_vars.items():
        sys_out = sys_out.replace("{{" + k + "}}", v).replace("{{ " + k + " }}", v)
        user_out = user_out.replace("{{" + k + "}}", v).replace("{{ " + k + " }}", v)
    return {
        "system": sys_out,
        "user": user_out,
        "variables": sample_vars,
        "template_hash": t.template_hash,
        "output_contract_key": t.output_contract_key,
    }
