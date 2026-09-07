"""Replace the unavailable exact GLM-5.2 routes, retaining binding audit.

Revision ID: 0026_glm53_flash_route
Revises: 0025_glm52_route
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0026_glm53_flash_route"
down_revision = "0025_glm52_route"
branch_labels = None
depends_on = None
RETIRED = ("glm-5.2", "z-ai/glm-5.2")
RETIRED_PROVIDERS = ("new-api", "primary")


def upgrade() -> None:
    conn = op.get_bind()
    metadata = sa.MetaData()
    bindings = sa.Table("agent_model_bindings", metadata, autoload_with=conn)
    audit = sa.Table("model_change_log", metadata, autoload_with=conn)
    # The observed retirement belongs to our gateway, not every provider
    # serving the same model identifier. Preserve unrelated provider routes.
    rows = conn.execute(sa.select(bindings).where(
        bindings.c.provider.in_(RETIRED_PROVIDERS),
        sa.or_(bindings.c.primary_model.in_(RETIRED),
               bindings.c.fallback_model.in_(RETIRED)),
    )).mappings().all()
    for row in rows:
        primary_changed = row["primary_model"] in RETIRED
        fallback_changed = row["fallback_model"] in RETIRED
        provider = "new-api" if primary_changed else row["provider"]
        model = "glm-5.3-flash" if primary_changed else row["primary_model"]
        mode = "auto" if primary_changed else row["reasoning_mode"]
        conn.execute(audit.insert().values(
            id=uuid.uuid4(), binding_id=row["id"], agent_role=row["agent_role"],
            old_provider=row["provider"], old_model=row["primary_model"],
            new_provider=provider, new_model=model,
            old_reasoning_mode=row["reasoning_mode"], new_reasoning_mode=mode,
            reason=(
                "retired gateway GLM-5.2 returned model_not_found; "
                "release-gate GLM-5.3-Flash; "
                f"fallback: {row['fallback_model']!r} -> "
                f"{None if fallback_changed else row['fallback_model']!r}"
            ),
            changed_by="release:0026_glm53_flash_route",
        ))
        values = dict(
            provider=provider, primary_model=model, reasoning_mode=mode,
            fallback_model=None if fallback_changed else row["fallback_model"],
            version=int(row["version"] or 0) + 1,
            updated_by="release:0026_glm53_flash_route", updated_at=sa.func.now(),
        )
        if primary_changed:
            values.update(allowed_model_ids=[], blocked_model_ids=[],
                          manual_primary_locked=False)
        if fallback_changed:
            values["manual_fallback_locked"] = False
        conn.execute(bindings.update().where(bindings.c.id == row["id"]).values(**values))


def downgrade() -> None:
    # Never reactivate an unavailable route. Original values remain in audit.
    pass
