"""v8.0: library book fields, import sessions, profiles, prompt studio."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0007_v8_library_import_prompt"
down_revision = "0006_pipeline_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    def has_col(table: str, col: str) -> bool:
        return bool(
            conn.execute(
                sa.text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name=:t AND column_name=:c"
                ),
                {"t": table, "c": col},
            ).scalar()
        )

    def has_table(table: str) -> bool:
        return bool(
            conn.execute(
                sa.text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": table},
            ).scalar()
        )

    book_cols = [
        ("subtitle", sa.Column("subtitle", sa.String(500), nullable=True)),
        ("logline", sa.Column("logline", sa.Text(), nullable=True)),
        ("synopsis", sa.Column("synopsis", sa.Text(), nullable=True)),
        ("genre", sa.Column("genre", sa.String(100), nullable=True)),
        ("tags", sa.Column("tags", JSONB(), nullable=False, server_default="[]")),
        ("tone_summary", sa.Column("tone_summary", sa.Text(), nullable=True)),
        ("cover_path", sa.Column("cover_path", sa.Text(), nullable=True)),
        ("cover_thumb_path", sa.Column("cover_thumb_path", sa.Text(), nullable=True)),
        ("cover_hash", sa.Column("cover_hash", sa.String(64), nullable=True)),
        ("planned_chapters", sa.Column("planned_chapters", sa.Integer(), nullable=True)),
        ("current_chapter_no", sa.Column("current_chapter_no", sa.Integer(), nullable=True)),
        (
            "lifecycle_status",
            sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="draft"),
        ),
        ("source_import_session_id", sa.Column("source_import_session_id", UUID(as_uuid=True), nullable=True)),
        ("last_activity_at", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True)),
    ]
    for name, col in book_cols:
        if not has_col("books", name):
            op.add_column("books", col)

    # backfill planned_chapters / lifecycle
    conn.execute(
        sa.text(
            "UPDATE books SET planned_chapters = target_chapters "
            "WHERE planned_chapters IS NULL"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE books SET last_activity_at = COALESCE(updated_at, created_at) "
            "WHERE last_activity_at IS NULL"
        )
    )

    node_cols = [
        ("volume_id", sa.Column("volume_id", UUID(as_uuid=True), nullable=True)),
        ("arc_id", sa.Column("arc_id", UUID(as_uuid=True), nullable=True)),
        ("source_refs", sa.Column("source_refs", JSONB(), nullable=False, server_default="[]")),
        ("import_artifact_id", sa.Column("import_artifact_id", UUID(as_uuid=True), nullable=True)),
    ]
    for name, col in node_cols:
        if not has_col("outline_nodes", name):
            op.add_column("outline_nodes", col)

    tables_sql = {
        "book_profiles": """
        CREATE TABLE IF NOT EXISTS book_profiles (
            id UUID PRIMARY KEY,
            book_id UUID NOT NULL UNIQUE REFERENCES books(id),
            logline TEXT,
            synopsis TEXT,
            genre VARCHAR(100),
            themes JSONB NOT NULL DEFAULT '[]',
            tone TEXT,
            audience VARCHAR(200),
            content_boundaries JSONB NOT NULL DEFAULT '[]',
            core_loop TEXT,
            extra JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
        "book_sources": """
        CREATE TABLE IF NOT EXISTS book_sources (
            id UUID PRIMARY KEY,
            book_id UUID REFERENCES books(id),
            original_filename VARCHAR(500) NOT NULL,
            mime_type VARCHAR(200),
            file_size INTEGER NOT NULL DEFAULT 0,
            sha256 VARCHAR(64) NOT NULL,
            storage_path TEXT NOT NULL,
            extractor_version VARCHAR(50) NOT NULL DEFAULT 'v1',
            extracted_text_path TEXT,
            extracted_blocks_json JSONB,
            uploaded_by VARCHAR(200),
            legacy_import BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
        "import_sessions": """
        CREATE TABLE IF NOT EXISTS import_sessions (
            id UUID PRIMARY KEY,
            status VARCHAR(50) NOT NULL DEFAULT 'uploaded',
            source_id UUID NOT NULL REFERENCES book_sources(id),
            book_id UUID REFERENCES books(id),
            primary_document_type VARCHAR(100),
            document_types JSONB NOT NULL DEFAULT '[]',
            progress DOUBLE PRECISION NOT NULL DEFAULT 0,
            current_step VARCHAR(100),
            error_code VARCHAR(100),
            error_detail TEXT,
            parser_version VARCHAR(50) NOT NULL DEFAULT 'v8.0',
            pipeline_version VARCHAR(50) NOT NULL DEFAULT 'v8.0',
            control_requested VARCHAR(50),
            preview_hash VARCHAR(64),
            commit_idempotency_key VARCHAR(100),
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
        "import_session_events": """
        CREATE TABLE IF NOT EXISTS import_session_events (
            id UUID PRIMARY KEY,
            import_session_id UUID NOT NULL REFERENCES import_sessions(id),
            from_status VARCHAR(50),
            to_status VARCHAR(50) NOT NULL,
            step VARCHAR(100),
            detail JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
        "import_artifacts": """
        CREATE TABLE IF NOT EXISTS import_artifacts (
            id UUID PRIMARY KEY,
            import_session_id UUID NOT NULL REFERENCES import_sessions(id),
            artifact_type VARCHAR(100) NOT NULL,
            artifact_key VARCHAR(200) NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(50) NOT NULL DEFAULT 'ready',
            input_hash VARCHAR(64),
            output_json JSONB,
            output_text TEXT,
            source_refs JSONB NOT NULL DEFAULT '[]',
            agent_run_id UUID,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            UNIQUE(import_session_id, artifact_key, version)
        )
        """,
        "import_conflicts": """
        CREATE TABLE IF NOT EXISTS import_conflicts (
            id UUID PRIMARY KEY,
            import_session_id UUID NOT NULL REFERENCES import_sessions(id),
            code VARCHAR(100) NOT NULL,
            severity VARCHAR(20) NOT NULL DEFAULT 'warning',
            entity_type VARCHAR(100),
            entity_temp_id VARCHAR(100),
            message TEXT NOT NULL,
            options JSONB NOT NULL DEFAULT '[]',
            selected_option_id VARCHAR(100),
            status VARCHAR(50) NOT NULL DEFAULT 'open',
            source_refs JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
        "location_cards": """
        CREATE TABLE IF NOT EXISTS location_cards (
            id UUID PRIMARY KEY,
            book_id UUID NOT NULL REFERENCES books(id),
            name VARCHAR(300) NOT NULL,
            aliases JSONB NOT NULL DEFAULT '[]',
            description TEXT,
            environment TEXT,
            resources JSONB NOT NULL DEFAULT '[]',
            rules JSONB NOT NULL DEFAULT '[]',
            parent_location_id UUID,
            source_refs JSONB NOT NULL DEFAULT '[]',
            status VARCHAR(50) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
        "character_relationships": """
        CREATE TABLE IF NOT EXISTS character_relationships (
            id UUID PRIMARY KEY,
            book_id UUID NOT NULL REFERENCES books(id),
            from_character_id UUID NOT NULL,
            to_character_id UUID NOT NULL,
            relation_type VARCHAR(100) NOT NULL,
            stage VARCHAR(100),
            strength DOUBLE PRECISION,
            start_chapter_no INTEGER,
            end_chapter_no INTEGER,
            description TEXT,
            source_refs JSONB NOT NULL DEFAULT '[]',
            status VARCHAR(50) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
        "outline_volumes": """
        CREATE TABLE IF NOT EXISTS outline_volumes (
            id UUID PRIMARY KEY,
            book_id UUID NOT NULL REFERENCES books(id),
            outline_version_id UUID REFERENCES outline_versions(id),
            volume_no INTEGER NOT NULL,
            title VARCHAR(500),
            chapter_from INTEGER,
            chapter_to INTEGER,
            goal TEXT,
            themes JSONB NOT NULL DEFAULT '[]',
            required_outcomes JSONB NOT NULL DEFAULT '[]',
            forbidden_outcomes JSONB NOT NULL DEFAULT '[]',
            involved_character_ids JSONB NOT NULL DEFAULT '[]',
            source_refs JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            UNIQUE(book_id, volume_no, outline_version_id)
        )
        """,
        "writing_constraints": """
        CREATE TABLE IF NOT EXISTS writing_constraints (
            id UUID PRIMARY KEY,
            book_id UUID NOT NULL REFERENCES books(id),
            scope_type VARCHAR(50) NOT NULL,
            scope_id VARCHAR(100),
            constraint_type VARCHAR(100) NOT NULL,
            title VARCHAR(300),
            body TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 100,
            is_hard BOOLEAN NOT NULL DEFAULT false,
            status VARCHAR(50) NOT NULL DEFAULT 'active',
            source_refs JSONB NOT NULL DEFAULT '[]',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
        "prompt_template_versions": """
        CREATE TABLE IF NOT EXISTS prompt_template_versions (
            id UUID PRIMARY KEY,
            template_key VARCHAR(200) NOT NULL,
            agent_role VARCHAR(100) NOT NULL,
            scope_type VARCHAR(50) NOT NULL DEFAULT 'system',
            scope_id VARCHAR(100),
            version INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(50) NOT NULL DEFAULT 'draft',
            name VARCHAR(300) NOT NULL,
            description TEXT,
            system_prompt TEXT NOT NULL DEFAULT '',
            user_prompt_template TEXT NOT NULL DEFAULT '',
            input_contract_key VARCHAR(200),
            input_contract_version VARCHAR(50),
            output_contract_key VARCHAR(200),
            output_contract_version VARCHAR(50),
            allowed_context_kinds JSONB NOT NULL DEFAULT '[]',
            required_context_kinds JSONB NOT NULL DEFAULT '[]',
            forbidden_context_kinds JSONB NOT NULL DEFAULT '[]',
            required_model_capabilities JSONB NOT NULL DEFAULT '[]',
            default_temperature DOUBLE PRECISION,
            default_max_output_tokens INTEGER,
            variables JSONB NOT NULL DEFAULT '[]',
            template_hash VARCHAR(64),
            created_by VARCHAR(200),
            activated_at TIMESTAMPTZ,
            supersedes_id UUID,
            last_test_passed BOOLEAN,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            UNIQUE(template_key, scope_type, scope_id, version)
        )
        """,
        "prompt_test_runs": """
        CREATE TABLE IF NOT EXISTS prompt_test_runs (
            id UUID PRIMARY KEY,
            template_version_id UUID NOT NULL REFERENCES prompt_template_versions(id),
            fixture_name VARCHAR(200),
            model VARCHAR(200),
            provider VARCHAR(100),
            input_json JSONB,
            output_json JSONB,
            output_text TEXT,
            contract_ok BOOLEAN,
            leak_ok BOOLEAN,
            latency_ms INTEGER,
            error TEXT,
            status VARCHAR(50) NOT NULL DEFAULT 'completed',
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """,
    }
    for name, sql in tables_sql.items():
        if not has_table(name):
            conn.execute(sa.text(sql))

    # indexes
    for stmt in [
        "CREATE INDEX IF NOT EXISTS ix_book_sources_sha256 ON book_sources(sha256)",
        "CREATE INDEX IF NOT EXISTS ix_import_sessions_status ON import_sessions(status)",
        "CREATE INDEX IF NOT EXISTS ix_import_artifacts_session ON import_artifacts(import_session_id)",
        "CREATE INDEX IF NOT EXISTS ix_books_lifecycle ON books(lifecycle_status)",
    ]:
        conn.execute(sa.text(stmt))

    # empty book_profiles for existing books
    conn.execute(
        sa.text(
            """
            INSERT INTO book_profiles (id, book_id, themes, content_boundaries, extra, created_at, updated_at)
            SELECT gen_random_uuid(), b.id, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, now(), now()
            FROM books b
            WHERE NOT EXISTS (SELECT 1 FROM book_profiles p WHERE p.book_id = b.id)
            """
        )
    )


def downgrade() -> None:
    # non-destructive preferred; leave tables
    pass
