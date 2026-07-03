from __future__ import annotations

import importlib

from pathlib import Path

import yaml

from backend.plugin.code_generator.parser.sql_parser import sql_parser

REPO_ROOT = Path(__file__).resolve().parents[3]
HASN_SQL_DIR = REPO_ROOT / "backend" / "sql" / "hasn"
OPENAPI_FILE = REPO_ROOT / "docs" / "openapi-hasn-cloud-v1.yaml"
ERRORS_FILE = REPO_ROOT / "sql" / "errors.md"
CODEGEN_DOC = REPO_ROOT / "backend" / "代码生成使用说明.md"
HASN_S0S1_README = HASN_SQL_DIR / "README.md"

CODEGEN_SQL_FILES = [
    "hasn_agent_capabilities.sql",
    "hasn_agent_runtime_reports.sql",
    "hasn_agents.sql",
    "hasn_audit_log.sql",
    "hasn_channel_bindings.sql",
    "hasn_clients.sql",
    "hasn_contacts.sql",
    "hasn_conversations.sql",
    "hasn_group_members.sql",
    "hasn_humans.sql",
    "hasn_messages.sql",
    "memory_namespace_revisions.sql",
    "hasn_node_bindings.sql",
    "hasn_nodes.sql",
    "hasn_notifications.sql",
    "hasn_owner_api_keys.sql",
    "hasn_pending_intents.sql",
    "hasn_suppressed_messages.sql",
    "hasn_sync_events.sql",
    "hasn_sync_inbox_events.sql",
    "hasn_trade_sessions.sql",
    "hasn_unread_counts.sql",
]

REQUIRED_S1_TABLES = {
    "hasn_nodes",
    "hasn_node_bindings",
    "hasn_owner_api_keys",
    "hasn_sync_events",
    "hasn_sync_inbox_events",
    "memory_namespace_revisions",
    "hasn_agent_runtime_reports",
    "hasn_suppressed_messages",
    "hasn_pending_intents",
    "hasn_channel_bindings",
}

PRIVATE_RUNTIME_COLUMNS = {
    "workspace",
    "workspace_path",
    "endpoint",
    "local_endpoint",
    "pid",
    "process_id",
    "cli_args",
    "oauth_path",
    "session_cache",
}


def _parse_table(sql_file: str):
    path = HASN_SQL_DIR / sql_file
    assert path.exists(), f"missing HASN SQL file: {path}"
    raw_sql = path.read_text(encoding="utf-8")
    tables = sql_parser.parse_all(raw_sql)
    assert len(tables) == 1, f"{sql_file} must contain exactly one CREATE TABLE for codegen"
    table = tables[0]
    assert table.name == sql_file.removesuffix(".sql")
    assert f'COMMENT ON TABLE "public"."{table.name}"' in raw_sql, (
        f"{sql_file} must have COMMENT ON TABLE for codegen metadata"
    )
    assert table.columns, f"{sql_file} must expose columns to codegen parser"
    missing_comments = [column.name for column in table.columns if not column.comment]
    assert not missing_comments, f"{sql_file} columns missing COMMENT ON COLUMN: {missing_comments}"
    return table


def test_openapi_and_error_contracts_are_readable_and_runtime_safe() -> None:
    data = yaml.safe_load(OPENAPI_FILE.read_text(encoding="utf-8"))
    assert data["openapi"] == "3.1.0"
    assert data["info"]["version"] == "1.0.0-p0"
    assert len(data["paths"]) == 18
    assert all(path.startswith("/api/v1/hasn/") for path in data["paths"])

    schemas = data["components"]["schemas"]
    inbox_item = schemas["InboxItem"]["properties"]
    for field in ("message_id", "owner_id", "hasn_id", "conversation_id", "inbox_kind", "dispatch_status"):
        assert field in inbox_item
    assert "runtime_unavailable" in inbox_item["dispatch_status"]["enum"]
    assert "suppressed_inbox" in inbox_item["inbox_kind"]["enum"]

    errors = ERRORS_FILE.read_text(encoding="utf-8")
    assert "ERR_MESSAGE_DELIVERY_FAILED" in errors
    assert "不得用于 RuntimeUnavailable" in errors
    assert "ERR_RUNTIME_PRIVATE_METADATA_REJECTED" in errors
    assert "RuntimeUnavailable != MessageDeliveryFailed" in errors


def test_codegen_doc_freezes_hasn_sql_path_and_no_handwritten_crud() -> None:
    doc = CODEGEN_DOC.read_text(encoding="utf-8")
    assert "backend/sql/hasn/xxx.sql" in doc
    assert "uv run fba codegen generate --sql-file backend/sql/hasn/xxx.sql --app hasn --execute" in doc
    assert "HASN 模块统一使用专属 SQL 目录" in doc
    assert "model/schema/crud/service/api" in doc
    readme = HASN_S0S1_README.read_text(encoding="utf-8")
    assert "不手写 CRUD 样板" in readme
    assert "CODEGEN_GAP" in readme


def test_hasn_sql_codegen_inputs_are_single_table_and_commented() -> None:
    parsed = {_parse_table(sql_file).name for sql_file in CODEGEN_SQL_FILES}
    assert REQUIRED_S1_TABLES.issubset(parsed)


def test_memory_namespace_revisions_schema_is_scope_namespace_authority() -> None:
    table = _parse_table("memory_namespace_revisions.sql")
    columns = {column.name for column in table.columns}

    for field in ("sync_scope_kind", "sync_scope_id", "namespace", "revision", "last_event_id", "updated_at"):
        assert field in columns

    raw_sql = (HASN_SQL_DIR / "memory_namespace_revisions.sql").read_text(encoding="utf-8")
    assert 'PRIMARY KEY ("sync_scope_kind", "sync_scope_id", "namespace")' in raw_sql
    assert "CHECK (\"sync_scope_kind\" IN ('owner', 'agent'))" in raw_sql
    assert '"revision"        bigint NOT NULL DEFAULT 0' in raw_sql
    assert 'idx_memory_namespace_revisions_updated' in raw_sql


def test_s1_message_and_suppressed_inbox_ownership_is_explicit() -> None:
    messages = _parse_table("hasn_messages.sql")
    message_columns = {column.name for column in messages.columns}
    for field in (
        "owner_id",
        "hasn_id",
        "sender_hasn_id",
        "recipient_hasn_id",
        "delivery_status",
        "dispatch_status",
        "sync_status",
    ):
        assert field in message_columns

    suppressed = _parse_table("hasn_suppressed_messages.sql")
    suppressed_columns = {column.name for column in suppressed.columns}
    for field in ("message_id", "owner_id", "hasn_id", "conversation_id", "suppress_reason", "dispatch_status"):
        assert field in suppressed_columns


def test_runtime_summary_tables_do_not_define_private_runtime_columns() -> None:
    for sql_file in (
        "hasn_agent_runtime_reports.sql",
        "hasn_suppressed_messages.sql",
        "hasn_nodes.sql",
        "hasn_messages.sql",
        "hasn_sync_inbox_events.sql",
    ):
        table = _parse_table(sql_file)
        columns = {column.name.lower() for column in table.columns}
        assert columns.isdisjoint(PRIVATE_RUNTIME_COLUMNS), f"{sql_file} leaks private runtime columns"


def test_migration_backfill_and_rollback_assets_exist_under_hasn_sql_dir() -> None:
    migration = HASN_SQL_DIR / "V001__hasn_s0_s1_existing_assets__migration.sql"
    rollback = HASN_SQL_DIR / "V001__hasn_s0_s1_existing_assets__rollback.sql"
    readme = HASN_SQL_DIR / "README.md"
    for path in (migration, rollback, readme):
        assert path.exists()
        assert path.parent == HASN_SQL_DIR

    migration_text = migration.read_text(encoding="utf-8")
    rollback_text = rollback.read_text(encoding="utf-8")
    assert "UPDATE \"public\".\"hasn_messages\"" in migration_text
    assert "RuntimeUnavailable" in migration_text
    assert "DROP COLUMN IF EXISTS \"owner_id\"" in rollback_text
    assert "DROP COLUMN IF EXISTS \"hasn_id\"" in rollback_text


def test_no_hasn_sql_added_outside_hasn_sql_dir() -> None:
    forbidden_roots = (REPO_ROOT / "backend" / "sql" / "tables", REPO_ROOT / "sql" / "migrations")
    leaked = [
        path.relative_to(REPO_ROOT).as_posix()
        for root in forbidden_roots
        if root.exists()
        for path in root.rglob("*hasn*.sql")
        if path.is_file()
    ]
    assert leaked == []


def test_task_system_v21_migration_task_uuid_unique_matches_upsert_conflict_target() -> None:
    migration = HASN_SQL_DIR / "migrations" / "2026-05-28-task-system-v21.sql"
    table_sql = HASN_SQL_DIR / "hasn_task.sql"
    for path in (migration, table_sql):
        assert path.exists()

    migration_text = migration.read_text(encoding="utf-8")
    table_text = table_sql.read_text(encoding="utf-8")

    assert 'CONSTRAINT "uq_hasn_task_task_uuid" UNIQUE ("task_uuid")' in table_text
    assert 'ADD CONSTRAINT "uq_hasn_task_task_uuid" UNIQUE ("task_uuid")' in migration_text
    assert 'ON "public"."hasn_task"("task_uuid")\n  WHERE "task_uuid" IS NOT NULL' not in migration_text


def test_task_assignment_keeps_one_current_row_per_task() -> None:
    migration = HASN_SQL_DIR / "migrations" / "2026-05-28-task-system-v21.sql"
    table_sql = HASN_SQL_DIR / "hasn_task_assignment.sql"
    model_file = REPO_ROOT / "backend" / "app" / "hasn" / "model" / "hasn_task_assignment.py"
    for path in (migration, table_sql, model_file):
        assert path.exists()

    migration_text = migration.read_text(encoding="utf-8")
    table_text = table_sql.read_text(encoding="utf-8")
    model_text = model_file.read_text(encoding="utf-8")

    assert 'CONSTRAINT "uq_hasn_task_assignment_task_uuid"' in table_text
    assert 'UNIQUE ("task_uuid")' in table_text
    assert 'uq_hasn_task_assignment_task_agent_node' not in table_text
    assert 'uq_hasn_task_assignment_task_uuid' in migration_text
    assert 'PARTITION BY "task_uuid"' in migration_text
    assert "name='uq_hasn_task_assignment_task_uuid'" in model_text
    assert "name='uq_hasn_task_assignment_task_agent_node'" not in model_text


def test_skill_pack_migration_backfills_legacy_hasn_skill_bundle() -> None:
    migration = REPO_ROOT / "backend" / "sql" / "marketplace" / "migrations" / "2026-05-28-skill-pack-hermes-fields.sql"
    migration_text = migration.read_text(encoding="utf-8")

    assert 'FROM "public"."hasn_skill_bundle"' in migration_text
    assert "template_type" in migration_text
    assert "'skill_pack'" in migration_text
    assert "jsonb_build_object" in migration_text
    assert "'skills'" in migration_text
    assert "hermes_yaml" in migration_text
    assert "bundle_slug" in migration_text
    assert "command_key" in migration_text
    assert "content_hash" in migration_text
    assert "'hasn-skill-bundle:' || b.\"id\"" in migration_text
    assert "md5(" in migration_text
    assert "ON CONFLICT" in migration_text


def test_hasn_h2_alembic_revision_chains_to_h1_contacts_revision() -> None:
    h1 = importlib.import_module("backend.alembic.versions.20260424_h1_hasn_contacts_peer_owner")
    h2 = importlib.import_module("backend.alembic.versions.20260425_h2_agent_runtime_binding_phase1")

    assert h2.down_revision == h1.revision


def test_hasn_h3_memory_namespace_revision_chains_to_h2_revision() -> None:
    h2 = importlib.import_module("backend.alembic.versions.20260425_h2_agent_runtime_binding_phase1")
    h3 = importlib.import_module("backend.alembic.versions.20260523_h3_memory_namespace_revisions")

    assert h3.down_revision == h2.revision
