from __future__ import annotations

from pathlib import Path

from backend.plugin.code_generator.parser.sql_parser import sql_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
HERMES_SQL_DIR = REPO_ROOT / "backend" / "sql" / "tables"
HERMES_APP_DIR = REPO_ROOT / "backend" / "app" / "hermes"
HUANXING_APP_DIR = REPO_ROOT / "backend" / "app" / "huanxing"

HERMES_TABLES = [
    "hermes_agent",
    "hermes_agent_runtime_state",
    "hermes_agent_channel_binding",
    "hermes_agent_operation",
]

FORBIDDEN_COLUMNS = {
    "raw_llm_key",
    "llm_api_key",
    "channel_secret",
    "raw_secret",
    "profile_env",
    "runtime_token",
}

DICT_COMMENT_MARKERS = {
    "hermes_agent.sql": ["template", "status", "llm_mode", "gateway_status", "workspace_status", "sandbox_status"],
    "hermes_agent_runtime_state.sql": ["gateway_status", "terminal_backend", "workspace_status", "mount_policy", "network_policy"],
    "hermes_agent_channel_binding.sql": ["channel", "bind_mode", "status"],
    "hermes_agent_operation.sql": ["operation_type", "operation_status"],
}


def _parse_hermes_table(table_name: str):
    path = HERMES_SQL_DIR / f"{table_name}.sql"
    assert path.exists(), f"missing Hermes SQL file: {path}"
    raw_sql = path.read_text(encoding="utf-8")
    tables = sql_parser.parse_all(raw_sql)
    assert len(tables) == 1, f"{path.name} must contain exactly one CREATE TABLE"
    table = tables[0]
    assert table.name == table_name
    assert f'COMMENT ON TABLE "public"."{table_name}"' in raw_sql
    missing_comments = [column.name for column in table.columns if not column.comment]
    assert missing_comments == []
    return table, raw_sql


def test_hermes_sql_contract_matches_codegen_and_secret_boundaries() -> None:
    for table_name in HERMES_TABLES:
        table, raw_sql = _parse_hermes_table(table_name)
        columns = {column.name for column in table.columns}
        assert columns.isdisjoint(FORBIDDEN_COLUMNS)
        assert "COMMENT ON COLUMN" in raw_sql
        assert "huanxing_user" not in raw_sql.lower()
        assert "hasn_" not in raw_sql.lower()

        for column in DICT_COMMENT_MARKERS[f"{table_name}.sql"]:
            comment = next(item.comment for item in table.columns if item.name == column)
            assert "(" in comment and ")" in comment and ":" in comment and "/" in comment


def test_hermes_codegen_outputs_are_admin_only_and_under_hermes_app() -> None:
    for table_name in HERMES_TABLES:
        assert (HERMES_APP_DIR / "model" / f"{table_name}.py").exists()
        assert (HERMES_APP_DIR / "schema" / f"{table_name}.py").exists()
        assert (HERMES_APP_DIR / "crud" / f"crud_{table_name}.py").exists()
        assert (HERMES_APP_DIR / "service" / f"{table_name}_service.py").exists()
        assert (HERMES_APP_DIR / "api" / "v1" / "admin" / f"{table_name}.py").exists()
        assert not (HERMES_APP_DIR / "api" / "v1" / "app" / f"{table_name}.py").exists()
        assert not (HUANXING_APP_DIR / "model" / f"{table_name}.py").exists()

    router_text = (HERMES_APP_DIR / "api" / "router.py").read_text(encoding="utf-8")
    assert "api.v1.admin" in router_text
    assert "api.v1.app.agents" in router_text
    assert "app.include_router(app_agents_router, prefix='/agents'" in router_text
