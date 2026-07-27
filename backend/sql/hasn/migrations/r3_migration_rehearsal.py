#!/usr/bin/env python3
"""R3 IM schema/角色迁移的本地 PostgreSQL 全链演练。

固定执行三条真实路径：

1. 故障基线：在缺表库执行正向迁移，证明单事务失败后 schema/角色均未残留；
2. 空开发基线：``metadata.create_all`` → 前置迁移 → forward → 四 LOGIN 权限矩阵
   → reverse → forward；
3. 当前本地基线：只读 ``pg_dump`` 当前 dev 库并恢复到隔离库，重复同一全链。

安全边界：

- 只允许连接 loopback PostgreSQL；
- 所有可写目标库名必须包含 ``_im_r3_``；
- 源库只做 ``pg_dump``，迁移只在临时库执行；
- 三个服务角色在开工前必须不存在，避免改写既有集群角色；
- 密码仅运行时随机生成并经 psql 变量/PGPASSWORD 注入，不进入证据或控制台。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from typing import Any
from urllib import error as url_error
from urllib.parse import quote
from urllib import request as url_request


HOST = os.getenv('R3_PG_HOST', '127.0.0.1')
PORT = os.getenv('R3_PG_PORT', '15432')
SUPERUSER = os.getenv('R3_PG_SUPERUSER', 'mac')
SUPERUSER_PASSWORD = os.getenv('R3_PG_SUPERUSER_PASSWORD', '')
SOURCE_DB = os.getenv('R3_SOURCE_DB', 'huanxing')

FAILURE_DB = 'huanxing_im_r3_failure_rehearsal'
EMPTY_DB = 'huanxing_im_r3_empty_rehearsal'
CURRENT_DB = 'huanxing_im_r3_current_rehearsal'
TARGET_DATABASES = (FAILURE_DB, EMPTY_DB, CURRENT_DB)

MIGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = MIGRATION_DIR.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.hasn_im.protocol.version_gate import (  # noqa: E402
    R3_COMPANION_DAEMON_VERSION,
)

LOGIN_OPERATION = (
    MIGRATION_DIR.parent / 'operations' / 'r3-enable-service-logins.sql'
)
PERMISSION_TEST = (
    MIGRATION_DIR / '2026-07-16-r2-11-permission-negative-test.sql'
)
REVERSE_MIGRATION = (
    MIGRATION_DIR / '2026-07-16-r2-11-schema-cutover.reverse.sql'
)
TASK_DISPATCH_MIGRATION = (
    MIGRATION_DIR.parents[1]
    / 'hasn_task/migrations/2026-07-27-r3-task-dispatch-outbox.sql'
)
FORWARD_MIGRATIONS = (
    MIGRATION_DIR / '2026-07-16-r2-02-conversation-seq.sql',
    MIGRATION_DIR / '2026-07-16-r2-03-conversation-memberships.sql',
    MIGRATION_DIR / '2026-07-16-r2-04-integration-events.sql',
    MIGRATION_DIR / '2026-07-16-r2-05-event-consumers.sql',
    MIGRATION_DIR / '2026-07-16-r2-07-hasn-sync-append-event.sql',
    MIGRATION_DIR / '2026-07-27-r3-agent-communication-settings.sql',
    MIGRATION_DIR / '2026-07-27-r3-suppressed-command.sql',
    MIGRATION_DIR / '2026-07-27-r3-relation-command-outbox.sql',
    MIGRATION_DIR / '2026-07-27-r3-sync-business-receipts.sql',
    MIGRATION_DIR / '2026-07-27-r3-notification-im-command-outbox.sql',
    MIGRATION_DIR / '2026-07-27-r3-community-im-command-outbox.sql',
    MIGRATION_DIR / '2026-07-27-r3-session-im-command-outbox.sql',
    MIGRATION_DIR / '2026-07-27-r3-group-im-command-outbox.sql',
    TASK_DISPATCH_MIGRATION,
    MIGRATION_DIR / '2026-07-16-r2-11-schema-cutover.sql',
    MIGRATION_DIR / '2026-07-27-r3-task-event-node-visibility.sql',
    MIGRATION_DIR / '2026-07-27-r3-sync-inbox-worker.sql',
)
# 生产 runner 的精确暂缓清单：二者只能由维护窗口/演练器显式调用，绝不能按文件名顺序自动执行。
DEFERRED_MIGRATIONS = frozenset(
    {
        PERMISSION_TEST.name,
        REVERSE_MIGRATION.name,
    }
)
SERVICE_ROLES = (
    'astra_im_service',
    'astra_sync_service',
    'astra_python_backend',
)
UNAUTHORIZED_ROLE = 'astra_r3_unauthorized_probe'
ALL_REHEARSAL_ROLES = (*SERVICE_ROLES, UNAUTHORIZED_ROLE)
EVIDENCE_PATH = (
    REPO_ROOT
    / 'test-results/im-r3/2026-07-27-l1-schema-role-migration-rehearsal.json'
)
LEGACY_SUPPRESSION_OWNER = 'h_r3_migration_owner'
LEGACY_SUPPRESSION_SENDER = 'h_r3_migration_sender'
LEGACY_SUPPRESSION_RECIPIENT = 'a_r3_migration_recipient'
LEGACY_SUPPRESSION_CONVERSATION = '00000000-0000-4000-8000-00000000a3a3'
LEGACY_SUPPRESSION_LOCAL_ID = 'r3-migration-suppressed'


class RehearsalError(RuntimeError):
    """演练硬失败。"""


def _safe_target(database: str) -> str:
    """拒绝任何不带 R3 隔离标识的可写数据库。"""
    if '_im_r3_' not in database:
        raise RehearsalError(f'拒绝写入非 R3 隔离库：{database}')
    if database == SOURCE_DB:
        raise RehearsalError('源库与演练目标库不能相同')
    return database


def _command_env(password: str = '') -> dict[str, str]:
    env = os.environ.copy()
    if password:
        env['PGPASSWORD'] = password
    elif SUPERUSER_PASSWORD:
        env['PGPASSWORD'] = SUPERUSER_PASSWORD
    else:
        env.pop('PGPASSWORD', None)
    return env


def _run(
    args: list[str],
    *,
    password: str = '',
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=_command_env(password),
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


def _psql(
    database: str,
    *,
    user: str = SUPERUSER,
    password: str = '',
    sql: str | None = None,
    file: Path | None = None,
    variables: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        'psql',
        '-h',
        HOST,
        '-p',
        PORT,
        '-U',
        user,
        '-d',
        database,
        '-X',
        '-q',
        '-v',
        'ON_ERROR_STOP=1',
    ]
    for name, value in (variables or {}).items():
        args.extend(['-v', f'{name}={value}'])
    if file is not None:
        args.extend(['-f', str(file)])
        return _run(args, password=password)
    return _run(args, password=password, input_text=sql)


def _require_ok(
    proc: subprocess.CompletedProcess[str],
    label: str,
    *,
    include_error: bool = True,
) -> subprocess.CompletedProcess[str]:
    if proc.returncode == 0:
        return proc
    detail = proc.stderr.strip()[-1200:] if include_error else '敏感步骤失败，输出已隐藏'
    raise RehearsalError(f'{label}：{detail}')


def _scalar(
    database: str,
    sql: str,
    *,
    user: str = SUPERUSER,
    password: str = '',
) -> str:
    args = [
        'psql',
        '-h',
        HOST,
        '-p',
        PORT,
        '-U',
        user,
        '-d',
        database,
        '-X',
        '-q',
        '-A',
        '-t',
        '-v',
        'ON_ERROR_STOP=1',
        '-c',
        sql,
    ]
    proc = _require_ok(
        _run(args, password=password),
        f'查询失败（database={database}, user={user}）',
    )
    return proc.stdout.strip()


def _record(
    evidence: dict[str, Any],
    name: str,
    started: float,
    **detail: Any,
) -> None:
    elapsed = round(time.monotonic() - started, 3)
    evidence['steps'].append(
        {
            'name': name,
            'ok': True,
            'seconds': elapsed,
            **detail,
        }
    )
    print(f'[r3-migration] OK {name} ({elapsed:.2f}s)', flush=True)


def _preflight(evidence: dict[str, Any]) -> None:
    started = time.monotonic()
    if HOST not in {'127.0.0.1', 'localhost', '::1'}:
        raise RehearsalError(f'只允许 loopback PostgreSQL，当前 host={HOST}')
    for database in TARGET_DATABASES:
        _safe_target(database)
    if any(path.name in DEFERRED_MIGRATIONS for path in FORWARD_MIGRATIONS):
        raise RehearsalError('正向清单错误包含 permission negative 或 reverse')
    who = _scalar(
        'postgres',
        "SELECT current_user || '|' || rolsuper::text "
        'FROM pg_roles WHERE rolname = current_user',
    )
    if who != f'{SUPERUSER}|true':
        raise RehearsalError(f'演练需要本机超级用户，当前={who}')
    existing = _scalar(
        'postgres',
        "SELECT string_agg(rolname, ',') FROM pg_roles "
        "WHERE rolname IN ('astra_im_service','astra_sync_service',"
        "'astra_python_backend','astra_r3_unauthorized_probe')",
    )
    if existing:
        raise RehearsalError(
            f'演练角色已存在，拒绝覆盖其密码或权限：{existing}'
        )
    source_exists = _scalar(
        'postgres',
        f"SELECT count(*) FROM pg_database WHERE datname = '{SOURCE_DB}'",
    )
    if source_exists != '1':
        raise RehearsalError(f'当前本地源库不存在：{SOURCE_DB}')
    _record(
        evidence,
        '预检本机 PG、隔离库名、超级用户与角色空闲',
        started,
        host=HOST,
        port=PORT,
        source_database=SOURCE_DB,
    )


def _terminate_and_drop_database(database: str) -> None:
    _safe_target(database)
    sql = f"""
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '{database}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "{database}";
"""
    _require_ok(_psql('postgres', sql=sql), f'清理演练库 {database}')


def _drop_rehearsal_roles() -> None:
    existing = _scalar(
        'postgres',
        "SELECT count(*) FROM pg_roles "
        "WHERE rolname IN ('astra_im_service','astra_sync_service',"
        "'astra_python_backend','astra_r3_unauthorized_probe')",
    )
    if existing == '0':
        return
    sql = """
DROP ROLE IF EXISTS astra_r3_unauthorized_probe;
DROP ROLE IF EXISTS astra_im_service;
DROP ROLE IF EXISTS astra_sync_service;
DROP ROLE IF EXISTS astra_python_backend;
"""
    _require_ok(
        _psql('postgres', sql=sql),
        '清理演练角色',
    )


def _create_database(database: str) -> None:
    _safe_target(database)
    _terminate_and_drop_database(database)
    _require_ok(
        _psql(
            'postgres',
            sql=f'CREATE DATABASE "{database}" TEMPLATE template0;',
        ),
        f'创建演练库 {database}',
    )


def _bootstrap_empty_database(database: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            'ENVIRONMENT': 'dev',
            'DATABASE_TYPE': 'postgresql',
            'DATABASE_HOST': HOST,
            'DATABASE_PORT': PORT,
            'DATABASE_USER': SUPERUSER,
            'DATABASE_PASSWORD': SUPERUSER_PASSWORD,
            'DATABASE_SCHEMA': database,
            'DATABASE_AUTO_CREATE_TABLES': 'true',
            'HASN_IM_SCHEMA_CUTOVER': 'false',
            'IM_SERVICE_DATABASE_URL': '',
            'SYNC_SERVICE_DATABASE_URL': '',
            'PYTHON_BACKEND_DATABASE_URL': '',
        }
    )
    code = """
import asyncio
from backend.database.db import async_engine, create_tables

async def main():
    await create_tables()
    await async_engine.dispose()

asyncio.run(main())
"""
    proc = subprocess.run(
        [sys.executable, '-c', code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    _require_ok(proc, f'空基线 metadata 建表 {database}')


def _clone_current_database(database: str) -> None:
    _create_database(database)
    dump = subprocess.Popen(
        [
            'pg_dump',
            '-h',
            HOST,
            '-p',
            PORT,
            '-U',
            SUPERUSER,
            '-d',
            SOURCE_DB,
            '--no-owner',
            '--no-privileges',
        ],
        cwd=REPO_ROOT,
        env=_command_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    restore = subprocess.Popen(
        [
            'psql',
            '-h',
            HOST,
            '-p',
            PORT,
            '-U',
            SUPERUSER,
            '-d',
            database,
            '-X',
            '-q',
            '-v',
            'ON_ERROR_STOP=1',
        ],
        cwd=REPO_ROOT,
        env=_command_env(),
        stdin=dump.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if dump.stdout is not None:
        dump.stdout.close()
    _, restore_error = restore.communicate()
    _, dump_error = dump.communicate()
    if dump.returncode != 0 or restore.returncode != 0:
        raise RehearsalError(
            '当前基线克隆失败：'
            + (dump_error.strip()[-500:] or restore_error.strip()[-500:])
        )


def _run_forward(database: str) -> None:
    for path in FORWARD_MIGRATIONS:
        if path.name == '2026-07-27-r3-suppressed-command.sql':
            _seed_legacy_suppressed(database)
        _require_ok(
            _psql(database, file=path),
            f'正向迁移失败：{path.name}',
        )


def _seed_legacy_suppressed(database: str) -> None:
    """在隔离库写入旧版“先落消息、再抑制”的确定性迁移探针。"""
    _require_ok(
        _psql(
            database,
            sql=f"""
DO $$
DECLARE
    normal_message_id bigint;
    suppressed_message_id bigint;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.hasn_suppressed_messages
        WHERE owner_id = '{LEGACY_SUPPRESSION_OWNER}'
          AND conversation_id = '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid
    ) THEN
        RETURN;
    END IF;

    INSERT INTO public.hasn_conversations (
        id, type, relation_type,
        participant_a_id, participant_b_id,
        participant_a_type, participant_b_type,
        agent_policy, join_policy, max_members,
        allow_invite, allow_member_invite_agent, mute_all, member_count,
        message_count, current_seq, status, revision, created_time
    ) VALUES (
        '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid, 'direct', 'social',
        '{LEGACY_SUPPRESSION_SENDER}', '{LEGACY_SUPPRESSION_RECIPIENT}',
        'human', 'agent',
        'free', 'invite_only', 500,
        true, true, false, 2,
        0, 2, 'active', 1, now()
    )
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO public.hasn_messages (
        conversation_id, conversation_seq, owner_id,
        from_id, from_type, to_id, to_type,
        content_type, content, process_blocks,
        msg_type, status, priority, local_id,
        mentions, mention_all, context,
        edit_version, server_received_at,
        origin_node_id, origin_session_id, created_time
    ) VALUES (
        '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid, 1,
        '{LEGACY_SUPPRESSION_OWNER}',
        '{LEGACY_SUPPRESSION_SENDER}', 1,
        '{LEGACY_SUPPRESSION_RECIPIENT}', 2,
        1, '{{"text":"迁移前已存在的普通消息"}}'::jsonb, '[]'::jsonb,
        'message', 1, 'normal', 'r3-migration-normal',
        NULL, false, NULL,
        0, now() - interval '1 second',
        'node-r3-migration', 'session-r3-migration', now() - interval '1 second'
    )
    RETURNING id INTO normal_message_id;

    INSERT INTO public.hasn_messages (
        conversation_id, conversation_seq, owner_id,
        from_id, from_type, to_id, to_type,
        content_type, content, process_blocks,
        msg_type, status, priority, local_id,
        mentions, mention_all, context,
        edit_version, server_received_at,
        origin_node_id, origin_session_id, created_time
    ) VALUES (
        '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid, 2,
        '{LEGACY_SUPPRESSION_OWNER}',
        '{LEGACY_SUPPRESSION_SENDER}', 1,
        '{LEGACY_SUPPRESSION_RECIPIENT}', 2,
        1, '{{"text":"应在迁移后等待放行"}}'::jsonb, '[]'::jsonb,
        'message', 1, 'normal', '{LEGACY_SUPPRESSION_LOCAL_ID}',
        NULL, false, '{{"relation_type":"social"}}'::jsonb,
        0, now(),
        'node-r3-migration', 'session-r3-migration', now()
    )
    RETURNING id INTO suppressed_message_id;

    UPDATE public.hasn_conversations
    SET last_message_id = suppressed_message_id,
        last_message_at = now(),
        last_message_preview = '应在迁移后等待放行',
        last_message_from = '{LEGACY_SUPPRESSION_SENDER}',
        message_count = 2
    WHERE id = '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'hasn_conversation_memberships'
          AND column_name = 'member_star_id'
    ) THEN
        INSERT INTO public.hasn_conversation_memberships (
            conversation_id, member_hasn_id, member_star_id, member_name,
            member_type, role, joined_seq, left_seq, read_seq, state,
            agent_group_trust_level, agent_charter, muted, invited_by,
            charter_updated_time, joined_at, left_at, created_time
        ) VALUES
            (
                '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid,
                '{LEGACY_SUPPRESSION_SENDER}', '', '迁移探针发送者',
                'human', 'member', 1, NULL, 0, 'active',
                2, NULL, false, NULL, NULL, now(), NULL, now()
            ),
            (
                '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid,
                '{LEGACY_SUPPRESSION_RECIPIENT}', '', '迁移探针接收者',
                'agent', 'member', 1, NULL, 0, 'active',
                2, NULL, false, NULL, NULL, now(), NULL, now()
            )
        ON CONFLICT DO NOTHING;
    ELSE
        INSERT INTO public.hasn_conversation_memberships (
            conversation_id, member_hasn_id, member_type, role,
            joined_seq, left_seq, read_seq, state,
            agent_group_trust_level, agent_charter,
            joined_at, left_at, created_time
        ) VALUES
            (
                '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid,
                '{LEGACY_SUPPRESSION_SENDER}', 'human', 'member',
                1, NULL, 0, 'active',
                2, NULL, now(), NULL, now()
            ),
            (
                '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid,
                '{LEGACY_SUPPRESSION_RECIPIENT}', 'agent', 'member',
                1, NULL, 0, 'active',
                2, NULL, now(), NULL, now()
            )
        ON CONFLICT DO NOTHING;
    END IF;

    INSERT INTO public.hasn_unread_projection (
        conversation_id, member_hasn_id, unread_count,
        computed_at_seq, created_time
    ) VALUES (
        '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid,
        '{LEGACY_SUPPRESSION_RECIPIENT}', 2, 2, now()
    )
    ON CONFLICT (conversation_id, member_hasn_id)
    DO UPDATE SET unread_count = 2, computed_at_seq = 2;

    INSERT INTO public.hasn_unread_counts (
        hasn_id, conversation_id, unread_count, last_read_msg_id, created_time
    ) VALUES (
        '{LEGACY_SUPPRESSION_RECIPIENT}',
        '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid,
        2, 0, now()
    );

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'hasn_suppressed_messages'
          AND column_name = 'command_payload'
    ) THEN
        INSERT INTO public.hasn_suppressed_messages (
            message_id, owner_id, hasn_id, conversation_id,
            sender_hasn_id, idempotency_scope, command_hash, command_payload,
            suppress_reason, dispatch_status, policy_snapshot,
            runtime_summary, visible_to_owner, created_time
        ) VALUES (
            suppressed_message_id,
            '{LEGACY_SUPPRESSION_OWNER}',
            '{LEGACY_SUPPRESSION_RECIPIENT}',
            '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid,
            NULL, NULL, NULL, '{{}}'::jsonb,
            'manual_only', 'suppressed_by_policy', '{{}}'::jsonb,
            '{{}}'::jsonb, true, now()
        );
    ELSE
        INSERT INTO public.hasn_suppressed_messages (
            message_id, owner_id, hasn_id, conversation_id,
            suppress_reason, dispatch_status, policy_snapshot,
            runtime_summary, visible_to_owner, created_time
        ) VALUES (
            suppressed_message_id,
            '{LEGACY_SUPPRESSION_OWNER}',
            '{LEGACY_SUPPRESSION_RECIPIENT}',
            '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid,
            'manual_only', 'suppressed_by_policy', '{{}}'::jsonb,
            '{{}}'::jsonb, true, now()
        );
    END IF;
END
$$;
""",
        ),
        '写入旧版抑制消息迁移探针',
    )


def _assert_legacy_suppression_migrated(database: str) -> None:
    """验证历史抑制消息已变成可重放命令且所有投影一致。"""
    shape = _scalar(
        database,
        f"""
SELECT
  (s.message_id IS NULL)::int || '|' ||
  (s.sender_hasn_id = '{LEGACY_SUPPRESSION_SENDER}')::int || '|' ||
  (s.command_payload->>'idempotency_key' = '{LEGACY_SUPPRESSION_LOCAL_ID}')::int || '|' ||
  (s.command_payload->'content'->>'text' = '应在迁移后等待放行')::int || '|' ||
  length(s.idempotency_scope) || '|' ||
  length(s.command_hash) || '|' ||
  (SELECT count(*) FROM hasn_im.hasn_messages m
   WHERE m.conversation_id = s.conversation_id) || '|' ||
  c.message_count || '|' ||
  c.current_seq || '|' ||
  c.last_message_preview || '|' ||
  p.unread_count || '|' ||
  u.unread_count
FROM hasn_im.hasn_suppressed_messages s
JOIN hasn_im.hasn_conversations c ON c.id = s.conversation_id
JOIN hasn_im.hasn_unread_projection p
  ON p.conversation_id = s.conversation_id
 AND p.member_hasn_id = '{LEGACY_SUPPRESSION_RECIPIENT}'
JOIN public.hasn_unread_counts u
  ON u.conversation_id = s.conversation_id
 AND u.hasn_id = '{LEGACY_SUPPRESSION_RECIPIENT}'
WHERE s.owner_id = '{LEGACY_SUPPRESSION_OWNER}'
  AND s.conversation_id = '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid;
""",
    )
    expected_shape = (
        '1|1|1|1|64|64|1|1|2|迁移前已存在的普通消息|1|1'
    )
    if shape != expected_shape:
        raise RehearsalError(
            f'旧抑制消息迁移形态错误：actual={shape!r}'
        )

    command_payload = json.loads(
        _scalar(
            database,
            f"""
SELECT command_payload::text
FROM hasn_im.hasn_suppressed_messages
WHERE owner_id = '{LEGACY_SUPPRESSION_OWNER}'
  AND conversation_id = '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid;
""",
        )
    )
    actual_scope = _scalar(
        database,
        f"""
SELECT idempotency_scope
FROM hasn_im.hasn_suppressed_messages
WHERE owner_id = '{LEGACY_SUPPRESSION_OWNER}'
  AND conversation_id = '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid;
""",
    )
    actual_hash = _scalar(
        database,
        f"""
SELECT command_hash
FROM hasn_im.hasn_suppressed_messages
WHERE owner_id = '{LEGACY_SUPPRESSION_OWNER}'
  AND conversation_id = '{LEGACY_SUPPRESSION_CONVERSATION}'::uuid;
""",
    )
    expected_scope = hashlib.sha256(
        '\0'.join(
            (
                LEGACY_SUPPRESSION_SENDER,
                'node-r3-migration',
                LEGACY_SUPPRESSION_LOCAL_ID,
            )
        ).encode()
    ).hexdigest()
    canonical_payload = json.dumps(
        command_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    expected_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
    if actual_scope != expected_scope or actual_hash != expected_hash:
        raise RehearsalError(
            '旧抑制命令的幂等作用域或规范化载荷指纹与应用算法不一致'
        )


def _assert_forward_shape(database: str) -> dict[str, int]:
    shape = _scalar(
        database,
        """
SELECT
  (to_regclass('hasn_im.hasn_messages') IS NOT NULL)::int || '|' ||
  (to_regclass('hasn_im.integration_events') IS NOT NULL)::int || '|' ||
  (to_regclass('hasn_sync.hasn_sync_events') IS NOT NULL)::int || '|' ||
  (to_regclass('public.hasn_messages') IS NULL)::int || '|' ||
  (SELECT count(*) FROM pg_roles
   WHERE rolname IN ('astra_im_service','astra_sync_service','astra_python_backend'));
""",
    )
    if shape != '1|1|1|1|3':
        raise RehearsalError(f'正向结构断言失败：{shape}')
    settings_and_outbox = _scalar(
        database,
        """
SELECT
  (to_regclass('hasn_im.agent_communication_settings') IS NOT NULL)::int || '|' ||
  (to_regclass('public.agent_communication_settings') IS NULL)::int || '|' ||
  (to_regclass('public.hasn_relation_command_outbox') IS NOT NULL)::int || '|' ||
  (to_regclass('hasn_task.task_dispatch_outbox') IS NOT NULL)::int || '|' ||
  (EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.hasn_relation_command_outbox'::regclass
      AND conname = 'uq_hasn_relation_command_outbox_idempotency'
  ))::int || '|' ||
  (EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'hasn_im.hasn_contacts'::regclass
      AND conname = 'uq_hasn_contact_relation'
  ))::int || '|' ||
  (EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'hasn_sync.hasn_sync_inbox_events'::regclass
      AND conname = 'uq_hasn_sync_inbox_client_event'
  ))::int || '|' ||
  (EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'hasn_sync'
      AND table_name = 'hasn_sync_inbox_events'
      AND column_name = 'attempt_count'
      AND column_default = '0'
  ))::int || '|' ||
  (EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.hasn_sync_business_receipts'::regclass
      AND conname = 'uq_hasn_sync_business_receipt_key'
  ))::int || '|' ||
  (EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.hasn_sync_business_receipts'::regclass
      AND conname = 'uq_hasn_sync_business_receipt_event'
  ))::int;
""",
    )
    if settings_and_outbox != '1|1|1|1|1|1|1|1|1|1':
        raise RehearsalError(
            '通信设置、联系人/关系命令/sync inbox 幂等约束或任务派发 outbox 归属错误：'
            f'{settings_and_outbox}'
        )
    _assert_legacy_suppression_migrated(database)
    read_backfill_exceptions = int(
        _scalar(
            database,
            'SELECT count(*) '
            'FROM hasn_im.membership_read_backfill_exceptions',
        )
    )
    if read_backfill_exceptions != 0:
        raise RehearsalError(
            f'read_seq 回填异常清单非空：{read_backfill_exceptions}'
        )
    return {
        'messages': int(
            _scalar(database, 'SELECT count(*) FROM hasn_im.hasn_messages')
        ),
        'memberships': int(
            _scalar(
                database,
                'SELECT count(*) FROM hasn_im.hasn_conversation_memberships',
            )
        ),
        'read_backfill_exceptions': read_backfill_exceptions,
    }


def _enable_service_logins(database: str, passwords: dict[str, str]) -> None:
    variables = {
        'im_service_password': passwords['astra_im_service'],
        'sync_service_password': passwords['astra_sync_service'],
        'python_backend_password': passwords['astra_python_backend'],
    }
    # 此步骤错误输出可能包含服务端回显的 SQL，证据中一律隐藏。
    _require_ok(
        _psql(database, file=LOGIN_OPERATION, variables=variables),
        '启用三个服务 LOGIN',
        include_error=False,
    )


def _create_permission_probe(
    database: str,
    unauthorized_password: str,
) -> None:
    _require_ok(
        _psql(
            database,
            sql=f"""
CREATE TABLE public.astra_r3_permission_probe (marker text PRIMARY KEY);
CREATE ROLE {UNAUTHORIZED_ROLE} LOGIN;
GRANT CONNECT ON DATABASE "{database}" TO {UNAUTHORIZED_ROLE};
GRANT USAGE ON SCHEMA hasn_sync TO {UNAUTHORIZED_ROLE};
""",
        ),
        '创建权限演练探针',
    )
    _require_ok(
        _psql(
            'postgres',
            variables={'probe_password': unauthorized_password},
            sql=(
                f'ALTER ROLE {UNAUTHORIZED_ROLE} '
                "PASSWORD :'probe_password';"
            ),
        ),
        '设置未授权探针 LOGIN',
        include_error=False,
    )


def _run_permission_matrix(
    database: str,
    passwords: dict[str, str],
) -> list[str]:
    users: list[str] = []
    for role in (*SERVICE_ROLES, UNAUTHORIZED_ROLE):
        password = passwords[role]
        actual = _scalar(
            database,
            'SELECT current_user',
            user=role,
            password=password,
        )
        if actual != role:
            raise RehearsalError(
                f'current_user 错误：登录={role}，实际={actual}'
            )
        proc = _require_ok(
            _psql(
                database,
                user=role,
                password=password,
                file=PERMISSION_TEST,
                variables={'expected_role': role},
            ),
            f'权限矩阵失败：{role}',
        )
        if 'R3 权限矩阵通过' not in (proc.stdout + proc.stderr):
            raise RehearsalError(f'权限脚本缺少通过标记：{role}')
        users.append(actual)
    if _scalar(
        database,
        'SELECT count(*) FROM public.astra_r3_permission_probe',
    ) != '0':
        raise RehearsalError('权限测试事务未回滚，探针表出现残留行')
    public_execute = _scalar(
        database,
        "SELECT has_function_privilege("
        f"'{UNAUTHORIZED_ROLE}',"
        "'hasn_sync.append_event(text,text,text,text,text,jsonb,text,text,timestamptz)',"
        "'EXECUTE')",
    )
    if public_execute != 'f':
        raise RehearsalError('未授权 LOGIN 仍可执行 hasn_sync.append_event')
    return users


def _drop_unauthorized_role(database: str) -> None:
    _require_ok(
        _psql(
            database,
            sql=f"""
DROP OWNED BY {UNAUTHORIZED_ROLE};
DROP ROLE {UNAUTHORIZED_ROLE};
""",
        ),
        '清理未授权探针角色',
    )


def _run_reverse(database: str, before: dict[str, int]) -> None:
    _require_ok(
        _psql(database, file=REVERSE_MIGRATION),
        '反向迁移失败',
    )
    shape = _scalar(
        database,
        """
SELECT
  (to_regclass('public.hasn_messages') IS NOT NULL)::int || '|' ||
  (to_regclass('public.hasn_im_integration_events') IS NOT NULL)::int || '|' ||
  (to_regclass('public.hasn_sync_events') IS NOT NULL)::int || '|' ||
  (to_regnamespace('hasn_im') IS NULL)::int || '|' ||
  (SELECT count(*) FROM pg_roles
   WHERE rolname IN ('astra_im_service','astra_sync_service','astra_python_backend')) || '|' ||
  has_function_privilege(
    'public',
    'hasn_sync.append_event(text,text,text,text,text,jsonb,text,text,timestamptz)',
    'EXECUTE'
  )::int;
""",
    )
    if shape != '1|1|1|1|0|1':
        raise RehearsalError(f'反向结构断言失败：{shape}')
    after_messages = int(
        _scalar(database, 'SELECT count(*) FROM public.hasn_messages')
    )
    if after_messages != before['messages']:
        raise RehearsalError(
            f'反向消息数变化：before={before["messages"]}, after={after_messages}'
        )


def _assert_three_engines(
    database: str,
    passwords: dict[str, str],
) -> list[str]:
    def dsn(role: str) -> str:
        encoded = quote(passwords[role], safe='')
        return (
            f'postgresql+asyncpg://{role}:{encoded}@{HOST}:{PORT}/{database}'
        )

    env = os.environ.copy()
    env.update(
        {
            'ENVIRONMENT': 'prod',
            'DATABASE_TYPE': 'postgresql',
            'DATABASE_HOST': HOST,
            'DATABASE_PORT': PORT,
            'DATABASE_USER': SUPERUSER,
            'DATABASE_PASSWORD': SUPERUSER_PASSWORD,
            'DATABASE_SCHEMA': database,
            'DATABASE_AUTO_CREATE_TABLES': 'true',
            'HASN_IM_SCHEMA_CUTOVER': 'true',
            'HASN_WS_MIN_CLIENT_VERSION': R3_COMPANION_DAEMON_VERSION,
            'IM_SERVICE_DATABASE_URL': dsn('astra_im_service'),
            'SYNC_SERVICE_DATABASE_URL': dsn('astra_sync_service'),
            'PYTHON_BACKEND_DATABASE_URL': dsn('astra_python_backend'),
        }
    )
    code = """
import asyncio
import json
from sqlalchemy import text
from backend.database.db import (
    create_tables,
    im_service_db_session,
    im_service_engine,
    python_backend_db_session,
    python_backend_engine,
    sync_service_db_session,
    sync_service_engine,
)

async def main():
    await create_tables()
    pairs = (
        (im_service_db_session, 'astra_im_service'),
        (sync_service_db_session, 'astra_sync_service'),
        (python_backend_db_session, 'astra_python_backend'),
    )
    users = []
    for maker, expected in pairs:
        async with maker() as session:
            actual = (await session.execute(text('SELECT current_user'))).scalar_one()
            if actual != expected:
                raise RuntimeError(f'current_user={actual}, expected={expected}')
            users.append(actual)
    engines = (im_service_engine, sync_service_engine, python_backend_engine)
    if len({id(engine.pool) for engine in engines}) != 3:
        raise RuntimeError('三个角色连接池未独立')
    for engine in engines:
        await engine.dispose()
    print(json.dumps(users))

asyncio.run(main())
"""
    proc = subprocess.run(
        [sys.executable, '-c', code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    _require_ok(proc, '三个真实 engine/session maker current_user 验收')
    users = json.loads(proc.stdout.strip().splitlines()[-1])
    if users != list(SERVICE_ROLES):
        raise RehearsalError(f'三 engine 用户断言失败：{users}')
    return users


def _cloud_environment(
    database: str,
    *,
    cutover: bool,
    passwords: dict[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            'ENVIRONMENT': 'prod',
            'DATABASE_TYPE': 'postgresql',
            'DATABASE_HOST': HOST,
            'DATABASE_PORT': PORT,
            'DATABASE_USER': SUPERUSER,
            'DATABASE_PASSWORD': SUPERUSER_PASSWORD,
            'DATABASE_SCHEMA': database,
            'DATABASE_AUTO_CREATE_TABLES': 'true',
            'HASN_IM_SCHEMA_CUTOVER': 'true' if cutover else 'false',
            'HASN_WS_MIN_CLIENT_VERSION': (
                R3_COMPANION_DAEMON_VERSION if cutover else ''
            ),
            'IM_SERVICE_DATABASE_URL': '',
            'SYNC_SERVICE_DATABASE_URL': '',
            'PYTHON_BACKEND_DATABASE_URL': '',
        }
    )
    if not cutover:
        return env
    if passwords is None:
        raise RehearsalError('cutover cloud 启动缺少三个角色密码')

    def dsn(role: str) -> str:
        encoded = quote(passwords[role], safe='')
        return (
            f'postgresql+asyncpg://{role}:{encoded}@{HOST}:{PORT}/{database}'
        )

    env.update(
        {
            'IM_SERVICE_DATABASE_URL': dsn('astra_im_service'),
            'SYNC_SERVICE_DATABASE_URL': dsn('astra_sync_service'),
            'PYTHON_BACKEND_DATABASE_URL': dsn('astra_python_backend'),
        }
    )
    return env


def _redact(text: str, passwords: dict[str, str] | None) -> str:
    redacted = text
    for password in (passwords or {}).values():
        redacted = redacted.replace(password, '<已脱敏>')
        redacted = redacted.replace(quote(password, safe=''), '<已脱敏>')
    return redacted


def _assert_cloud_startup(
    database: str,
    *,
    cutover: bool,
    passwords: dict[str, str] | None = None,
) -> None:
    """启动真实 FastAPI lifespan，并通过真实 HTTP /metrics 证明服务已就绪。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        port = int(probe.getsockname()[1])

    proc = subprocess.Popen(
        [
            sys.executable,
            '-m',
            'uvicorn',
            'backend.main:app',
            '--host',
            '127.0.0.1',
            '--port',
            str(port),
            '--log-level',
            'warning',
        ],
        cwd=REPO_ROOT,
        env=_cloud_environment(
            database,
            cutover=cutover,
            passwords=passwords,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = False
    # 首次导入全量应用模块与初始化多进程资源在冷缓存机器上可能超过 30 秒。
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                with url_request.urlopen(
                    f'http://127.0.0.1:{port}/metrics',
                    timeout=1,
                ) as response:
                    if response.status == 200:
                        ready = True
                        break
            except (url_error.URLError, TimeoutError):
                time.sleep(0.2)
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
    combined = _redact(stdout + stderr, passwords)
    if not ready:
        raise RehearsalError(
            'cloud 启动/HTTP 就绪失败：' + combined.strip()[-1600:]
        )
    forbidden = (
        'Application startup failed',
        'UndefinedTable',
        'permission denied',
        '应用目录播种失败',
    )
    findings = [token for token in forbidden if token in combined]
    if findings:
        raise RehearsalError(
            f'cloud 启动日志存在失败信号 {findings}：'
            + combined.strip()[-1600:]
        )


def _run_atomic_failure_probe(evidence: dict[str, Any]) -> None:
    started = time.monotonic()
    _create_database(FAILURE_DB)
    try:
        proc = _psql(FAILURE_DB, file=FORWARD_MIGRATIONS[-1])
        if proc.returncode == 0:
            raise RehearsalError('缺表故障注入竟未失败')
        residue = _scalar(
            FAILURE_DB,
            """
SELECT
  (to_regnamespace('hasn_im') IS NOT NULL)::int || '|' ||
  (SELECT count(*) FROM pg_roles
   WHERE rolname IN ('astra_im_service','astra_sync_service','astra_python_backend'));
""",
        )
        if residue != '0|0':
            raise RehearsalError(f'失败后存在半迁移残留：{residue}')
        _record(
            evidence,
            '正向迁移故障注入后单事务全回滚',
            started,
            residue=residue,
        )
    finally:
        _terminate_and_drop_database(FAILURE_DB)


def _run_baseline(
    evidence: dict[str, Any],
    *,
    label: str,
    database: str,
    current_clone: bool,
) -> None:
    started = time.monotonic()
    try:
        if current_clone:
            _clone_current_database(database)
            source_messages = int(
                _scalar(SOURCE_DB, 'SELECT count(*) FROM public.hasn_messages')
            )
            cloned_messages = int(
                _scalar(database, 'SELECT count(*) FROM public.hasn_messages')
            )
            if cloned_messages != source_messages:
                raise RehearsalError(
                    f'快照克隆不保真：source={source_messages}, clone={cloned_messages}'
                )
        else:
            _create_database(database)
            _bootstrap_empty_database(database)

        _assert_cloud_startup(database, cutover=False)
        _run_forward(database)
        before = _assert_forward_shape(database)
        passwords = {
            role: secrets.token_hex(24)
            for role in ALL_REHEARSAL_ROLES
        }
        _enable_service_logins(database, passwords)
        _create_permission_probe(
            database,
            passwords[UNAUTHORIZED_ROLE],
        )
        permission_users = _run_permission_matrix(database, passwords)
        _drop_unauthorized_role(database)

        _run_reverse(database, before)
        _assert_cloud_startup(database, cutover=False)
        _run_forward(database)
        after = _assert_forward_shape(database)
        if after['messages'] != before['messages']:
            raise RehearsalError(
                f'二次正向消息数变化：before={before}, after={after}'
            )
        final_passwords = {
            role: secrets.token_hex(24)
            for role in ALL_REHEARSAL_ROLES
        }
        _enable_service_logins(database, final_passwords)
        engine_users = _assert_three_engines(database, final_passwords)
        _assert_cloud_startup(
            database,
            cutover=True,
            passwords=final_passwords,
        )

        _record(
            evidence,
            f'{label} forward→negative→reverse→forward',
            started,
            database=database,
            row_counts=after,
            permission_current_users=permission_users,
            engine_current_users=engine_users,
            permission_writes_rolled_back=True,
            cloud_pre_cutover_started=True,
            cloud_after_reverse_started=True,
            cloud_post_cutover_started=True,
        )
    finally:
        _terminate_and_drop_database(database)
        _drop_rehearsal_roles()


def main() -> int:
    started_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    evidence: dict[str, Any] = {
        'rehearsal': 'im_r3_schema_roles_forward_reverse',
        'started_at': started_at,
        'status': 'running',
        'safety': {
            'loopback_only': True,
            'target_name_requires': '_im_r3_',
            'source_database_read_only_dump': SOURCE_DB,
            'passwords_recorded': False,
        },
        'forward_migrations': [path.name for path in FORWARD_MIGRATIONS],
        'deferred_migrations': sorted(DEFERRED_MIGRATIONS),
        'steps': [],
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        _preflight(evidence)
        _run_atomic_failure_probe(evidence)
        _run_baseline(
            evidence,
            label='空开发基线',
            database=EMPTY_DB,
            current_clone=False,
        )
        _run_baseline(
            evidence,
            label='当前本地快照基线',
            database=CURRENT_DB,
            current_clone=True,
        )
        evidence['status'] = 'passed'
        return_code = 0
    except Exception as exc:
        evidence['status'] = 'failed'
        evidence['failure'] = str(exc)
        print(f'[r3-migration] FAIL {exc}', file=sys.stderr)
        return_code = 1
    finally:
        for database in TARGET_DATABASES:
            try:
                _terminate_and_drop_database(database)
            except Exception:
                pass
        try:
            _drop_rehearsal_roles()
        except Exception:
            pass
        evidence['completed_at'] = time.strftime(
            '%Y-%m-%dT%H:%M:%SZ',
            time.gmtime(),
        )
        EVIDENCE_PATH.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    if return_code == 0:
        print(
            f'[r3-migration] 全链通过，证据：{EVIDENCE_PATH}',
            flush=True,
        )
    return return_code


if __name__ == '__main__':
    raise SystemExit(main())
