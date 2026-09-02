"""Move the exact failed production routes to release-qualified GLM-5.2.

Revision ID: 0025_glm52_route
Revises: 0024_production_schema

The provider catalog on 2026-09-02 exposed ``new-api/glm-5.2`` while the two
previous exact routes repeatedly failed the release gate.  This migration is
idempotent, preserves a ModelChangeLog row per affected binding, removes stale
allow/block constraints, and clears only retired fallbacks.
"""
from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa


revision = "0025_glm52_route"
down_revision = "0024_production_schema"
branch_labels = None
depends_on = None


TARGET_PROVIDER = "new-api"
TARGET_MODEL = "glm-5.2"
RETIRED_MODELS = (
    "deepseek-v4-flash",
    "deepseek-v4-flash-free",
    "stepfun-ai/step-3.7-flash",
)
MIGRATION_ACTOR = "release:0025_glm52_route"


def upgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    bindings = sa.Table(
        "agent_model_bindings",
        metadata,
        autoload_with=connection,
    )
    change_log = sa.Table(
        "model_change_log",
        metadata,
        autoload_with=connection,
    )

    affected = list(
        connection.execute(
            sa.select(bindings).where(
                sa.or_(
                    bindings.c.primary_model.in_(RETIRED_MODELS),
                    bindings.c.fallback_model.in_(RETIRED_MODELS),
                )
            )
        )
        .mappings()
        .all()
    )
    for row in affected:
        primary_changed = row["primary_model"] in RETIRED_MODELS
        fallback_changed = row["fallback_model"] in RETIRED_MODELS
        new_provider = TARGET_PROVIDER if primary_changed else row["provider"]
        new_primary = TARGET_MODEL if primary_changed else row["primary_model"]
        new_fallback = None if fallback_changed else row["fallback_model"]
        new_reasoning_mode = "disabled" if primary_changed else row["reasoning_mode"]

        reasons = []
        if primary_changed:
            reasons.append("replace release-gate-failed primary with glm-5.2")
        if fallback_changed:
            reasons.append("remove release-gate-failed fallback")
        connection.execute(
            change_log.insert().values(
                id=uuid.uuid4(),
                binding_id=row["id"],
                agent_role=row["agent_role"],
                old_provider=row["provider"],
                old_model=row["primary_model"],
                new_provider=new_provider,
                new_model=new_primary,
                old_reasoning_mode=row["reasoning_mode"],
                new_reasoning_mode=new_reasoning_mode,
                reason="; ".join(reasons),
                changed_by=MIGRATION_ACTOR,
            )
        )

        values = {
            "provider": new_provider,
            "primary_model": new_primary,
            "fallback_model": new_fallback,
            "reasoning_mode": new_reasoning_mode,
            "version": int(row["version"] or 0) + 1,
            "updated_by": MIGRATION_ACTOR,
            "updated_at": sa.func.now(),
        }
        if primary_changed:
            # Catalog UUID constraints from the retired route must not exclude
            # the newly qualified target during the next session preflight.
            values.update(
                allowed_model_ids=[],
                blocked_model_ids=[],
                manual_primary_locked=False,
                manual_fallback_locked=False,
            )
        connection.execute(
            bindings.update().where(bindings.c.id == row["id"]).values(**values)
        )


def downgrade() -> None:
    # Deliberately irreversible: restoring provider routes that failed the
    # production release gate would make a rollback less safe.  The exact old
    # values remain recoverable from model_change_log and database backups.
    pass
