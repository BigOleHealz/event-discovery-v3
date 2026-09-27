"""Source inventory, replay plans and site-page audit trail (Phase 8b)."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260927_0013"
down_revision: str | None = "20260926_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ingest.source_adapter (
            source TEXT PRIMARY KEY CHECK (btrim(source) <> ''),
            fetch_method TEXT NOT NULL CHECK (fetch_method IN ('api', 'http', 'stagehand')),
            priority SMALLINT NOT NULL CHECK (priority >= 0),
            extraction JSONB,
            pagination JSONB,
            model TEXT,
            prompt_version SMALLINT CHECK (prompt_version > 0),
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (
                (fetch_method = 'api' AND extraction IS NULL AND pagination IS NULL
                 AND model IS NULL AND prompt_version IS NULL)
                OR (fetch_method <> 'api' AND jsonb_typeof(extraction) = 'object'
                    AND extraction IS NOT NULL AND jsonb_typeof(pagination) = 'object'
                    AND pagination IS NOT NULL AND btrim(model) <> '' AND model IS NOT NULL
                    AND prompt_version IS NOT NULL)
            )
        );
        INSERT INTO ingest.source_adapter (source, fetch_method, priority, enabled)
            VALUES ('eventbrite', 'api', 0, true), ('meetup', 'api', 1, true);

        CREATE TABLE ingest.extraction_plan (
            source TEXT NOT NULL REFERENCES ingest.source_adapter(source) ON DELETE CASCADE,
            config_hash TEXT NOT NULL,
            plan JSONB NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
            model TEXT NOT NULL,
            prompt_version SMALLINT NOT NULL CHECK (prompt_version > 0),
            derived_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (source, config_hash)
        );
        ALTER TABLE source_listing ADD COLUMN extraction_model TEXT;
        ALTER TABLE source_listing ADD COLUMN extraction_prompt_version SMALLINT;
        ALTER TABLE source_listing ADD CONSTRAINT extraction_provenance_complete CHECK (
            (extraction_model IS NULL AND extraction_prompt_version IS NULL) OR
            (extraction_model IS NOT NULL AND extraction_prompt_version IS NOT NULL
             AND extraction_prompt_version > 0)
        );
        ALTER TABLE ingest.run ADD COLUMN source_config JSONB;
        CREATE TABLE ingest.site_page (
            page_fetch_id UUID PRIMARY KEY REFERENCES ingest.page_fetch(id) ON DELETE CASCADE,
            config_hash TEXT NOT NULL,
            category TEXT NOT NULL,
            html TEXT NOT NULL,
            events JSONB CHECK (jsonb_typeof(events) = 'array'),
            next_url TEXT,
            next_selector TEXT,
            extraction_model TEXT,
            prompt_version SMALLINT,
            cache_hit BOOLEAN NOT NULL DEFAULT false,
            validation_failures SMALLINT NOT NULL DEFAULT 0 CHECK (validation_failures >= 0)
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE ingest.site_page;
        ALTER TABLE ingest.run DROP COLUMN source_config;
        ALTER TABLE source_listing DROP CONSTRAINT extraction_provenance_complete;
        ALTER TABLE source_listing DROP COLUMN extraction_prompt_version;
        ALTER TABLE source_listing DROP COLUMN extraction_model;
        DROP TABLE ingest.extraction_plan;
        DROP TABLE ingest.source_adapter;
    """)
