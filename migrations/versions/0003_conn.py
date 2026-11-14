"""Connection metadata tables (DATA-011..013).

Created in P0 because scope and field policies reference connections. Status
values follow the connection state machine in function_document.md (PRE-06,
inconsistency I-3): draft, testing, connected, introspecting, incomplete,
error, disabled. Deleted is a soft delete (deleted_at).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""

from migrations.helpers import run

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        CREATE TABLE conn.connections (
          id                     uuid PRIMARY KEY,
          tenant_id              uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          name                   text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
          engine                 text NOT NULL CHECK (engine IN
                                   ('postgres','mysql','sqlserver','mongodb','excel','csv','dataverse')),
          descriptor             jsonb NOT NULL DEFAULT '{}',
          secret_ref             text,
          status                 text NOT NULL DEFAULT 'draft' CHECK (status IN
                                   ('draft','testing','connected','introspecting','incomplete','error','disabled')),
          read_only_verified_at  timestamptz,
          read_only_evidence     jsonb,
          schema_checksum        text,
          freshness_policy       jsonb NOT NULL DEFAULT '{"mode":"live","cache_ttl_s":900}',
          row_cap                integer NOT NULL DEFAULT 10000 CHECK (row_cap BETWEEN 1 AND 10000),
          timeout_ms             integer NOT NULL DEFAULT 20000 CHECK (timeout_ms BETWEEN 1000 AND 20000),
          deleted_at             timestamptz,
          created_at             timestamptz NOT NULL DEFAULT now(),
          updated_at             timestamptz NOT NULL DEFAULT now(),
          created_by             uuid,
          -- Secrets never live in the descriptor (REQ-014).
          CONSTRAINT ck_descriptor_no_secret CHECK (NOT (descriptor ?| ARRAY['password','secret','client_secret','api_key']))
        );
        CREATE UNIQUE INDEX uq_connections_name ON conn.connections (tenant_id, name) WHERE deleted_at IS NULL;
        CREATE INDEX ix_connections_status ON conn.connections (tenant_id, status);

        CREATE TABLE conn.connection_objects (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id    uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          object_kind      text NOT NULL CHECK (object_kind IN ('table','view','collection','sheet','dataverse_table')),
          qualified_name   text NOT NULL,
          display_name     text,
          columns          jsonb NOT NULL DEFAULT '[]',
          is_allowed       boolean NOT NULL DEFAULT false,
          introspected_at  timestamptz NOT NULL DEFAULT now(),
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          created_by       uuid,
          CONSTRAINT uq_connection_object UNIQUE (connection_id, qualified_name)
        );

        CREATE TABLE conn.file_sources (
          id                 uuid PRIMARY KEY,
          tenant_id          uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id      uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          version_no         integer NOT NULL DEFAULT 1,
          object_key         text NOT NULL,
          original_filename  text NOT NULL,
          content_type       text NOT NULL,
          size_bytes         bigint NOT NULL CHECK (size_bytes > 0),
          sha256             text NOT NULL,
          sheet_manifest     jsonb NOT NULL DEFAULT '[]',
          retention_until    timestamptz,
          replaced_at        timestamptz,
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now(),
          created_by         uuid,
          CONSTRAINT uq_file_version UNIQUE (connection_id, version_no)
        );
        -- One current file per connection; superseded files are kept until retention ends.
        CREATE UNIQUE INDEX uq_file_current ON conn.file_sources (connection_id) WHERE replaced_at IS NULL;
        """
    )
    for table in ("connections", "connection_objects", "file_sources"):
        run(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON conn.{table} "
            "FOR EACH ROW EXECUTE FUNCTION util.set_updated_at()"
        )


def downgrade() -> None:
    for table in ("file_sources", "connection_objects", "connections"):
        run(f"DROP TABLE IF EXISTS conn.{table} CASCADE")
