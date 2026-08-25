"""v9.7 PromptResolver (spec §6): single prompt resolution entry point.

Priority: Book-scoped active > Genre-scoped active (legacy compat) >
Global active > built-in PROMPTS. The database enforces one active per
agent_role + scope_type + scope_id via partial unique index.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptTemplateVersion


@dataclass(frozen=True)
class ResolvedPrompt:
    template_id: uuid.UUID
    template_key: str
    version: int
    system_prompt: str
    user_prompt_template: str
    output_schema: dict | None = None
    scope_type: str = "global"


async def resolve_prompt(
    db: AsyncSession,
    *,
    agent_role: str,
    book_id: uuid.UUID | None = None,
    genre_id: uuid.UUID | None = None,
) -> ResolvedPrompt | None:
    """Resolve the active prompt for an agent (Book > Genre > Global)."""
    attempts = []
    if book_id is not None:
        attempts.append(("book", str(book_id)))
        attempts.append(("system", str(book_id)))  # legacy scope_type for book rows
    if genre_id is not None:
        attempts.append(("genre", str(genre_id)))
    attempts.append(("global", None))
    attempts.append(("system", None))  # legacy global rows

    for scope_type, scope_id in attempts:
        row = (
            await db.execute(
                select(PromptTemplateVersion)
                .where(
                    PromptTemplateVersion.agent_role == agent_role,
                    PromptTemplateVersion.scope_type == scope_type,
                    # v9.7 §7.6: a running canary version takes precedence over active
                    PromptTemplateVersion.canary_status == "running",
                )
                .order_by(PromptTemplateVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            row = (
                await db.execute(
                    select(PromptTemplateVersion)
                    .where(
                        PromptTemplateVersion.agent_role == agent_role,
                        PromptTemplateVersion.scope_type == scope_type,
                        PromptTemplateVersion.status == "active",
                        PromptTemplateVersion.activated_at.isnot(None),
                    )
                    .order_by(PromptTemplateVersion.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is not None:
            if scope_type == "global" and scope_id is None:
                pass
            else:
                pass
            return ResolvedPrompt(
                template_id=row.id,
                template_key=row.template_key,
                version=row.version,
                system_prompt=row.system_prompt or "",
                user_prompt_template=row.user_prompt_template or "",
                output_schema=getattr(row, "output_schema", None) or getattr(row, "compiled_schema", None),
                scope_type=scope_type,
            )
    return None


async def resolve_prompt_with_builtin(
    db: AsyncSession,
    *,
    agent_role: str,
    book_id: uuid.UUID | None = None,
    genre_id: uuid.UUID | None = None,
    builtin: dict | None = None,
) -> ResolvedPrompt | None:
    """Resolver + built-in fallback used by the caller."""
    resolved = await resolve_prompt(db, agent_role=agent_role, book_id=book_id, genre_id=genre_id)
    if resolved is not None:
        return resolved
    if builtin:
        return ResolvedPrompt(
            template_id=uuid.uuid4(),
            template_key=f"builtin:{agent_role}",
            version=1,
            system_prompt=builtin.get("system_prompt", ""),
            user_prompt_template=builtin.get("user_prompt_template", "{{user_content}}"),
            output_schema=builtin.get("output_schema"),
            scope_type="builtin",
        )
    return None
