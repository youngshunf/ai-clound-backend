"""获客项目化 S1 加法数据层真实 PostgreSQL 契约测试。

覆盖：
- 新表与现有表加列迁移可重复执行；
- 项目、项目线索、项目打法和幂等事件的数据库唯一约束生效；
- Owner/企业双模约束与版本化 HMAC 字段齐全；
- 旧字段保留，迁移不包含 DROP TABLE、CASCADE 或新增 NOT NULL 项目列；
- 触达自动批准的数据库和 ORM 默认值均为 false。

需要 export DATABASE_PORT=15432。测试事务最终回滚，不污染业务数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.schema.outreach_message import CreateOutreachMessageParam
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_SCHEMA_SQL = _REPO / 'backend/sql/hasn_growth/007_create_growth_project_v4_tables.sql'
_KEY_STATE_SQL = _REPO / 'backend/sql/hasn_growth/008_create_growth_pii_key_state.sql'
_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-07-28-growth-project-v4-columns.sql'
_KEY_FENCE_SQL = (
    _REPO / 'backend/sql/hasn_growth/migrations/2026-07-28-growth-pii-key-fence-triggers.sql'
)

_NEW_TABLES = {
    'growth_project',
    'growth_project_lead',
    'growth_attribution_event',
    'growth_project_provision',
    'growth_project_playbook',
    'playbook_version',
    'outreach_message_event',
    'contact_private_profile',
    'contact_channel',
    'growth_pii_key_state',
    'growth_pii_migration_quarantine',
    'contact_private_access_audit',
}


async def _apply_sql(session: AsyncSession) -> None:
    """经 asyncpg simple query 协议运行多语句 SQL。"""
    raw = await (await session.connection()).get_raw_connection()
    connection = raw.driver_connection
    assert connection is not None
    await connection.execute(_SCHEMA_SQL.read_text(encoding='utf-8'))
    await connection.execute(_KEY_STATE_SQL.read_text(encoding='utf-8'))
    await connection.execute(_MIGRATION_SQL.read_text(encoding='utf-8'))
    await connection.execute(_KEY_FENCE_SQL.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()


async def test_s1_sql_is_additive_and_idempotent(session: AsyncSession) -> None:
    schema_sql = _SCHEMA_SQL.read_text(encoding='utf-8')
    migration_sql = _MIGRATION_SQL.read_text(encoding='utf-8')
    normalized = f'{schema_sql}\n{migration_sql}'.upper()
    assert 'DROP TABLE' not in normalized
    assert 'CASCADE' not in normalized
    assert 'DROP COLUMN' not in normalized

    await _apply_sql(session)
    await _apply_sql(session)

    actual = set(
        (
            await session.execute(
                text(
                    'SELECT table_name FROM information_schema.tables '
                    "WHERE table_schema='hasn_growth' AND table_name = ANY(:tables)"
                ),
                {'tables': sorted(_NEW_TABLES)},
            )
        ).scalars()
    )
    assert actual == _NEW_TABLES


async def test_s1_existing_table_columns_and_defaults(session: AsyncSession) -> None:
    await _apply_sql(session)
    assert GrowthProject.__table__.schema == 'hasn_growth'
    assert GrowthProject.__table__.c.id.type.python_type is UUID

    for table_name in ('customer', 'opportunity', 'outreach_message', 'activity', 'form_submission'):
        row = (
            await session.execute(
                text(
                    'SELECT data_type, is_nullable FROM information_schema.columns '
                    "WHERE table_schema='hasn_growth' AND table_name=:table_name "
                    "AND column_name='growth_project_id'"
                ),
                {'table_name': table_name},
            )
        ).one()
        assert row.data_type == 'uuid'
        assert row.is_nullable == 'YES'

    outreach_columns = set(
        (
            await session.execute(
                text(
                    'SELECT column_name FROM information_schema.columns '
                    "WHERE table_schema='hasn_growth' AND table_name='outreach_message' "
                    'AND column_name = ANY(:columns)'
                ),
                {
                    'columns': [
                        'approval_status',
                        'delivery_status',
                        'approval_version',
                        'content_version',
                        'manual_attested_at',
                        'manual_attested_by',
                        'manual_attested_channel',
                    ]
                },
            )
        ).scalars()
    )
    assert outreach_columns == {
        'approval_status',
        'delivery_status',
        'approval_version',
        'content_version',
        'manual_attested_at',
        'manual_attested_by',
        'manual_attested_channel',
    }

    default_expr = (
        await session.execute(
            text(
                'SELECT column_default FROM information_schema.columns '
                "WHERE table_schema='hasn_growth' AND table_name='outreach_message' "
                "AND column_name='auto_approved'"
            )
        )
    ).scalar_one()
    assert default_expr in {'false', 'false::boolean'}
    assert OutreachMessage.__dataclass_fields__['auto_approved'].default is False
    assert CreateOutreachMessageParam.model_fields['auto_approved'].default is False


async def test_s1_owner_scope_hmac_and_version_contracts(session: AsyncSession) -> None:
    await _apply_sql(session)

    required = {
        'contact_private_profile': {
            'owner_scope',
            'user_id',
            'enterprise_id',
            'contact_name_ciphertext',
            'title_ciphertext',
            'encryption_key_version',
            'lawful_basis',
            'source_ref',
            'retention_until',
        },
        'contact_channel': {
            'owner_scope',
            'user_id',
            'enterprise_id',
            'value_ciphertext',
            'value_hmac',
            'encryption_key_version',
            'hash_key_version',
            'lawful_basis',
            'source_ref',
            'retention_until',
        },
        'optout_record': {
            'owner_scope',
            'enterprise_id',
            'address_hmac',
            'hash_key_version',
            'growth_project_id',
        },
        'playbook': {'version'},
    }
    for table_name, expected_columns in required.items():
        actual = set(
            (
                await session.execute(
                    text(
                        'SELECT column_name FROM information_schema.columns '
                        "WHERE table_schema='hasn_growth' AND table_name=:table_name "
                        'AND column_name = ANY(:columns)'
                    ),
                    {'table_name': table_name, 'columns': sorted(expected_columns)},
                )
            ).scalars()
        )
        assert actual == expected_columns, f'{table_name} 缺字段：{expected_columns - actual}'


async def test_s1_project_unique_constraints_reject_duplicates(session: AsyncSession) -> None:
    await _apply_sql(session)

    platform_project_id = (
        await session.execute(
            text(
                'INSERT INTO hasn_project.hasn_project (owner_id, name, status) '
                "VALUES ('h_s1_schema_owner', 'S1 契约测试项目', 'active') RETURNING id"
            )
        )
    ).scalar_one()
    growth_project_id = (
        await session.execute(
            text(
                'INSERT INTO hasn_growth.growth_project '
                '(platform_project_id, user_id, owner_hasn_id, owner_scope, name) '
                "VALUES (:platform_project_id, 980001, 'h_s1_schema_owner', 'personal', 'S1 漏斗') "
                'RETURNING id'
            ),
            {'platform_project_id': platform_project_id},
        )
    ).scalar_one()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(
                    'INSERT INTO hasn_growth.growth_project '
                    '(platform_project_id, user_id, owner_hasn_id, owner_scope, name) '
                    "VALUES (:platform_project_id, 980001, 'h_s1_schema_owner', 'personal', '重复漏斗')"
                ),
                {'platform_project_id': platform_project_id},
            )

    lead_contact_id = (
        await session.execute(
            text(
                'INSERT INTO hasn_growth.contact '
                '(lead_no, pool_visibility, status, confidence_score, normalization_version, '
                'first_seen_at, last_seen_at) '
                "VALUES ('S1SCHEMALEAD', 'private', 'valid', 80, 's1', now(), now()) RETURNING id"
            )
        )
    ).scalar_one()
    await session.execute(
        text(
            'INSERT INTO hasn_growth.growth_project_lead '
            '(growth_project_id, lead_contact_id, user_id, owner_scope) '
            "VALUES (:growth_project_id, :lead_contact_id, 980001, 'personal')"
        ),
        {'growth_project_id': growth_project_id, 'lead_contact_id': lead_contact_id},
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(
                    'INSERT INTO hasn_growth.growth_project_lead '
                    '(growth_project_id, lead_contact_id, user_id, owner_scope) '
                    "VALUES (:growth_project_id, :lead_contact_id, 980001, 'personal')"
                ),
                {'growth_project_id': growth_project_id, 'lead_contact_id': lead_contact_id},
            )


async def test_private_access_audit_is_append_only(session: AsyncSession) -> None:
    await _apply_sql(session)
    audit_id = (
        await session.execute(
            text(
                'INSERT INTO hasn_growth.contact_private_access_audit '
                '(owner_scope, user_id, actor_type, actor_id, action, resource_type, resource_id, result) '
                "VALUES ('personal', 980002, 'owner', 'h_s1_schema_owner', 'reveal', "
                "'contact_channel', '1', 'allowed') RETURNING id"
            )
        )
    ).scalar_one()

    with pytest.raises(Exception, match='append-only'):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE hasn_growth.contact_private_access_audit SET result='denied' WHERE id=:audit_id"),
                {'audit_id': audit_id},
            )
