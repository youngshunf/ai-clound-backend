"""R3 moved table 显式 schema 路由守卫。"""

from __future__ import annotations

import os
import subprocess
import sys

from pathlib import Path

from backend.database.schema_names import SchemaNames
from backend.scripts.im_r3_static_audit import find_unqualified_moved_table_sql

_REPO_ROOT = Path(__file__).resolve().parents[4]
_APP_ROOT = _REPO_ROOT / 'backend/app'


def test_pre_cutover_schema_is_explicit_public() -> None:
    """迁移前 ORM 与裸 SQL 同样必须显式使用 public，不能依赖默认 search_path。"""
    names = SchemaNames(cutover=False)

    assert names.im_schema == 'public'
    assert names.sync_schema == 'public'
    assert names.im_table('hasn_messages') == 'public.hasn_messages'
    assert names.sync_table('hasn_sync_events') == 'public.hasn_sync_events'


def test_cutover_moved_orm_tables_compile_in_target_schemas() -> None:
    """独立进程按 cutover=true 导入模型，逐表核对 SQLAlchemy fullname。"""
    code = """
from backend.app.hasn.model.hasn_asset_grants import HasnAssetGrants
from backend.app.hasn.model.hasn_contact_requests import HasnContactRequests
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.model.hasn_conversation_memberships import HasnConversationMemberships
from backend.app.hasn.model.hasn_conversations import HasnConversations
from backend.app.hasn.model.hasn_group_agent_invites import HasnGroupAgentInvites
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_suppressed_messages import HasnSuppressedMessages
from backend.app.hasn.model.hasn_sync_events import HasnSyncEvents
from backend.app.hasn.model.hasn_sync_inbox_events import HasnSyncInboxEvents
from backend.app.hasn.model.hasn_unread_projection import HasnUnreadProjection

expected = {
    HasnAssetGrants: "hasn_im.hasn_asset_grants",
    HasnContactRequests: "hasn_im.hasn_contact_requests",
    HasnContacts: "hasn_im.hasn_contacts",
    HasnConversationMemberships: "hasn_im.hasn_conversation_memberships",
    HasnConversations: "hasn_im.hasn_conversations",
    HasnGroupAgentInvites: "hasn_im.hasn_group_agent_invites",
    HasnMessages: "hasn_im.hasn_messages",
    HasnSuppressedMessages: "hasn_im.hasn_suppressed_messages",
    HasnSyncEvents: "hasn_sync.hasn_sync_events",
    HasnSyncInboxEvents: "hasn_sync.hasn_sync_inbox_events",
    HasnUnreadProjection: "hasn_im.hasn_unread_projection",
}
actual = {model.__name__: model.__table__.fullname for model in expected}
wanted = {model.__name__: fullname for model, fullname in expected.items()}
if actual != wanted:
    raise SystemExit(f"ORM schema 不匹配：{actual!r} != {wanted!r}")
"""
    env = os.environ.copy()
    env['HASN_IM_SCHEMA_CUTOVER'] = 'true'
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=_REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_runtime_moved_table_sql_has_explicit_schema() -> None:
    """运行时 moved table 裸 SQL 必须清零。"""
    findings = [
        item
        for item in find_unqualified_moved_table_sql(_APP_ROOT)
        if 'tests' not in item.path.parts and 'migration' not in item.path.parts
    ]

    assert findings == []


def test_cutover_migration_has_no_compatibility_view() -> None:
    """R2-11 不得引入 public compatibility view。"""
    migration = (
        _REPO_ROOT
        / 'backend/sql/hasn/migrations/2026-07-16-r2-11-schema-cutover.sql'
    ).read_text(encoding='utf-8')

    assert 'CREATE VIEW' not in migration.upper()
