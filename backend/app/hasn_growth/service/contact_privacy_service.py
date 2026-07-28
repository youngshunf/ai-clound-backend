"""获客联系人私有资料业务层。

所有 PII 新写集中在本服务：调用方只传授权主体、合法依据和来源，服务负责加密、
版本化 HMAC、主体隔离和无明文返回。Owner reveal 是单渠道短时响应；Agent 永不
获得明文。reveal 审计使用独立事务持久化，审计失败时不返回明文。
"""

from __future__ import annotations

import hashlib

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.contact_private_access_audit import ContactPrivateAccessAudit
from backend.app.hasn_growth.model.contact_private_profile import ContactPrivateProfile
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_pii_key_state import GrowthPiiKeyState
from backend.app.hasn_growth.model.optout_record import OptoutRecord
from backend.app.hasn_growth.service.audit_service import assert_audit_payload_safe
from backend.app.hasn_growth.service.pii import mask_email, mask_name, mask_phone, mask_wechat
from backend.app.hasn_growth.service.pii_keyring import (
    GrowthPiiCiphertextError,
    GrowthPiiKeyring,
    require_growth_pii_keyring,
)
from backend.app.hasn_growth.service.project_mode_gate import (
    ENTERPRISE_MODE_DISABLED,
    assert_project_scope_enabled,
)
from backend.app.hasn_growth.service.scope_context import GrowthScope
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

OwnerScope = Literal['personal', 'enterprise']
ActorType = Literal['owner', 'agent', 'system', 'admin']
REVEAL_PURPOSE_CODES = frozenset({
    'manual_assist_send',
    'contact_verification',
    'customer_support',
    'data_correction',
})


@dataclass(frozen=True)
class ContactChannelWrite:
    """一条联系方式新写请求；明文只在本次调用内存在。"""

    channel: str
    value: str
    lawful_basis: str
    source_ref: str
    consent_ref: str | None = None
    verified_at: datetime | None = None
    fresh_until: datetime | None = None
    retention_until: datetime | None = None


def _validate_owner(
    *,
    owner_scope: str,
    user_id: int | None,
    enterprise_id: int | None,
) -> OwnerScope:
    normalized = owner_scope.strip().casefold()
    if normalized == 'personal' and user_id is not None and enterprise_id is None:
        return 'personal'
    if normalized == 'enterprise' and enterprise_id is not None:
        return 'enterprise'
    raise errors.RequestError(
        msg='PII 授权主体与 owner_scope 不一致',
        data={'error_code': 'GROWTH_PII_OWNER_SCOPE_INVALID'},
    )


def _owner_conditions(
    model: Any,
    *,
    owner_scope: OwnerScope,
    user_id: int | None,
    enterprise_id: int | None,
) -> tuple[Any, ...]:
    if owner_scope == 'personal':
        return model.owner_scope == 'personal', model.user_id == user_id
    return model.owner_scope == 'enterprise', model.enterprise_id == enterprise_id


def _mask_channel(channel: str, value: str) -> str:
    if channel == 'email':
        return mask_email(value) or ''
    if channel == 'phone':
        return mask_phone(value) or ''
    if channel == 'wechat':
        return mask_wechat(value) or ''
    if len(value) <= 2:
        return value[:1] + '*'
    return f'{value[:2]}***{value[-1:]}'


def _legacy_address_hash(value: str) -> str:
    """只用于读取历史 SHA256 退订行，禁止新写。"""
    normalized = value.strip()
    if '@' in normalized:
        normalized = normalized.casefold()
    else:
        digits = ''.join(character for character in normalized if character.isdigit())
        normalized = digits or normalized.casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _pii_purpose(
    resource: str,
    *,
    owner_scope: str,
    user_id: int | None,
    enterprise_id: int | None,
    lead_contact_id: int,
    channel: str | None = None,
) -> str:
    """把密文绑定到主体、联系人和字段，阻止合法密文跨行调包。"""
    owner = f'personal:{user_id}' if owner_scope == 'personal' else f'enterprise:{enterprise_id}'
    suffix = f':{channel}' if channel else ''
    return f'{resource}:{owner}:contact:{lead_contact_id}{suffix}'


def _validate_channel_write(channel_write: ContactChannelWrite) -> tuple[str, str, str]:
    """校验并规范化单个联系方式的非 PII 元数据。"""
    channel = channel_write.channel.strip().casefold()
    lawful_basis = channel_write.lawful_basis.strip()
    source_ref = channel_write.source_ref.strip()
    if not lawful_basis or not source_ref:
        raise errors.RequestError(msg='每个联系方式都必须提供合法依据和来源')
    if len(channel) > 24 or len(lawful_basis) > 48 or len(source_ref) > 255:
        raise errors.RequestError(msg='联系方式渠道、合法依据或来源超过长度限制')
    return channel, lawful_basis, source_ref


async def ensure_growth_pii_key_write_fence(
    db: AsyncSession,
    *,
    keyring: GrowthPiiKeyring,
) -> None:
    """锁定并单调推进 PII 写入版本栅栏，拒绝已过期进程继续写旧版本。"""

    async def read_versions(*, write_lock: bool | None = None) -> tuple[int, int] | None:
        statement = sa.select(
            GrowthPiiKeyState.min_encryption_write_version,
            GrowthPiiKeyState.min_hmac_write_version,
        ).where(GrowthPiiKeyState.id == 1)
        if write_lock is not None:
            statement = statement.with_for_update(read=not write_lock)
        row = (await db.execute(statement)).one_or_none()
        if row is None:
            return None
        return int(row[0]), int(row[1])

    snapshot = await read_versions()
    if snapshot is None:
        raise errors.ServerError(
            msg='PII 密钥版本栅栏尚未初始化',
            data={'error_code': 'GROWTH_PII_KEY_FENCE_UNAVAILABLE'},
        )
    active = (
        keyring.active_encryption_version,
        keyring.active_hmac_version,
    )
    needs_advance = active[0] > snapshot[0] or active[1] > snapshot[1]
    locked = await read_versions(write_lock=needs_advance)
    if locked is None:
        raise errors.ServerError(
            msg='PII 密钥版本栅栏尚未初始化',
            data={'error_code': 'GROWTH_PII_KEY_FENCE_UNAVAILABLE'},
        )
    if active[0] < locked[0] or active[1] < locked[1]:
        raise errors.ConflictError(
            msg='当前进程的 PII 密钥版本已过期，请完成滚动发布',
            data={'error_code': 'GROWTH_PII_KEY_VERSION_STALE'},
        )
    if active[0] > locked[0] or active[1] > locked[1]:
        await db.execute(
            sa
            .update(GrowthPiiKeyState)
            .where(GrowthPiiKeyState.id == 1)
            .values(
                min_encryption_write_version=max(active[0], locked[0]),
                min_hmac_write_version=max(active[1], locked[1]),
                updated_time=timezone.now(),
            )
        )


class ContactPrivacyService:
    """联系人 PII 主体隔离写入、脱敏读取、Owner reveal 和退订匹配。"""

    @staticmethod
    async def _write_access_audit(
        *,
        owner_scope: OwnerScope,
        user_id: int | None,
        enterprise_id: int | None,
        actor_type: ActorType,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        purpose: str,
        trace_id: str,
        result: Literal['allowed', 'denied', 'error'],
        denial_code: str | None = None,
        request_metadata: dict | None = None,
    ) -> None:
        metadata = request_metadata or {}
        assert_audit_payload_safe(metadata)
        async with async_db_session.begin() as audit_db:
            audit_db.add(
                ContactPrivateAccessAudit(
                    owner_scope=owner_scope,
                    user_id=user_id,
                    enterprise_id=enterprise_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    purpose=purpose,
                    trace_id=trace_id,
                    result=result,
                    denial_code=denial_code,
                    request_metadata=metadata,
                )
            )

    @staticmethod
    async def store_private_contact(  # ruff: ignore[complex-structure]
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        lead_contact_id: int,
        owner_scope: str,
        user_id: int | None,
        enterprise_id: int | None,
        contact_name: str | None,
        title: str | None,
        lawful_basis: str,
        source_ref: str,
        retention_until: datetime,
        channels: list[ContactChannelWrite],
        preserve_existing: bool = False,
        allow_profile_only: bool = False,
    ) -> dict:
        """按主体 UPSERT 私有资料和渠道；公开回流可选择只补缺失数据。"""
        scope = _validate_owner(
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
        )
        if not lawful_basis.strip() or not source_ref.strip():
            raise errors.RequestError(msg='PII 新写必须提供合法依据和来源')
        if len(lawful_basis.strip()) > 48 or len(source_ref.strip()) > 255:
            raise errors.RequestError(msg='PII 合法依据或来源超过长度限制')
        if retention_until <= timezone.now():
            raise errors.RequestError(msg='PII 保留期必须晚于当前时间')
        if not channels and (
            not allow_profile_only
            or not ((contact_name or '').strip() or (title or '').strip())
        ):
            raise errors.RequestError(msg='PII 新写至少需要联系方式或私有资料')
        await ensure_growth_pii_key_write_fence(db, keyring=keyring)

        normalized_channels: list[tuple[ContactChannelWrite, str, str, str, datetime]] = []
        for channel_write in channels:
            channel, channel_basis, channel_source = _validate_channel_write(channel_write)
            channel_retention = channel_write.retention_until or retention_until
            if channel_retention <= timezone.now():
                raise errors.RequestError(msg='联系方式保留期必须晚于当前时间')
            normalized_channels.append((channel_write, channel, channel_basis, channel_source, channel_retention))

        encrypted_name = (
            keyring.encrypt(
                contact_name,
                purpose=_pii_purpose(
                    'contact_name',
                    owner_scope=scope,
                    user_id=user_id,
                    enterprise_id=enterprise_id,
                    lead_contact_id=lead_contact_id,
                ),
            )
            if contact_name
            else None
        )
        encrypted_title = (
            keyring.encrypt(
                title,
                purpose=_pii_purpose(
                    'contact_title',
                    owner_scope=scope,
                    user_id=user_id,
                    enterprise_id=enterprise_id,
                    lead_contact_id=lead_contact_id,
                ),
            )
            if title
            else None
        )
        profile_insert = pg_insert(ContactPrivateProfile).values(
            lead_contact_id=lead_contact_id,
            owner_scope=scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
            contact_name_ciphertext=encrypted_name,
            title_ciphertext=encrypted_title,
            encryption_key_version=keyring.active_encryption_version,
            lawful_basis=lawful_basis.strip(),
            source_ref=source_ref.strip(),
            retention_until=retention_until,
            status='active',
        )
        profile_updates = {
            'contact_name_ciphertext': encrypted_name,
            'title_ciphertext': encrypted_title,
            'encryption_key_version': keyring.active_encryption_version,
            'lawful_basis': lawful_basis.strip(),
            'source_ref': source_ref.strip(),
            'retention_until': retention_until,
            'status': 'active',
            'updated_time': timezone.now(),
        }
        profile_index_elements = (
            [
                ContactPrivateProfile.lead_contact_id,
                ContactPrivateProfile.user_id,
            ]
            if scope == 'personal'
            else [
                ContactPrivateProfile.lead_contact_id,
                ContactPrivateProfile.enterprise_id,
            ]
        )
        profile_index_where = ContactPrivateProfile.owner_scope == scope
        if preserve_existing:
            profile_insert = profile_insert.on_conflict_do_nothing(
                index_elements=profile_index_elements,
                index_where=profile_index_where,
            )
        else:
            profile_insert = profile_insert.on_conflict_do_update(
                index_elements=profile_index_elements,
                index_where=profile_index_where,
                set_=profile_updates,
            )
        inserted_profile_id = (
            await db.execute(profile_insert.returning(ContactPrivateProfile.id))
        ).scalar_one_or_none()
        if inserted_profile_id is None:
            profile_id = int(
                (
                    await db.execute(
                        sa
                        .select(ContactPrivateProfile.id)
                        .where(
                            ContactPrivateProfile.lead_contact_id == lead_contact_id,
                            *_owner_conditions(
                                ContactPrivateProfile,
                                owner_scope=scope,
                                user_id=user_id,
                                enterprise_id=enterprise_id,
                            ),
                        )
                        .with_for_update()
                    )
                ).scalar_one()
            )
        else:
            profile_id = int(inserted_profile_id)

        channel_views: list[dict] = []
        for channel_write, channel, channel_basis, channel_source, channel_retention in normalized_channels:
            hmac_candidates = keyring.hmac_candidates(channel, channel_write.value)
            active_hmac = next(
                candidate.value
                for candidate in hmac_candidates
                if candidate.version == keyring.active_hmac_version
            )
            oldest_candidate = min(hmac_candidates, key=lambda candidate: candidate.version)
            owner_lock_id = user_id if scope == 'personal' else enterprise_id
            lock_key = (
                f'growth-contact-channel:{scope}:{owner_lock_id}:{channel}:'
                f'v{oldest_candidate.version}:{oldest_candidate.value}'
            )
            # 轮换窗口的新旧进程都保留最老 HMAC 版本，用无明文 advisory lock 串行化跨版本写入。
            await db.execute(
                sa.select(
                    sa.func.pg_advisory_xact_lock(
                        sa.func.hashtextextended(lock_key, 0)
                    )
                )
            )
            existing_candidates = (
                (
                    await db.execute(
                        sa
                        .select(ContactChannel)
                        .where(
                            ContactChannel.channel == channel,
                            sa.tuple_(
                                ContactChannel.hash_key_version,
                                ContactChannel.value_hmac,
                            ).in_([
                                (candidate.version, candidate.value)
                                for candidate in hmac_candidates
                            ]),
                            *_owner_conditions(
                                ContactChannel,
                                owner_scope=scope,
                                user_id=user_id,
                                enterprise_id=enterprise_id,
                            ),
                        )
                        .order_by(
                            ContactChannel.hash_key_version.desc(),
                            ContactChannel.id,
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if any(
                existing.lead_contact_id != lead_contact_id
                for existing in existing_candidates
            ):
                raise errors.ConflictError(
                    msg='同一主体下该联系方式已归属于其他联系人，必须先执行联系人合并',
                    data={'error_code': 'GROWTH_PII_CHANNEL_CONTACT_CONFLICT'},
                )
            existing = next(
                (
                    row
                    for row in existing_candidates
                    if row.hash_key_version == keyring.active_hmac_version
                    and row.value_hmac == active_hmac
                ),
                existing_candidates[0] if existing_candidates else None,
            )
            ciphertext = keyring.encrypt(
                channel_write.value,
                purpose=_pii_purpose(
                    'contact_channel',
                    owner_scope=scope,
                    user_id=user_id,
                    enterprise_id=enterprise_id,
                    lead_contact_id=lead_contact_id,
                    channel=channel,
                ),
            )
            if existing is not None:
                if not preserve_existing:
                    await db.execute(
                        sa
                        .update(ContactChannel)
                        .where(ContactChannel.id == existing.id)
                        .values(
                            private_profile_id=profile_id,
                            value_ciphertext=ciphertext,
                            encryption_key_version=keyring.active_encryption_version,
                            value_hmac=active_hmac,
                            hash_key_version=keyring.active_hmac_version,
                            lawful_basis=channel_basis,
                            source_ref=channel_source,
                            consent_ref=channel_write.consent_ref,
                            verified_at=channel_write.verified_at,
                            fresh_until=channel_write.fresh_until,
                            retention_until=channel_retention,
                            status='active',
                            updated_time=timezone.now(),
                        )
                    )
                channel_id = existing.id
                channel_views.append({
                    'id': channel_id,
                    'channel': channel,
                    'masked_value': _mask_channel(channel, channel_write.value),
                    'status': 'active',
                })
                continue

            channel_insert = pg_insert(ContactChannel).values(
                private_profile_id=profile_id,
                lead_contact_id=lead_contact_id,
                owner_scope=scope,
                user_id=user_id,
                enterprise_id=enterprise_id,
                channel=channel,
                value_ciphertext=ciphertext,
                encryption_key_version=keyring.active_encryption_version,
                value_hmac=active_hmac,
                hash_key_version=keyring.active_hmac_version,
                lawful_basis=channel_basis,
                source_ref=channel_source,
                consent_ref=channel_write.consent_ref,
                verified_at=channel_write.verified_at,
                fresh_until=channel_write.fresh_until,
                retention_until=channel_retention,
                status='active',
            )
            if scope == 'personal':
                channel_insert = channel_insert.on_conflict_do_nothing(
                    index_elements=[
                        ContactChannel.user_id,
                        ContactChannel.channel,
                        ContactChannel.value_hmac,
                        ContactChannel.hash_key_version,
                    ],
                    index_where=ContactChannel.owner_scope == 'personal',
                )
            else:
                channel_insert = channel_insert.on_conflict_do_nothing(
                    index_elements=[
                        ContactChannel.enterprise_id,
                        ContactChannel.channel,
                        ContactChannel.value_hmac,
                        ContactChannel.hash_key_version,
                    ],
                    index_where=ContactChannel.owner_scope == 'enterprise',
                )
            inserted_id = (await db.execute(channel_insert.returning(ContactChannel.id))).scalar_one_or_none()
            if inserted_id is None:
                existing = (
                    (
                        await db.execute(
                            sa
                            .select(ContactChannel)
                            .where(
                                ContactChannel.channel == channel,
                                ContactChannel.value_hmac == active_hmac,
                                ContactChannel.hash_key_version == keyring.active_hmac_version,
                                *_owner_conditions(
                                    ContactChannel,
                                    owner_scope=scope,
                                    user_id=user_id,
                                    enterprise_id=enterprise_id,
                                ),
                            )
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .one()
                )
                if existing.lead_contact_id != lead_contact_id:
                    raise errors.ConflictError(
                        msg='同一主体下该联系方式已归属于其他联系人，必须先执行联系人合并',
                        data={'error_code': 'GROWTH_PII_CHANNEL_CONTACT_CONFLICT'},
                    )
                if not preserve_existing:
                    await db.execute(
                        sa
                        .update(ContactChannel)
                        .where(ContactChannel.id == existing.id)
                        .values(
                            private_profile_id=profile_id,
                            value_ciphertext=ciphertext,
                            encryption_key_version=keyring.active_encryption_version,
                            lawful_basis=channel_basis,
                            source_ref=channel_source,
                            consent_ref=channel_write.consent_ref,
                            verified_at=channel_write.verified_at,
                            fresh_until=channel_write.fresh_until,
                            retention_until=channel_retention,
                            status='active',
                            updated_time=timezone.now(),
                        )
                    )
                channel_id = existing.id
            else:
                channel_id = int(inserted_id)
            channel_views.append({
                'id': channel_id,
                'channel': channel,
                'masked_value': _mask_channel(channel, channel_write.value),
                'status': 'active',
            })

        return {
            'private_profile_id': profile_id,
            'lead_contact_id': lead_contact_id,
            'contact_name': mask_name(contact_name),
            'title': mask_name(title),
            'channels': channel_views,
        }

    @staticmethod
    async def get_masked_contact(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        private_profile_id: int,
        owner_scope: str,
        user_id: int | None,
        enterprise_id: int | None,
    ) -> dict:
        """读取本主体单个私有资料，只返回脱敏值。"""
        scope = _validate_owner(
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
        )
        profile = (
            (
                await db.execute(
                    sa.select(ContactPrivateProfile).where(
                        ContactPrivateProfile.id == private_profile_id,
                        ContactPrivateProfile.status == 'active',
                        ContactPrivateProfile.retention_until > timezone.now(),
                        *_owner_conditions(
                            ContactPrivateProfile,
                            owner_scope=scope,
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                        ),
                    )
                )
            )
            .scalars()
            .first()
        )
        if profile is None:
            raise errors.NotFoundError(msg='联系人私有资料不存在')

        name = (
            keyring.decrypt(
                profile.contact_name_ciphertext,
                version=profile.encryption_key_version,
                purpose=_pii_purpose(
                    'contact_name',
                    owner_scope=profile.owner_scope,
                    user_id=profile.user_id,
                    enterprise_id=profile.enterprise_id,
                    lead_contact_id=profile.lead_contact_id,
                ),
            )
            if profile.contact_name_ciphertext
            else None
        )
        title = (
            keyring.decrypt(
                profile.title_ciphertext,
                version=profile.encryption_key_version,
                purpose=_pii_purpose(
                    'contact_title',
                    owner_scope=profile.owner_scope,
                    user_id=profile.user_id,
                    enterprise_id=profile.enterprise_id,
                    lead_contact_id=profile.lead_contact_id,
                ),
            )
            if profile.title_ciphertext
            else None
        )
        channels = (
            (
                await db.execute(
                    sa
                    .select(ContactChannel)
                    .where(
                        ContactChannel.private_profile_id == profile.id,
                        ContactChannel.status == 'active',
                        ContactChannel.retention_until > timezone.now(),
                        *_owner_conditions(
                            ContactChannel,
                            owner_scope=scope,
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                        ),
                    )
                    .order_by(ContactChannel.id)
                )
            )
            .scalars()
            .all()
        )
        channel_views = []
        for channel in channels:
            value = keyring.decrypt(
                channel.value_ciphertext,
                version=channel.encryption_key_version,
                purpose=_pii_purpose(
                    'contact_channel',
                    owner_scope=channel.owner_scope,
                    user_id=channel.user_id,
                    enterprise_id=channel.enterprise_id,
                    lead_contact_id=channel.lead_contact_id,
                    channel=channel.channel,
                ),
            )
            channel_views.append({
                'id': channel.id,
                'channel': channel.channel,
                'masked_value': _mask_channel(channel.channel, value),
                'status': channel.status,
            })
        return {
            'private_profile_id': profile.id,
            'lead_contact_id': profile.lead_contact_id,
            'contact_name': mask_name(name),
            'title': mask_name(title),
            'channels': channel_views,
        }

    @staticmethod
    async def find_masked_contact_for_lead(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring | None = None,
        lead_contact_id: int,
        owner_scope: str,
        user_id: int | None,
        enterprise_id: int | None,
    ) -> dict | None:
        """按联系人和授权主体查找私有资料；不存在时返回空，不跨主体回退。"""
        scope = _validate_owner(
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
        )
        private_profile_id = (
            await db.execute(
                sa
                .select(ContactPrivateProfile.id)
                .where(
                    ContactPrivateProfile.lead_contact_id == lead_contact_id,
                    ContactPrivateProfile.status == 'active',
                    ContactPrivateProfile.retention_until > timezone.now(),
                    *_owner_conditions(
                        ContactPrivateProfile,
                        owner_scope=scope,
                        user_id=user_id,
                        enterprise_id=enterprise_id,
                    ),
                )
                .order_by(ContactPrivateProfile.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if private_profile_id is None:
            return None
        return await ContactPrivacyService.get_masked_contact(
            db,
            keyring=keyring or require_growth_pii_keyring(),
            private_profile_id=int(private_profile_id),
            owner_scope=scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
        )

    async def reveal_channel(  # ruff: ignore[complex-structure]
        self,
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring | None = None,
        channel_id: int,
        actor_type: ActorType,
        actor_id: str,
        scope: GrowthScope,
        purpose: str,
        trace_id: str,
    ) -> dict:
        """Owner 单渠道 reveal；审计独立提交，失败时不返回明文。"""
        purpose = purpose.strip()
        trace_id = trace_id.strip()
        actor_id = actor_id.strip()
        if purpose not in REVEAL_PURPOSE_CODES:
            raise errors.RequestError(
                msg='reveal 必须使用受控原因码',
                data={'error_code': 'GROWTH_PII_REVEAL_PURPOSE_INVALID'},
            )
        if not trace_id or len(trace_id) > 128:
            raise errors.RequestError(msg='reveal trace_id 无效')
        if not actor_id or len(actor_id) > 64:
            raise errors.RequestError(msg='reveal 操作者标识无效')
        owner_scope: OwnerScope = 'enterprise' if scope.is_enterprise else 'personal'
        user_id = scope.user_id
        enterprise_id = scope.enterprise_id
        try:
            assert_project_scope_enabled(owner_scope)
        except errors.ConflictError:
            await self._write_access_audit(
                owner_scope=owner_scope,
                user_id=user_id,
                enterprise_id=enterprise_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action='reveal',
                resource_type='contact_channel',
                resource_id=str(channel_id),
                purpose=purpose,
                trace_id=trace_id,
                result='denied',
                denial_code=ENTERPRISE_MODE_DISABLED,
                request_metadata={'channel_id': channel_id},
            )
            raise
        if actor_type != 'owner':
            await self._write_access_audit(
                owner_scope=owner_scope,
                user_id=user_id,
                enterprise_id=enterprise_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action='reveal',
                resource_type='contact_channel',
                resource_id=str(channel_id),
                purpose=purpose,
                trace_id=trace_id,
                result='denied',
                denial_code='GROWTH_PII_AGENT_REVEAL_DENIED',
                request_metadata={'channel_id': channel_id},
            )
            raise errors.ForbiddenError(
                msg='Agent 不允许读取联系人明文',
                data={'error_code': 'GROWTH_PII_AGENT_REVEAL_DENIED'},
            )

        channel_conditions: list[Any] = [
            ContactChannel.id == channel_id,
            ContactChannel.status == 'active',
            ContactChannel.retention_until > timezone.now(),
            *_owner_conditions(
                ContactChannel,
                owner_scope=owner_scope,
                user_id=user_id,
                enterprise_id=enterprise_id,
            ),
        ]
        if scope.is_enterprise and not scope.is_manager:
            if scope.viewer_role != 'member' or not scope.owner_hasn_id:
                channel_conditions.append(sa.false())
            else:
                channel_conditions.append(
                    sa.exists(
                        sa.select(Customer.id).where(
                            Customer.lead_contact_id == ContactChannel.lead_contact_id,
                            Customer.owner_scope == 'enterprise',
                            Customer.enterprise_id == enterprise_id,
                            Customer.assignee == scope.owner_hasn_id,
                        )
                    )
                )

        channel = (
            (
                await db.execute(
                    sa.select(ContactChannel).where(*channel_conditions)
                )
            )
            .scalars()
            .first()
        )
        if channel is None:
            await self._write_access_audit(
                owner_scope=owner_scope,
                user_id=user_id,
                enterprise_id=enterprise_id,
                actor_type='owner',
                actor_id=actor_id,
                action='reveal',
                resource_type='contact_channel',
                resource_id=str(channel_id),
                purpose=purpose,
                trace_id=trace_id,
                result='denied',
                denial_code='GROWTH_PII_CHANNEL_NOT_FOUND',
                request_metadata={'channel_id': channel_id},
            )
            raise errors.NotFoundError(msg='联系人渠道不存在')

        try:
            active_keyring = keyring or require_growth_pii_keyring()
        except errors.ServerError as exc:
            if exc.data != {'error_code': 'GROWTH_PII_KEYRING_UNAVAILABLE'}:
                raise
            await self._write_access_audit(
                owner_scope=owner_scope,
                user_id=user_id,
                enterprise_id=enterprise_id,
                actor_type='owner',
                actor_id=actor_id,
                action='reveal',
                resource_type='contact_channel',
                resource_id=str(channel_id),
                purpose=purpose,
                trace_id=trace_id,
                result='error',
                denial_code='GROWTH_PII_KEYRING_UNAVAILABLE',
                request_metadata={'channel_id': channel_id, 'channel': channel.channel},
            )
            raise
        try:
            value = active_keyring.decrypt(
                channel.value_ciphertext,
                version=channel.encryption_key_version,
                purpose=_pii_purpose(
                    'contact_channel',
                    owner_scope=channel.owner_scope,
                    user_id=channel.user_id,
                    enterprise_id=channel.enterprise_id,
                    lead_contact_id=channel.lead_contact_id,
                    channel=channel.channel,
                ),
            )
        except GrowthPiiCiphertextError as exc:
            await self._write_access_audit(
                owner_scope=owner_scope,
                user_id=user_id,
                enterprise_id=enterprise_id,
                actor_type='owner',
                actor_id=actor_id,
                action='reveal',
                resource_type='contact_channel',
                resource_id=str(channel_id),
                purpose=purpose,
                trace_id=trace_id,
                result='error',
                denial_code='GROWTH_PII_DECRYPT_FAILED',
                request_metadata={'channel_id': channel_id, 'channel': channel.channel},
            )
            raise errors.ServerError(
                msg='联系人渠道解密失败',
                data={'error_code': 'GROWTH_PII_DECRYPT_FAILED'},
            ) from exc

        await self._write_access_audit(
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
            actor_type='owner',
            actor_id=actor_id,
            action='reveal',
            resource_type='contact_channel',
            resource_id=str(channel_id),
            purpose=purpose,
            trace_id=trace_id,
            result='allowed',
            request_metadata={'channel_id': channel_id, 'channel': channel.channel},
        )
        return {
            'channel': channel.channel,
            'value': value,
            'expires_in_seconds': 30,
        }

    @staticmethod
    async def is_opted_out(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        owner_scope: str,
        user_id: int | None,
        enterprise_id: int | None,
        channel: str,
        address: str,
    ) -> bool:
        """用全部保留 HMAC 版本匹配，并只读兼容历史 SHA256 退订。"""
        scope = _validate_owner(
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
        )
        normalized_channel = channel.strip().casefold()
        match_channels = (normalized_channel, 'all') if normalized_channel != 'all' else ('all',)
        conditions: list[Any] = []
        for match_channel in match_channels:
            try:
                candidates = keyring.hmac_candidates(match_channel, address)
            except ValueError:
                # 同一客户可能有多种渠道；具体渠道格式不匹配时仍继续检查 all 退订。
                continue
            conditions.extend(
                sa.and_(
                    OptoutRecord.channel == match_channel,
                    OptoutRecord.hash_key_version == candidate.version,
                    OptoutRecord.address_hmac == candidate.value,
                )
                for candidate in candidates
            )
        conditions.append(
            sa.and_(
                OptoutRecord.channel.in_(match_channels),
                OptoutRecord.address_hash == _legacy_address_hash(address),
            )
        )
        return (
            await db.execute(
                sa
                .select(OptoutRecord.id)
                .where(
                    *_owner_conditions(
                        OptoutRecord,
                        owner_scope=scope,
                        user_id=user_id,
                        enterprise_id=enterprise_id,
                    ),
                    sa.or_(*conditions),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None

    @staticmethod
    async def is_private_contact_opted_out(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        lead_contact_id: int,
        owner_scope: str,
        user_id: int | None,
        enterprise_id: int | None,
        channel: str,
    ) -> bool:
        """在服务端解密本主体有效渠道并匹配退订，不把明文返回给调用方。"""
        scope = _validate_owner(
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
        )
        channels = (
            (
                await db.execute(
                    sa.select(ContactChannel).where(
                        ContactChannel.lead_contact_id == lead_contact_id,
                        ContactChannel.status == 'active',
                        ContactChannel.retention_until > timezone.now(),
                        *_owner_conditions(
                            ContactChannel,
                            owner_scope=scope,
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        for private_channel in channels:
            address = keyring.decrypt(
                private_channel.value_ciphertext,
                version=private_channel.encryption_key_version,
                purpose=_pii_purpose(
                    'contact_channel',
                    owner_scope=private_channel.owner_scope,
                    user_id=private_channel.user_id,
                    enterprise_id=private_channel.enterprise_id,
                    lead_contact_id=private_channel.lead_contact_id,
                    channel=private_channel.channel,
                ),
            )
            if await ContactPrivacyService.is_opted_out(
                db,
                keyring=keyring,
                owner_scope=scope,
                user_id=user_id,
                enterprise_id=enterprise_id,
                channel=channel,
                address=address,
            ):
                return True
        return False

    @staticmethod
    async def register_optout(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring,
        owner_scope: str,
        user_id: int | None,
        enterprise_id: int | None,
        channel: str,
        address: str,
        reason: str | None,
        source: str,
        customer_id: int | None = None,
    ) -> tuple[OptoutRecord, bool]:
        """登记 active HMAC 版本退订；新写明确不产生旧 SHA256。"""
        scope = _validate_owner(
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=enterprise_id,
        )
        normalized_channel = channel.strip().casefold()
        normalized_source = source.strip()
        if not normalized_source or len(normalized_source) > 64:
            raise errors.RequestError(msg='退订来源无效')
        await ensure_growth_pii_key_write_fence(db, keyring=keyring)
        address_hmac = keyring.hmac_for(normalized_channel, address)
        insert_stmt = (
            pg_insert(OptoutRecord)
            .values(
                user_id=user_id or 0,
                owner_scope=scope,
                enterprise_id=enterprise_id,
                channel=normalized_channel,
                address_hash=None,
                address_hmac=address_hmac,
                hash_key_version=keyring.active_hmac_version,
                customer_id=customer_id,
                reason=reason,
                source=normalized_source,
            )
            .on_conflict_do_nothing()
            .returning(OptoutRecord.id)
        )
        inserted_id = (await db.execute(insert_stmt)).scalar_one_or_none()
        if inserted_id is not None:
            row = await db.get(OptoutRecord, inserted_id)
            if row is None:
                raise errors.ServerError(msg='退订登记后无法读取记录')
            return row, True

        existing = (
            (
                await db.execute(
                    sa.select(OptoutRecord).where(
                        OptoutRecord.channel == normalized_channel,
                        OptoutRecord.address_hmac == address_hmac,
                        OptoutRecord.hash_key_version == keyring.active_hmac_version,
                        *_owner_conditions(
                            OptoutRecord,
                            owner_scope=scope,
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                        ),
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            raise errors.ConflictError(msg='退订登记发生并发冲突，请重试')
        return existing, False


contact_privacy_service = ContactPrivacyService()
