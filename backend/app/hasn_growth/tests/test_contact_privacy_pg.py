"""获客项目化 S1 联系人 PII 真实 PostgreSQL 测试。

覆盖：
- 独立加密/HMAC 密钥缺失时 fail-closed，不生成随机 fallback；
- 新写只落应用层密文和当前版本 HMAC，返回值不含密文或明文副本；
- Owner 仅能 reveal 自己主体下的单个渠道，Agent reveal 被拒；
- reveal 允许、拒绝均写独立事务的追加式审计，审计不含 PII；
- HMAC 轮换期查询当前与保留版本，旧 SHA256 行只读兼容且新写不再产生。
"""

from __future__ import annotations

import asyncio
import hashlib

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.admin.model.user import User
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.api.v1.agent.growth import router as agent_growth_router
from backend.app.hasn_growth.api.v1.app.growth import router as app_growth_router
from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.contact_private_access_audit import ContactPrivateAccessAudit
from backend.app.hasn_growth.model.contact_private_profile import ContactPrivateProfile
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_pii_key_state import GrowthPiiKeyState
from backend.app.hasn_growth.model.optout_record import OptoutRecord
from backend.app.hasn_growth.schema.optout_record import (
    CreateOptoutRecordParam,
    DeleteOptoutRecordParam,
)
from backend.app.hasn_growth.service.contact_privacy_service import ContactChannelWrite, contact_privacy_service
from backend.app.hasn_growth.service.optout_record_service import optout_record_service
from backend.app.hasn_growth.service.pii_keyring import (
    GrowthPiiCiphertextError,
    GrowthPiiKeyConfigurationError,
    GrowthPiiKeyring,
    get_growth_pii_keyring,
    require_growth_pii_keyring,
)
from backend.app.hasn_growth.service.scope_context import GrowthScope
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.common.exception import errors
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.jwt import create_access_token, revoke_token
from backend.core.conf import settings
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.redis import redis_client
from backend.middleware.jwt_auth_middleware import JwtAuthMiddleware

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


def _keyring() -> GrowthPiiKeyring:
    return GrowthPiiKeyring(
        encryption_keys={1: b'\x11' * 32, 2: b'\x22' * 32},
        hmac_keys={1: b'\x33' * 32, 2: b'\x44' * 32},
        active_encryption_version=2,
        active_hmac_version=2,
    )


async def _apply_sql(session: AsyncSession) -> None:
    raw = await (await session.connection()).get_raw_connection()
    connection = raw.driver_connection
    assert connection is not None
    await connection.execute(_SCHEMA_SQL.read_text(encoding='utf-8'))
    await connection.execute(_KEY_STATE_SQL.read_text(encoding='utf-8'))
    await connection.execute(_MIGRATION_SQL.read_text(encoding='utf-8'))
    await connection.execute(_KEY_FENCE_SQL.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    trace_prefix = f's1-pii-{uuid4()}'
    db.info['trace_prefix'] = trace_prefix
    try:
        await _apply_sql(db)
        yield db
    finally:
        await db.rollback()
        await db.close()
        async with engine.begin() as connection:
            # 测试审计使用独立提交；清理事务按唯一 trace 精确移除测试数据后恢复门禁。
            await connection.execute(text('ALTER TABLE hasn_growth.contact_private_access_audit DISABLE TRIGGER USER'))
            await connection.execute(
                text('DELETE FROM hasn_growth.contact_private_access_audit WHERE trace_id LIKE :trace_pattern'),
                {'trace_pattern': f'{trace_prefix}%'},
            )
            await connection.execute(text('ALTER TABLE hasn_growth.contact_private_access_audit ENABLE TRIGGER USER'))
        await engine.dispose()


async def _lead_contact_id(session: AsyncSession, *, suffix: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    'INSERT INTO hasn_growth.contact '
                    '(lead_no, pool_visibility, status, confidence_score, normalization_version, '
                    'first_seen_at, last_seen_at) '
                    "VALUES (:lead_no, 'private', 'valid', 80, 's1-pii', now(), now()) RETURNING id"
                ),
                {'lead_no': f'S1PII{suffix}{uuid4().hex[:8]}'},
            )
        ).scalar_one()
    )


async def _stored_channel(session: AsyncSession) -> tuple[int, str]:
    plaintext = 'Sales.Owner@Example.com'
    result = await contact_privacy_service.store_private_contact(
        session,
        keyring=_keyring(),
        lead_contact_id=await _lead_contact_id(session, suffix='STORE'),
        owner_scope='personal',
        user_id=981001,
        enterprise_id=None,
        contact_name='王小明',
        title='销售总监',
        lawful_basis='public_business_contact',
        source_ref='hasn://asset/s1-pii-evidence',
        retention_until=datetime.now(UTC) + timedelta(days=90),
        channels=[
            ContactChannelWrite(
                channel='email',
                value=plaintext,
                lawful_basis='public_business_contact',
                source_ref='hasn://asset/s1-pii-evidence',
            )
        ],
    )
    return int(result['channels'][0]['id']), plaintext


async def test_keyring_fails_closed_and_supports_rotation() -> None:  # ruff: ignore[unused-async]
    with pytest.raises(GrowthPiiKeyConfigurationError, match='加密密钥'):
        GrowthPiiKeyring(
            encryption_keys={},
            hmac_keys={1: b'\x33' * 32},
            active_encryption_version=1,
            active_hmac_version=1,
        )
    with pytest.raises(GrowthPiiKeyConfigurationError, match='不得复用'):
        GrowthPiiKeyring(
            encryption_keys={1: b'\x55' * 32},
            hmac_keys={1: b'\x55' * 32},
            active_encryption_version=1,
            active_hmac_version=1,
        )

    keyring = _keyring()
    ciphertext = keyring.encrypt('敏感值', purpose='contact_channel')
    assert '敏感值' not in ciphertext
    assert keyring.decrypt(ciphertext, version=2, purpose='contact_channel') == '敏感值'
    with pytest.raises(GrowthPiiCiphertextError, match='无效或已被篡改'):
        keyring.decrypt(f'{ciphertext[:-2]}!!', version=2, purpose='contact_channel')
    with pytest.raises(GrowthPiiCiphertextError, match='无效或已被篡改'):
        keyring.decrypt(ciphertext, version=2, purpose='other_contact_channel')
    assert {item.version for item in keyring.hmac_candidates('email', 'Sales.Owner@Example.com')} == {1, 2}


async def test_runtime_keyring_configuration_fails_closed() -> None:  # ruff: ignore[unused-async]
    previous = (
        settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
        settings.GROWTH_PII_HMAC_KEYS_JSON,
        settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
        settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
    )
    settings.GROWTH_PII_ENCRYPTION_KEYS_JSON = ''
    settings.GROWTH_PII_HMAC_KEYS_JSON = ''
    settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION = 0
    settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION = 0
    get_growth_pii_keyring.cache_clear()
    try:
        with pytest.raises(errors.ServerError, match='暂不可用'):
            require_growth_pii_keyring()
    finally:
        (
            settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
            settings.GROWTH_PII_HMAC_KEYS_JSON,
            settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
            settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
        ) = previous
        get_growth_pii_keyring.cache_clear()


async def test_private_contact_new_write_contains_no_plaintext(session: AsyncSession) -> None:
    channel_id, plaintext = await _stored_channel(session)
    channel = await session.get(ContactChannel, channel_id)
    assert channel is not None
    assert plaintext not in channel.value_ciphertext
    assert 'sales.owner@example.com' not in channel.value_ciphertext
    assert channel.encryption_key_version == 2
    assert channel.hash_key_version == 2
    assert channel.value_hmac == _keyring().hmac_for('email', plaintext, version=2)

    profile = channel.private_profile_id
    result = await contact_privacy_service.get_masked_contact(
        session,
        keyring=_keyring(),
        private_profile_id=profile,
        owner_scope='personal',
        user_id=981001,
        enterprise_id=None,
    )
    assert result['contact_name'] == '王**'
    assert result['channels'][0]['masked_value'] == 'S***@Example.com'
    assert plaintext not in str(result)
    assert 'ciphertext' not in str(result)


async def test_private_contact_upsert_is_concurrent_and_never_reassigns_channel() -> None:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner_user_id = 981100 + int(uuid4().int % 8000)
    async with sessions.begin() as setup:
        await _apply_sql(setup)
        first_contact_id = await _lead_contact_id(setup, suffix='RACEA')
        second_contact_id = await _lead_contact_id(setup, suffix='RACEB')

    async def _write(contact_id: int) -> dict:
        async with sessions.begin() as write_db:
            return await contact_privacy_service.store_private_contact(
                write_db,
                keyring=_keyring(),
                lead_contact_id=contact_id,
                owner_scope='personal',
                user_id=owner_user_id,
                enterprise_id=None,
                contact_name='并发联系人',
                title='采购负责人',
                lawful_basis='public_business_contact',
                source_ref='hasn://asset/s1-pii-race',
                retention_until=datetime.now(UTC) + timedelta(days=90),
                channels=[
                    ContactChannelWrite(
                        channel='email',
                        value='race.owner@example.com',
                        lawful_basis='public_business_contact',
                        source_ref='hasn://asset/s1-pii-race',
                    )
                ],
            )

    try:
        first, second = await asyncio.gather(
            _write(first_contact_id),
            _write(first_contact_id),
        )
        assert first['private_profile_id'] == second['private_profile_id']
        assert first['channels'][0]['id'] == second['channels'][0]['id']

        with pytest.raises(errors.ConflictError) as exc_info:
            await _write(second_contact_id)
        assert exc_info.value.data == {
            'error_code': 'GROWTH_PII_CHANNEL_CONTACT_CONFLICT',
        }

        async with sessions() as verify:
            channel = (
                await verify.execute(
                    select(ContactChannel).where(
                        ContactChannel.user_id == owner_user_id,
                        ContactChannel.channel == 'email',
                    )
                )
            ).scalar_one()
            assert channel.lead_contact_id == first_contact_id
    finally:
        async with engine.begin() as cleanup:
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact_channel WHERE user_id = :user_id'),
                {'user_id': owner_user_id},
            )
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact_private_profile WHERE user_id = :user_id'),
                {'user_id': owner_user_id},
            )
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact WHERE id IN (:first_id, :second_id)'),
                {'first_id': first_contact_id, 'second_id': second_contact_id},
            )
        await engine.dispose()


async def test_private_contact_rejects_cross_version_channel_reassignment() -> None:
    """密钥轮换并发窗口中，同一地址不能跨 HMAC 版本改挂其他联系人。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner_user_id = 981200 + int(uuid4().int % 8000)
    async with sessions.begin() as setup:
        await _apply_sql(setup)
        first_contact_id = await _lead_contact_id(setup, suffix='ROTATEA')
        second_contact_id = await _lead_contact_id(setup, suffix='ROTATEB')
    old_keyring = GrowthPiiKeyring(
        encryption_keys={1: b'\x11' * 32},
        hmac_keys={1: b'\x33' * 32},
        active_encryption_version=1,
        active_hmac_version=1,
    )

    async def write(*, keyring: GrowthPiiKeyring, lead_contact_id: int) -> dict:
        async with sessions.begin() as write_db:
            return await contact_privacy_service.store_private_contact(
                write_db,
                keyring=keyring,
                lead_contact_id=lead_contact_id,
                owner_scope='personal',
                user_id=owner_user_id,
                enterprise_id=None,
                contact_name='轮换联系人',
                title=None,
                lawful_basis='public_business_contact',
                source_ref='hasn://asset/s1-pii-rotation',
                retention_until=datetime.now(UTC) + timedelta(days=90),
                channels=[
                    ContactChannelWrite(
                        channel='email',
                        value='rotation.owner@example.com',
                        lawful_basis='public_business_contact',
                        source_ref='hasn://asset/s1-pii-rotation',
                    )
                ],
            )

    try:
        results = await asyncio.gather(
            write(keyring=old_keyring, lead_contact_id=first_contact_id),
            write(keyring=_keyring(), lead_contact_id=second_contact_id),
            return_exceptions=True,
        )
        conflicts = [result for result in results if isinstance(result, errors.ConflictError)]
        successes = [result for result in results if isinstance(result, dict)]
        assert len(conflicts) == 1, [
            (
                type(result).__name__,
                type(getattr(result, 'orig', None)).__name__,
                getattr(getattr(result, 'orig', None), 'sqlstate', None),
                getattr(result, 'data', None),
            )
            for result in results
        ]
        assert len(successes) == 1
        assert conflicts[0].data['error_code'] in {
            'GROWTH_PII_CHANNEL_CONTACT_CONFLICT',
            'GROWTH_PII_KEY_VERSION_STALE',
        }

        async with sessions() as verify:
            channels = (
                (
                    await verify.execute(
                        select(ContactChannel).where(
                            ContactChannel.user_id == owner_user_id,
                            ContactChannel.channel == 'email',
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(channels) == 1
    finally:
        async with engine.begin() as cleanup:
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact_channel WHERE user_id = :user_id'),
                {'user_id': owner_user_id},
            )
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact_private_profile WHERE user_id = :user_id'),
                {'user_id': owner_user_id},
            )
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact WHERE id IN (:first_id, :second_id)'),
                {'first_id': first_contact_id, 'second_id': second_contact_id},
            )
        await engine.dispose()


async def test_private_contact_stale_process_cannot_downgrade_key_versions(
    session: AsyncSession,
) -> None:
    """新版本写入提交后，仅持旧密钥的进程不能把同一联系人降级回旧版本。"""
    owner_user_id = 981300 + int(uuid4().int % 8000)
    contact_id = await _lead_contact_id(session, suffix='DOWNGRADE')
    old_keyring = GrowthPiiKeyring(
        encryption_keys={1: b'\x11' * 32},
        hmac_keys={1: b'\x33' * 32},
        active_encryption_version=1,
        active_hmac_version=1,
    )

    async def write(keyring: GrowthPiiKeyring) -> dict:
        return await contact_privacy_service.store_private_contact(
            session,
            keyring=keyring,
            lead_contact_id=contact_id,
            owner_scope='personal',
            user_id=owner_user_id,
            enterprise_id=None,
            contact_name='版本栅栏联系人',
            title=None,
            lawful_basis='public_business_contact',
            source_ref='hasn://asset/s1-pii-fence',
            retention_until=datetime.now(UTC) + timedelta(days=90),
            channels=[
                ContactChannelWrite(
                    channel='email',
                    value='fenced.owner@example.com',
                    lawful_basis='public_business_contact',
                    source_ref='hasn://asset/s1-pii-fence',
                )
            ],
        )

    created = await write(_keyring())
    with pytest.raises(errors.ConflictError) as exc_info:
        await write(old_keyring)
    assert exc_info.value.data == {
        'error_code': 'GROWTH_PII_KEY_VERSION_STALE',
    }

    profile = await session.get(
        ContactPrivateProfile,
        created['private_profile_id'],
    )
    channel = await session.get(ContactChannel, created['channels'][0]['id'])
    assert profile is not None and profile.encryption_key_version == 2
    assert channel is not None
    assert channel.encryption_key_version == 2
    assert channel.hash_key_version == 2
    with pytest.raises(sa.exc.IntegrityError):
        async with session.begin_nested():
            await session.execute(
                sa
                .update(ContactChannel)
                .where(ContactChannel.id == channel.id)
                .values(
                    encryption_key_version=1,
                    hash_key_version=1,
                )
            )
    with pytest.raises(sa.exc.IntegrityError):
        async with session.begin_nested():
            await session.execute(
                sa
                .update(GrowthPiiKeyState)
                .where(GrowthPiiKeyState.id == 1)
                .values(
                    min_encryption_write_version=1,
                    min_hmac_write_version=1,
                )
            )


async def test_owner_reveal_is_single_resource_and_agent_is_denied(session: AsyncSession) -> None:
    channel_id, plaintext = await _stored_channel(session)
    trace_prefix = str(session.info['trace_prefix'])

    with pytest.raises(errors.RequestError) as invalid_purpose:
        await contact_privacy_service.reveal_channel(
            session,
            keyring=_keyring(),
            channel_id=channel_id,
            actor_type='owner',
            actor_id='h_owner_s1_pii',
            scope=GrowthScope(user_id=981001, owner_hasn_id='h_owner_s1_pii'),
            purpose='核对 sales.owner@example.com',
            trace_id=f'{trace_prefix}-invalid-purpose',
        )
    assert invalid_purpose.value.data == {
        'error_code': 'GROWTH_PII_REVEAL_PURPOSE_INVALID',
    }

    revealed = await contact_privacy_service.reveal_channel(
        session,
        keyring=_keyring(),
        channel_id=channel_id,
        actor_type='owner',
        actor_id='h_owner_s1_pii',
        scope=GrowthScope(user_id=981001, owner_hasn_id='h_owner_s1_pii'),
        purpose='contact_verification',
        trace_id=f'{trace_prefix}-allow',
    )
    assert revealed == {'channel': 'email', 'value': plaintext, 'expires_in_seconds': 30}

    with pytest.raises(errors.ForbiddenError, match='Agent'):
        await contact_privacy_service.reveal_channel(
            session,
            keyring=_keyring(),
            channel_id=channel_id,
            actor_type='agent',
            actor_id='a_sales_s1_pii',
            scope=GrowthScope(user_id=981001, owner_hasn_id='h_owner_s1_pii'),
            purpose='customer_support',
            trace_id=f'{trace_prefix}-deny',
        )

    channel = await session.get(ContactChannel, channel_id)
    assert channel is not None
    channel.value_ciphertext = 'corrupted'
    await session.flush()
    with pytest.raises(errors.ServerError, match='解密失败'):
        await contact_privacy_service.reveal_channel(
            session,
            keyring=_keyring(),
            channel_id=channel_id,
            actor_type='owner',
            actor_id='h_owner_s1_pii',
            scope=GrowthScope(user_id=981001, owner_hasn_id='h_owner_s1_pii'),
            purpose='data_correction',
            trace_id=f'{trace_prefix}-error',
        )

    audits = (
        (
            await session.execute(
                select(ContactPrivateAccessAudit)
                .where(ContactPrivateAccessAudit.trace_id.like(f'{trace_prefix}%'))
                .order_by(ContactPrivateAccessAudit.created_time)
            )
        )
        .scalars()
        .all()
    )
    assert [item.result for item in audits] == ['allowed', 'denied', 'error']
    assert all(plaintext not in str(item.request_metadata) for item in audits)
    assert all('ciphertext' not in str(item.request_metadata) for item in audits)


async def test_owner_reveal_missing_keyring_is_audited_before_failing(
    session: AsyncSession,
) -> None:
    channel_id, plaintext = await _stored_channel(session)
    trace_id = f"{session.info['trace_prefix']}-keyring-unavailable"
    previous = (
        settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
        settings.GROWTH_PII_HMAC_KEYS_JSON,
        settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
        settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
    )
    settings.GROWTH_PII_ENCRYPTION_KEYS_JSON = ''
    settings.GROWTH_PII_HMAC_KEYS_JSON = ''
    settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION = 0
    settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION = 0
    get_growth_pii_keyring.cache_clear()
    try:
        with pytest.raises(errors.ServerError) as exc_info:
            await contact_privacy_service.reveal_channel(
                session,
                channel_id=channel_id,
                actor_type='owner',
                actor_id='h_owner_s1_pii',
                scope=GrowthScope(user_id=981001, owner_hasn_id='h_owner_s1_pii'),
                purpose='contact_verification',
                trace_id=trace_id,
            )
        assert exc_info.value.data == {
            'error_code': 'GROWTH_PII_KEYRING_UNAVAILABLE',
        }
    finally:
        (
            settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
            settings.GROWTH_PII_HMAC_KEYS_JSON,
            settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
            settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
        ) = previous
        get_growth_pii_keyring.cache_clear()

    audit = (
        await session.execute(
            select(ContactPrivateAccessAudit).where(
                ContactPrivateAccessAudit.trace_id == trace_id,
            )
        )
    ).scalar_one()
    assert audit.result == 'error'
    assert audit.denial_code == 'GROWTH_PII_KEYRING_UNAVAILABLE'
    assert plaintext not in str(audit.request_metadata)


async def test_optout_matches_retained_hmac_versions_and_new_write_uses_active_version(
    session: AsyncSession,
) -> None:
    keyring = _keyring()
    address = 'Sales.Owner@Example.com'
    session.add(
        OptoutRecord(
            user_id=981002,
            owner_scope='personal',
            enterprise_id=None,
            channel='email',
            address_hash=None,
            address_hmac=keyring.hmac_for('email', address, version=1),
            hash_key_version=1,
            source='s1-retained-key-test',
        )
    )
    # 只在种子区间绕过新写门禁，模拟栅栏上线前已存在的 v1 历史行。
    await session.execute(
        text(
            'ALTER TABLE hasn_growth.optout_record '
            'DISABLE TRIGGER trg_growth_optout_key_fence'
        )
    )
    try:
        await session.flush()
    finally:
        await session.execute(
            text(
                'ALTER TABLE hasn_growth.optout_record '
                'ENABLE TRIGGER trg_growth_optout_key_fence'
            )
        )

    assert await contact_privacy_service.is_opted_out(
        session,
        keyring=keyring,
        owner_scope='personal',
        user_id=981002,
        enterprise_id=None,
        channel='email',
        address=address,
    )

    legacy_address = 'legacy@example.com'
    session.add(
        OptoutRecord(
            user_id=981002,
            owner_scope='personal',
            enterprise_id=None,
            channel='email',
            address_hash=hashlib.sha256(legacy_address.encode()).hexdigest(),
            address_hmac=None,
            hash_key_version=None,
            source='s1-legacy-read-only-test',
        )
    )
    await session.flush()
    assert await contact_privacy_service.is_opted_out(
        session,
        keyring=keyring,
        owner_scope='personal',
        user_id=981002,
        enterprise_id=None,
        channel='email',
        address='Legacy@Example.com',
    )

    created, was_created = await contact_privacy_service.register_optout(
        session,
        keyring=keyring,
        owner_scope='personal',
        user_id=981003,
        enterprise_id=None,
        channel='all',
        address='another@example.com',
        reason='主人主动登记',
        source='owner',
    )
    assert was_created is True
    assert created.address_hash is None
    assert created.hash_key_version == 2
    assert created.address_hmac == keyring.hmac_for('all', 'another@example.com', version=2)
    assert await contact_privacy_service.is_opted_out(
        session,
        keyring=keyring,
        owner_scope='personal',
        user_id=981003,
        enterprise_id=None,
        channel='wechat',
        address='ANOTHER@example.com',
    )


async def test_legacy_optout_admin_mutations_are_rejected(session: AsyncSession) -> None:
    legacy_create = CreateOptoutRecordParam(
        user_id=981004,
        channel='email',
        address_hash='a' * 64,
        source='legacy-admin',
    )
    with pytest.raises(errors.ForbiddenError, match='Owner 退订业务端点'):
        await optout_record_service.create(db=session, obj=legacy_create)

    with pytest.raises(errors.ForbiddenError, match='不允许删除'):
        await optout_record_service.delete(
            db=session,
            obj=DeleteOptoutRecordParam(pks=[1]),
        )


async def test_optout_concurrent_registration_is_idempotent() -> None:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = 982_000_000 + int(uuid4().int % 10_000_000)

    async def register_once() -> bool:
        async with session_factory.begin() as db:
            _, created = await contact_privacy_service.register_optout(
                db,
                keyring=_keyring(),
                owner_scope='personal',
                user_id=user_id,
                enterprise_id=None,
                channel='email',
                address='concurrent@example.com',
                reason='并发幂等测试',
                source='test',
            )
            return created

    try:
        results = await asyncio.gather(register_once(), register_once())
        assert sorted(results) == [False, True]
        async with session_factory() as db:
            count = (
                await db.execute(
                    sa
                    .select(sa.func.count())
                    .select_from(OptoutRecord)
                    .where(
                        OptoutRecord.user_id == user_id,
                        OptoutRecord.channel == 'email',
                        OptoutRecord.address_hash.is_(None),
                    )
                )
            ).scalar_one()
            assert count == 1
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text('DELETE FROM hasn_growth.optout_record WHERE user_id = :user_id'),
                {'user_id': user_id},
            )
        await engine.dispose()


async def test_owner_reveal_http_uses_real_jwt_and_enterprise_acl() -> None:
    """真实 Owner JWT/Redis/PG：企业门禁与 assignee 资源 ACL 均在后端强制执行。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid4().hex
    trace_prefix = f's1-real-auth-{marker}'
    enterprise_id = 8_810_000_000 + int(uuid4().int % 10_000_000)
    manager_hasn = f'h_pii_mgr_{marker[:18]}'
    member_hasn = f'h_pii_mem_{marker[:18]}'
    outsider_hasn = f'h_pii_out_{marker[:18]}'
    plaintext = f'enterprise-{marker[:8]}@example.com'

    async with sessions.begin() as setup:
        await _apply_sql(setup)
        manager = User(
            username=f'pii_mgr_{marker[:16]}',
            nickname=f'PII 企业经理 {marker[:8]}',
            password=None,
            salt=None,
        )
        member = User(
            username=f'pii_mem_{marker[:16]}',
            nickname=f'PII 企业成员 {marker[:8]}',
            password=None,
            salt=None,
        )
        outsider = User(
            username=f'pii_out_{marker[:16]}',
            nickname=f'PII 外部主人 {marker[:8]}',
            password=None,
            salt=None,
        )
        setup.add_all([manager, member, outsider])
        await setup.flush()
        setup.add_all([
            HasnHumans(
                hasn_id=manager_hasn,
                star_id=f'm{marker[:24]}',
                user_id=manager.id,
                nickname=manager.nickname,
                status='active',
            ),
            HasnHumans(
                hasn_id=member_hasn,
                star_id=f's{marker[:24]}',
                user_id=member.id,
                nickname=member.nickname,
                status='active',
            ),
            HasnHumans(
                hasn_id=outsider_hasn,
                star_id=f'o{marker[:24]}',
                user_id=outsider.id,
                nickname=outsider.nickname,
                status='active',
            ),
            HasnEnterpriseMembership(
                enterprise_id=enterprise_id,
                user_id=manager.id,
                role='owner',
                status='approved',
            ),
            HasnEnterpriseMembership(
                enterprise_id=enterprise_id,
                user_id=member.id,
                role='member',
                status='approved',
            ),
            HasnOwnerWorkbenchPref(
                owner_hasn_id=manager_hasn,
                active_enterprise_id=enterprise_id,
            ),
            HasnOwnerWorkbenchPref(
                owner_hasn_id=member_hasn,
                active_enterprise_id=enterprise_id,
            ),
            HasnOwnerWorkbenchPref(
                owner_hasn_id=outsider_hasn,
                active_enterprise_id=None,
            ),
        ])
        lead_contact_id = await _lead_contact_id(setup, suffix='HTTPAUTH')
        private = await contact_privacy_service.store_private_contact(
            setup,
            keyring=_keyring(),
            lead_contact_id=lead_contact_id,
            owner_scope='enterprise',
            user_id=manager.id,
            enterprise_id=enterprise_id,
            contact_name='企业联系人',
            title='采购经理',
            lawful_basis='public_business_contact',
            source_ref='hasn://asset/s1-real-auth',
            retention_until=datetime.now(UTC) + timedelta(days=90),
            channels=[
                ContactChannelWrite(
                    channel='email',
                    value=plaintext,
                    lawful_basis='public_business_contact',
                    source_ref='hasn://asset/s1-real-auth',
                )
            ],
        )
        channel_id = int(private['channels'][0]['id'])
        setup.add(
            Customer(
                user_id=manager.id,
                lead_contact_id=lead_contact_id,
                source_kind='manual',
                lifecycle_status='active',
                owner_scope='enterprise',
                enterprise_id=enterprise_id,
                assignee=manager_hasn,
            )
        )

    app = FastAPI()
    app.include_router(app_growth_router, prefix='/api/v1/growth/app')
    app.include_router(agent_growth_router, prefix='/api/v1/growth/agent')

    @app.exception_handler(BaseExceptionError)
    async def _error_handler(  # ruff: ignore[unused-async]
        _request: Request,
        exc: BaseExceptionError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.code,
            content={'code': exc.code, 'msg': str(exc.msg), 'data': exc.data},
        )

    app.add_middleware(
        AuthenticationMiddleware,
        backend=JwtAuthMiddleware(),
        on_error=JwtAuthMiddleware.auth_exception_handler,
    )
    app.add_middleware(
        ContextMiddleware,
        plugins=[RequestIdPlugin(validate=False)],
    )
    manager_token = await create_access_token(manager.id, multi_login=True)
    member_token = await create_access_token(member.id, multi_login=True)
    outsider_token = await create_access_token(outsider.id, multi_login=True)
    previous_enterprise_enabled = settings.GROWTH_PROJECT_V4_ENTERPRISE_ENABLED
    previous_runtime_keys = (
        settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
        settings.GROWTH_PII_HMAC_KEYS_JSON,
        settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
        settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
    )

    def _headers(token: str, suffix: str) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {token}',
            settings.TRACE_ID_REQUEST_HEADER_KEY: f'{trace_prefix}-{suffix}',
        }

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url='http://privacy-e2e',
        ) as client:
            settings.GROWTH_PROJECT_V4_ENTERPRISE_ENABLED = False
            settings.GROWTH_PII_ENCRYPTION_KEYS_JSON = ''
            settings.GROWTH_PII_HMAC_KEYS_JSON = ''
            settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION = 0
            settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION = 0
            get_growth_pii_keyring.cache_clear()
            gate_response = await client.post(
                f'/api/v1/growth/app/contacts/channels/{channel_id}/reveal',
                json={'purpose': 'contact_verification'},
                headers=_headers(manager_token.access_token, 'gate-disabled'),
            )
            assert gate_response.status_code == 409
            assert gate_response.json()['data'] == {
                'error_code': 'GROWTH_ENTERPRISE_PROJECT_MODE_DISABLED',
            }

            (
                settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
                settings.GROWTH_PII_HMAC_KEYS_JSON,
                settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
                settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
            ) = previous_runtime_keys
            get_growth_pii_keyring.cache_clear()
            settings.GROWTH_PROJECT_V4_ENTERPRISE_ENABLED = True
            response = await client.post(
                f'/api/v1/growth/app/contacts/channels/{channel_id}/reveal',
                json={'purpose': 'contact_verification'},
                headers=_headers(manager_token.access_token, 'manager'),
            )
            assert response.status_code == 200, response.text
            assert response.headers['cache-control'] == 'no-store, max-age=0'
            assert response.headers['pragma'] == 'no-cache'
            assert response.json()['data'] == {
                'channel': 'email',
                'value': plaintext,
                'expires_in_seconds': 30,
            }

            member_response = await client.post(
                f'/api/v1/growth/app/contacts/channels/{channel_id}/reveal',
                json={'purpose': 'customer_support'},
                headers=_headers(member_token.access_token, 'member'),
            )
            assert member_response.status_code == 404

            async with sessions.begin() as assignment:
                await assignment.execute(
                    sa.update(Customer)
                    .where(
                        Customer.enterprise_id == enterprise_id,
                        Customer.lead_contact_id == lead_contact_id,
                    )
                    .values(assignee=member_hasn)
                )

            assigned_member_response = await client.post(
                f'/api/v1/growth/app/contacts/channels/{channel_id}/reveal',
                json={'purpose': 'customer_support'},
                headers=_headers(member_token.access_token, 'member-assigned'),
            )
            assert assigned_member_response.status_code == 200, assigned_member_response.text
            assert assigned_member_response.json()['data']['value'] == plaintext

            outsider_response = await client.post(
                f'/api/v1/growth/app/contacts/channels/{channel_id}/reveal',
                json={'purpose': 'contact_verification'},
                headers=_headers(outsider_token.access_token, 'outsider'),
            )
            assert outsider_response.status_code == 404

            unsafe_purpose = await client.post(
                f'/api/v1/growth/app/contacts/channels/{channel_id}/reveal',
                json={'purpose': f'核对 {plaintext}'},
                headers=_headers(manager_token.access_token, 'unsafe-purpose'),
            )
            assert unsafe_purpose.status_code == 422

            agent_response = await client.post(
                f'/api/v1/growth/agent/contacts/channels/{channel_id}/reveal',
                json={'purpose': 'customer_support'},
                headers=_headers(manager_token.access_token, 'agent-route'),
            )
            assert agent_response.status_code == 404

        async with sessions() as verify:
            audits = (
                (
                    await verify.execute(
                        select(ContactPrivateAccessAudit)
                        .where(ContactPrivateAccessAudit.trace_id.like(f'{trace_prefix}%'))
                        .order_by(ContactPrivateAccessAudit.created_time)
                    )
                )
                .scalars()
                .all()
            )
            assert [audit.result for audit in audits] == ['denied', 'allowed', 'denied', 'allowed', 'denied']
            assert audits[0].denial_code == 'GROWTH_ENTERPRISE_PROJECT_MODE_DISABLED'
            assert all(plaintext not in str(audit.request_metadata) for audit in audits)
            assert all(audit.purpose in {'contact_verification', 'customer_support'} for audit in audits)
    finally:
        settings.GROWTH_PROJECT_V4_ENTERPRISE_ENABLED = previous_enterprise_enabled
        (
            settings.GROWTH_PII_ENCRYPTION_KEYS_JSON,
            settings.GROWTH_PII_HMAC_KEYS_JSON,
            settings.GROWTH_PII_ACTIVE_ENCRYPTION_KEY_VERSION,
            settings.GROWTH_PII_ACTIVE_HMAC_KEY_VERSION,
        ) = previous_runtime_keys
        get_growth_pii_keyring.cache_clear()
        await revoke_token(manager.id, manager_token.session_uuid)
        await revoke_token(member.id, member_token.session_uuid)
        await revoke_token(outsider.id, outsider_token.session_uuid)
        await redis_client.delete(
            f'{settings.JWT_USER_REDIS_PREFIX}:{manager.id}',
            f'{settings.JWT_USER_REDIS_PREFIX}:{member.id}',
            f'{settings.JWT_USER_REDIS_PREFIX}:{outsider.id}',
        )
        async with engine.begin() as cleanup:
            await cleanup.execute(text('ALTER TABLE hasn_growth.contact_private_access_audit DISABLE TRIGGER USER'))
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact_private_access_audit WHERE trace_id LIKE :trace_pattern'),
                {'trace_pattern': f'{trace_prefix}%'},
            )
            await cleanup.execute(text('ALTER TABLE hasn_growth.contact_private_access_audit ENABLE TRIGGER USER'))
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact_channel WHERE enterprise_id = :enterprise_id'),
                {'enterprise_id': enterprise_id},
            )
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact_private_profile WHERE enterprise_id = :enterprise_id'),
                {'enterprise_id': enterprise_id},
            )
            await cleanup.execute(
                text('DELETE FROM hasn_growth.customer WHERE enterprise_id = :enterprise_id'),
                {'enterprise_id': enterprise_id},
            )
            await cleanup.execute(
                text('DELETE FROM hasn_growth.contact WHERE id = :lead_contact_id'),
                {'lead_contact_id': lead_contact_id},
            )
            await cleanup.execute(
                sa.delete(HasnOwnerWorkbenchPref).where(
                    HasnOwnerWorkbenchPref.owner_hasn_id.in_([manager_hasn, member_hasn, outsider_hasn])
                )
            )
            await cleanup.execute(
                sa.delete(HasnEnterpriseMembership).where(HasnEnterpriseMembership.user_id.in_([manager.id, member.id]))
            )
            await cleanup.execute(
                sa.delete(HasnHumans).where(HasnHumans.user_id.in_([manager.id, member.id, outsider.id]))
            )
            await cleanup.execute(sa.delete(User).where(User.id.in_([manager.id, member.id, outsider.id])))
        await engine.dispose()
