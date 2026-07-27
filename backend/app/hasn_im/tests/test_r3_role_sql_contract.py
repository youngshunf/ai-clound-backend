"""R3 角色、权限负测与自包含迁移的静态契约。"""

from __future__ import annotations

from pathlib import Path
import re


_ROOT = Path(__file__).resolve().parents[4]
_MIGRATIONS = _ROOT / 'backend/sql/hasn/migrations'
_FORWARD = _MIGRATIONS / '2026-07-16-r2-11-schema-cutover.sql'
_PERMISSION_TEST = _MIGRATIONS / '2026-07-16-r2-11-permission-negative-test.sql'
_LOGIN_OPERATION = _ROOT / 'backend/sql/hasn/operations/r3-enable-service-logins.sql'
_MEMBERSHIP_ADDITIVE = (
    _MIGRATIONS / '2026-07-15-r2-03-membership-display-fields.sql'
)
_LATE_MEMBERSHIP_MIGRATION = (
    _MIGRATIONS / '2026-07-27-r3-membership-compat-fields.sql'
)
_TASK_VISIBILITY_MIGRATION = (
    _MIGRATIONS / '2026-07-27-r3-task-event-node-visibility.sql'
)
_SYNC_INBOX_WORKER_MIGRATION = (
    _MIGRATIONS / '2026-07-27-r3-sync-inbox-worker.sql'
)
_REHEARSAL = _MIGRATIONS / 'r3_migration_rehearsal.py'


def test_forward_migration_contains_membership_columns_before_backfill() -> None:
    """R2-11 自身必须先补齐回填所需字段，不能依赖日期更晚的迁移。"""
    sql = _FORWARD.read_text(encoding='utf-8')
    insert_at = sql.index('INSERT INTO public.hasn_conversation_memberships')
    for column in (
        'member_star_id',
        'member_name',
        'muted',
        'invited_by',
        'charter_updated_time',
        'history_complete_from_seq',
    ):
        add_at = sql.index(f'ADD COLUMN IF NOT EXISTS {column}')
        assert add_at < insert_at, f'{column} 必须在首次成员回填前创建'
    assert not _LATE_MEMBERSHIP_MIGRATION.exists(), '必要字段折叠后必须删除晚日期重复迁移'


def test_sync_inbox_idempotency_constraint_is_self_contained() -> None:
    """metadata 基线和 R3 迁移都必须提供 sync push 的 ON CONFLICT 约束。"""
    migration = _FORWARD.read_text(encoding='utf-8')
    worker_migration = _SYNC_INBOX_WORKER_MIGRATION.read_text(encoding='utf-8')
    rehearsal = _REHEARSAL.read_text(encoding='utf-8')

    assert 'ADD CONSTRAINT uq_hasn_sync_inbox_client_event' in migration
    assert 'UNIQUE (owner_id, node_id, client_event_id)' in migration
    add_column_at = worker_migration.index(
        'ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0'
    )
    set_default_at = worker_migration.index(
        'ALTER COLUMN attempt_count SET DEFAULT 0'
    )
    assert add_column_at < set_default_at
    assert "conname = 'uq_hasn_sync_inbox_client_event'" in rehearsal
    assert "column_default = '0'" in rehearsal


def test_membership_fields_have_pre_cutover_additive_migration() -> None:
    """前切换代码也要能先补字段，不能只有 R2-11 维护窗口才创建。"""
    sql = _MEMBERSHIP_ADDITIVE.read_text(encoding='utf-8').upper()
    assert 'ALTER TABLE PUBLIC.HASN_CONVERSATION_MEMBERSHIPS' in sql
    for column in (
        'MEMBER_STAR_ID',
        'MEMBER_NAME',
        'MUTED',
        'INVITED_BY',
        'CHARTER_UPDATED_TIME',
        'HISTORY_COMPLETE_FROM_SEQ',
    ):
        assert f'ADD COLUMN IF NOT EXISTS {column}' in sql


def test_login_operation_uses_psql_variables_and_contains_no_password() -> None:
    """LOGIN 启用脚本只接受 psql 变量，不在源码内保存任何密码。"""
    sql = _LOGIN_OPERATION.read_text(encoding='utf-8')
    assert "PASSWORD :'im_service_password'" in sql
    assert "PASSWORD :'sync_service_password'" in sql
    assert "PASSWORD :'python_backend_password'" in sql
    for role in (
        'astra_im_service',
        'astra_sync_service',
        'astra_python_backend',
    ):
        assert re.search(rf'ALTER ROLE {role} WITH\s+LOGIN\b', sql)
    assert 'test-only' not in sql
    assert 'postgresql://' not in sql
    assert 'postgresql+asyncpg://' not in sql


def test_permission_test_uses_real_login_transaction_and_rolls_back() -> None:
    """权限断言必须由真实 current_user 执行，允许路径不能吞掉任意异常。"""
    sql = _PERMISSION_TEST.read_text(encoding='utf-8')
    upper = sql.upper()
    executable = '\n'.join(
        line for line in upper.splitlines() if not line.lstrip().startswith('--')
    )
    assert 'BEGIN;' in upper
    assert upper.rstrip().endswith('ROLLBACK;')
    assert 'CURRENT_USER' in upper
    assert 'SET ROLE' not in executable
    assert 'WHEN OTHERS' not in executable
    assert 'ASTRA_R3_PERMISSION_PROBE' in upper
    assert 'PERM-POS FAIL' in sql
    assert 'ASSERT_CHECK_VIOLATION' in upper
    assert 'APPEND-VALIDATION FAIL' in sql


def test_python_role_has_no_direct_im_or_sync_table_access() -> None:
    """普通 Python 角色只经 append_event 跨域写，不直接读写 IM/sync 表。"""
    sql = _FORWARD.read_text(encoding='utf-8').upper()
    assert (
        'GRANT SELECT ON ALL TABLES IN SCHEMA HASN_IM TO ASTRA_PYTHON_BACKEND'
        not in sql
    )
    assert (
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA HASN_SYNC '
        'TO ASTRA_PYTHON_BACKEND'
        not in sql
    )
    assert (
        'REVOKE ALL ON FUNCTION HASN_SYNC.APPEND_EVENT'
        in sql
    )


def test_sync_role_cannot_bypass_append_event() -> None:
    """sync 角色可维护 inbox 和清理事件，但不得直接创建下行事件。"""
    forward = _FORWARD.read_text(encoding='utf-8').upper()
    permission_test = _PERMISSION_TEST.read_text(encoding='utf-8').upper()

    assert (
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA HASN_SYNC '
        'TO ASTRA_SYNC_SERVICE'
        not in forward
    )
    assert (
        'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA HASN_SYNC '
        'FROM ASTRA_SYNC_SERVICE'
        in forward
    )
    assert (
        'GRANT SELECT, DELETE ON HASN_SYNC.HASN_SYNC_EVENTS '
        'TO ASTRA_SYNC_SERVICE'
        in forward
    )
    assert re.search(
        r'GRANT SELECT,\s*INSERT,\s*UPDATE,\s*DELETE\s+'
        r'ON HASN_SYNC\.HASN_SYNC_INBOX_EVENTS TO ASTRA_SYNC_SERVICE',
        forward,
    )
    assert 'SYNC 角色仍可绕过 APPEND_EVENT' in permission_test
    assert 'SYNC 角色 INBOX 状态更新结果' in permission_test


def test_task_visibility_is_backfilled_before_sync_role_serves_pull() -> None:
    """存量任务事件必须补齐节点可见性，且迁移纳入 R3 正向清单。"""
    sql = _TASK_VISIBILITY_MIGRATION.read_text(encoding='utf-8')
    assert "payload ? 'visible_node_ids'" in sql
    assert 'hasn_task.assignment' in sql
    assert 'jsonb_set' in sql
    rehearsal = _REHEARSAL.read_text(encoding='utf-8')
    assert _TASK_VISIBILITY_MIGRATION.name in rehearsal
