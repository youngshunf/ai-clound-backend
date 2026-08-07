"""获客触达状态机 + 营销合规服务（设计 07 §8.2 G4 + §10.3）。

触达审批状态机（业务态，**不走 ask_gate**，对齐任务系统 D4）：
    agent outreach.send → 服务端合规检查 → 首触达必 pending_approval（不可豁免）/
    白名单（主人开自动放行 且 客户曾回复）→ approved(auto_approved=true) / 其余 → pending_approval。
    owner approve / edit-then-approve / reject(reason) → 发送 worker（M6）接手。

合规硬闸（§10.3）：optout 命中（无豁免）/ 广告法极限词 / 同客户同渠道频控（≤2/周）→ 拦截落库
（blocked_optout / blocked_compliance），分身下轮经 timeline 学习。quiet hours / 微信个人号开关
属发送 worker（M6）排队与渠道策略，本服务记录于 compliance_check 供下游裁决。

PII 边界（§10.2）：本服务只处理话术正文（不含明文联系方式）；optout 命中判定在服务端完成，
明文不出参、不进 LLM。manual_assist 素材包不含联系方式，目标渠道由 Owner 单独 reveal。
"""

from __future__ import annotations

import hashlib

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import identity
from backend.app.hasn_core.app_platform import app_catalog_service
from backend.app.hasn_growth.model.contact_channel import ContactChannel
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_playbook import (
    GrowthProjectPlaybook,
)
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.optout_record import OptoutRecord
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.model.outreach_message_event import OutreachMessageEvent
from backend.app.hasn_growth.service.attribution_service import growth_attribution_service
from backend.app.hasn_growth.service.contact_privacy_service import contact_privacy_service
from backend.app.hasn_growth.service.funnel_service import GrowthFunnelService
from backend.app.hasn_growth.service.growth_notification import growth_notification_service
from backend.app.hasn_growth.service.pii import mask_contact_fields, redact_pii_value
from backend.app.hasn_growth.service.pii_boundary import (
    GrowthPiiBoundaryError,
    assert_growth_pii_payload_safe,
)
from backend.app.hasn_growth.service.pii_keyring import (
    GrowthPiiKeyring,
    require_growth_pii_keyring,
)
from backend.app.hasn_growth.service.scope_context import GrowthScope, apply_scope
from backend.app.hasn_task.service.agent_task_service import agent_task_service
from backend.common.exception import errors
from backend.core.conf import settings
from backend.utils.timezone import timezone

# 广告法极限词（节选，playbook 可收紧不可放宽）——命中 → blocked_compliance 回分身改写。
_BANNED_SUPERLATIVES = (
    '国家级',
    '最高级',
    '最佳',
    '第一品牌',
    '全网第一',
    '全国第一',
    '世界第一',
    '100%',
    '百分之百',
    '绝对',
    '顶级',
    '独一无二',
    '唯一',
    '最大',
    '最好',
    '最低价',
    '史上最',
    '全球领先',
    '永久',
    '万能',
    '彻底根治',
    '稳赚',
)

# 同客户同渠道频控：默认 ≤2 条/周（§10.3）。
_FREQ_WINDOW_DAYS = 7
_FREQ_MAX_PER_WINDOW = 2

# 频控计数纳入的「占用配额」状态（已发/在途/待审/已批都算占用，拒绝/拦截/失败不算）。
_QUOTA_STATUSES = ('pending_approval', 'approved', 'sending', 'sent', 'replied')

# quiet hours 默认窗口 [09:00, 21:00)（§10.3，客户时区缺省随主人时区，此处用服务端时区近似）。
_QUIET_START_HOUR = 9
_QUIET_END_HOUR = 21

# J3 即时跟进防抖：同客户 10 分钟窗口仅触发一次 run_now（§M6，云端侧窗口合并）。
_FOLLOWUP_DEBOUNCE_MINUTES = 10
_OUTREACH_CHANNELS = frozenset({
    'email',
    'feishu',
    'hasn_dm',
    'manual_assist',
    'qq',
    'wechat',
})
_APPROVAL_STATUSES = frozenset({
    'approved',
    'cancelled',
    'draft',
    'pending_approval',
    'rejected',
})
_DELIVERY_STATUSES = frozenset({
    'blocked_compliance',
    'blocked_optout',
    'delivered',
    'failed',
    'not_queued',
    'queued',
    'sending',
    'sent',
})


def _is_quiet_hour(hour: int, *, start: int, end: int) -> bool:
    """判断支持跨午夜的项目静默时段。"""
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


def _stable_key(*parts: object) -> str:
    """生成 outreach_message 全局唯一且不携带业务明文的稳定键。"""
    material = '|'.join(str(part) for part in parts)
    return hashlib.sha256(material.encode()).hexdigest()[:64]


def _legacy_approval_status(message: OutreachMessage) -> str:
    if message.approval_status in _APPROVAL_STATUSES:
        return str(message.approval_status)
    if message.status == 'approved':
        return 'approved'
    if message.status == 'rejected':
        return 'rejected'
    if message.status == 'pending_approval':
        return 'pending_approval'
    return 'draft'


def _legacy_delivery_status(message: OutreachMessage) -> str:
    if message.delivery_status in _DELIVERY_STATUSES:
        return str(message.delivery_status)
    if message.status in {'sending', 'sent', 'failed', 'blocked_optout', 'blocked_compliance'}:
        return message.status
    return 'not_queued'


def _sync_legacy_status(message: OutreachMessage) -> None:
    """只为旧客户端投影单一状态；业务判定一律读取正交字段。"""
    approval_status = _legacy_approval_status(message)
    delivery_status = _legacy_delivery_status(message)
    if message.direction == 'inbound' or message.replied_at is not None:
        message.status = 'replied'
    elif delivery_status in {'blocked_optout', 'blocked_compliance', 'failed', 'sending'}:
        message.status = delivery_status
    elif delivery_status in {'sent', 'delivered'}:
        message.status = 'sent'
    elif approval_status in {'rejected', 'cancelled'}:
        message.status = 'rejected'
    else:
        message.status = approval_status


def _assert_content_version(message: OutreachMessage, expected_content_version: int | None) -> int:
    current = int(message.content_version or 1)
    if expected_content_version is not None and expected_content_version != current:
        raise errors.ConflictError(
            msg='内容已变化，请重新审核',
            data={
                'error_code': 'GROWTH_OUTREACH_CONTENT_CHANGED',
                'expected_content_version': expected_content_version,
                'current_content_version': current,
            },
        )
    return current


def _public_content_assets(value: dict | None) -> dict:
    """审批冻结快照只供 worker 使用，不回传内部目标引用。"""
    return {key: item for key, item in (value or {}).items() if key != '_approval_snapshot'}


def _legacy_address_hash(value: str | None) -> str | None:
    """仅供 PII 切流开关关闭时维持历史 SHA256 退订链路。"""
    if not value:
        return None
    normalized = value.strip()
    if '@' in normalized:
        normalized = normalized.casefold()
    else:
        digits = ''.join(character for character in normalized if character.isdigit())
        normalized = digits or normalized.casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _outreach_to_dict(m: OutreachMessage) -> dict[str, Any]:
    return redact_pii_value({
        'id': m.id,
        'growth_project_id': (str(m.growth_project_id) if m.growth_project_id is not None else None),
        'customer_id': m.customer_id,
        'opportunity_id': m.opportunity_id,
        'agent_id': m.agent_id,
        'direction': m.direction,
        'channel': m.channel,
        'subject': m.subject,
        'content': m.content,
        'content_assets': _public_content_assets(m.content_assets),
        'status': m.status,
        'approval_status': m.approval_status,
        'delivery_status': m.delivery_status,
        'approval_version': m.approval_version,
        'content_version': m.content_version,
        'intent_note': m.intent_note,
        'auto_approved': m.auto_approved,
        'approval_user_id': m.approval_user_id,
        'approved_at': m.approved_at,
        'reject_reason': m.reject_reason,
        'manual_attested_at': m.manual_attested_at,
        'manual_attested_by': m.manual_attested_by,
        'manual_attested_channel': m.manual_attested_channel,
        'sent_at': m.sent_at,
        'replied_at': m.replied_at,
        'error_message': m.error_message,
        'compliance_check': m.compliance_check,
        'task_run_id': m.task_run_id,
        'workflow_run_id': m.workflow_run_id,
        'created_time': m.created_time,
    })


def _event_to_dict(event: OutreachMessageEvent) -> dict[str, Any]:
    return redact_pii_value({
        'id': event.id,
        'event_type': event.event_type,
        'occurred_time': event.occurred_time,
        'actor_kind': event.actor_kind,
        'actor_id': event.actor_id,
        'approval_status': event.approval_status,
        'delivery_status': event.delivery_status,
        'approval_version': event.approval_version,
        'content_version': event.content_version,
        'error_class': event.error_class,
        'metadata': event.meta_data,
    })


def _assert_outreach_payload_safe(payload: dict[str, Any]) -> None:
    """触达写入拒绝可识别联系人明文，联系方式必须走私有渠道 reveal。"""
    try:
        assert_growth_pii_payload_safe(payload)
    except GrowthPiiBoundaryError as exc:
        raise errors.RequestError(
            msg='触达内容不得包含明文联系方式，请使用联系人私有渠道',
            data={'error_code': 'GROWTH_OUTREACH_PII_FORBIDDEN'},
        ) from exc


def _normalize_outreach_channel(channel: str) -> str:
    """只接受协议已声明的稳定渠道枚举，禁止自由文本进入消息元数据。"""
    normalized = channel.strip().casefold()
    if normalized not in _OUTREACH_CHANNELS:
        raise errors.RequestError(
            msg='触达渠道无效',
            data={'error_code': 'GROWTH_OUTREACH_CHANNEL_INVALID'},
        )
    return normalized


def _approval_scope(scope: GrowthScope | None) -> GrowthScope | None:
    """审批/取材维度：enterprise 下恒收敛到 assignee=自己（经理不代审他人名下触达，GE5.2 铁律）。

    经理 view=team 时也强制按「我的」过滤触达消息——经理只在审批页获「团队审批态」只读总览（另一端点），
    写操作（批/拒/取材/标已发）始终回落 assignee 的主人。
    """
    if scope is None or not scope.is_enterprise or scope.restrict_to_self:
        return scope
    return replace(scope, view='mine')


class GrowthOutreachService:
    """触达状态机 + 合规闸门，全 user_id 隔离，跨户 → NotFound。"""

    # ---------- 项目、事件与版本 ----------

    @staticmethod
    async def _append_event(
        db: AsyncSession,
        *,
        message: OutreachMessage,
        event_type: str,
        idempotency_key: str,
        actor_kind: str,
        actor_id: str | None,
        error_class: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """追加幂等事件；迁移期无项目的旧消息保持兼容但不伪造项目事件。"""
        if message.growth_project_id is None:
            return False
        safe_metadata = redact_pii_value(metadata or {})
        statement = (
            pg_insert(OutreachMessageEvent)
            .values(
                growth_project_id=message.growth_project_id,
                outreach_message_id=message.id,
                event_type=event_type,
                idempotency_key=idempotency_key[:200],
                actor_kind=actor_kind,
                actor_id=actor_id,
                approval_status=_legacy_approval_status(message),
                delivery_status=_legacy_delivery_status(message),
                approval_version=message.approval_version,
                content_version=message.content_version,
                error_class=error_class,
                meta_data=safe_metadata,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    OutreachMessageEvent.outreach_message_id,
                    OutreachMessageEvent.idempotency_key,
                ]
            )
            .returning(OutreachMessageEvent.id)
        )
        return (await db.execute(statement)).scalar_one_or_none() is not None

    @staticmethod
    async def _event_views(
        db: AsyncSession,
        *,
        message_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        if not message_ids:
            return {}
        events = (
            (
                await db.execute(
                    sa
                    .select(OutreachMessageEvent)
                    .where(OutreachMessageEvent.outreach_message_id.in_(message_ids))
                    .order_by(
                        OutreachMessageEvent.occurred_time,
                        OutreachMessageEvent.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        result: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            result.setdefault(event.outreach_message_id, []).append(_event_to_dict(event))
        return result

    @staticmethod
    async def _require_project_message(
        db: AsyncSession,
        *,
        message: OutreachMessage,
        growth_project_id: str | UUID | None,
    ) -> None:
        if growth_project_id is None:
            return
        try:
            expected = UUID(str(growth_project_id))
        except ValueError as exc:
            raise errors.RequestError(msg='获客项目 ID 无效') from exc
        if message.growth_project_id != expected:
            raise errors.NotFoundError(msg='触达消息不存在或无权访问')

    @staticmethod
    async def _target_snapshot(
        db: AsyncSession,
        *,
        message: OutreachMessage,
        customer: Customer,
    ) -> dict[str, Any]:
        """冻结目标渠道版本引用；不把密文、HMAC 或明文复制进消息。"""
        if message.channel == 'manual_assist' or customer.lead_contact_id is None:
            return {
                'lead_contact_id': customer.lead_contact_id,
                'contact_channel_id': None,
                'channel': message.channel,
            }
        conditions = [
            ContactChannel.lead_contact_id == customer.lead_contact_id,
            ContactChannel.channel == message.channel,
            ContactChannel.status == 'active',
            ContactChannel.retention_until > timezone.now(),
            ContactChannel.owner_scope == (customer.owner_scope or 'personal'),
        ]
        if customer.owner_scope == 'enterprise':
            conditions.append(ContactChannel.enterprise_id == customer.enterprise_id)
        else:
            conditions.append(ContactChannel.user_id == customer.user_id)
        channel = (
            (await db.execute(sa.select(ContactChannel).where(*conditions).order_by(ContactChannel.id.desc()).limit(1)))
            .scalars()
            .first()
        )
        return {
            'lead_contact_id': customer.lead_contact_id,
            'contact_channel_id': channel.id if channel else None,
            'channel': message.channel,
            'encryption_key_version': (channel.encryption_key_version if channel else None),
            'hash_key_version': channel.hash_key_version if channel else None,
        }

    @classmethod
    async def _freeze_approval_snapshot(
        cls,
        db: AsyncSession,
        *,
        message: OutreachMessage,
    ) -> None:
        customer = await db.get(Customer, message.customer_id)
        if customer is None:
            raise errors.NotFoundError(msg='客户不存在或无权访问')
        playbook_link = None
        if message.growth_project_id is not None:
            playbook_link = (
                (
                    await db.execute(
                        sa
                        .select(GrowthProjectPlaybook)
                        .where(
                            GrowthProjectPlaybook.growth_project_id == message.growth_project_id,
                            GrowthProjectPlaybook.status == 'active',
                        )
                        .order_by(GrowthProjectPlaybook.id.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        if playbook_link is not None:
            message.growth_project_playbook_id = playbook_link.id
            message.playbook_id = playbook_link.playbook_id
            message.playbook_version = playbook_link.playbook_version
        current_version = int(message.content_version or 1)
        public_assets = _public_content_assets(message.content_assets)
        snapshot = {
            'content': message.content,
            'subject': message.subject,
            'content_assets': public_assets,
            'content_version': current_version,
            'channel': message.channel,
            'target': await cls._target_snapshot(
                db,
                message=message,
                customer=customer,
            ),
            'playbook': {
                'growth_project_playbook_id': message.growth_project_playbook_id,
                'playbook_id': message.playbook_id,
                'playbook_version': message.playbook_version,
                'configuration_snapshot': (playbook_link.configuration_snapshot if playbook_link is not None else None),
            },
        }
        message.content_assets = {
            **public_assets,
            '_approval_snapshot': snapshot,
        }
        message.approval_version = current_version

    @staticmethod
    def _approved_snapshot(message: OutreachMessage) -> dict[str, Any]:
        snapshot = (message.content_assets or {}).get('_approval_snapshot')
        if not isinstance(snapshot, dict):
            raise errors.ConflictError(
                msg='批准快照缺失，请重新审核',
                data={'error_code': 'GROWTH_OUTREACH_APPROVAL_SNAPSHOT_MISSING'},
            )
        if (
            _legacy_approval_status(message) != 'approved'
            or message.approval_version != message.content_version
            or snapshot.get('content_version') != message.content_version
        ):
            raise errors.ConflictError(
                msg='内容已变化，请重新审核',
                data={'error_code': 'GROWTH_OUTREACH_APPROVAL_STALE'},
            )
        return snapshot

    # ---------- 合规 ----------

    @staticmethod
    async def _customer_optout_hit(
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring | None,
        use_private_channels: bool,
        user_id: int,
        customer: Customer,
        channel: str,
    ) -> bool:
        """客户任一联系方式在该渠道（或 all）登记退订 → 命中（硬闸，无豁免）。"""
        owner_scope = customer.owner_scope or 'personal'
        legacy_addresses = tuple(address for address in (customer.email, customer.phone, customer.wechat) if address)
        if not legacy_addresses and customer.lead_contact_id is not None:
            # 迁移窗口只读旧公共联系人作退订匹配，绝不复制到 customer 或响应。
            legacy_contact = await db.get(LeadContact, customer.lead_contact_id)
            if legacy_contact is not None:
                legacy_addresses = tuple(address for address in (legacy_contact.email, legacy_contact.phone) if address)
        if keyring is None:
            legacy_hashes = tuple(
                address_hash
                for address_hash in (_legacy_address_hash(address) for address in legacy_addresses)
                if address_hash
            )
            if legacy_hashes:
                legacy_hit = (
                    await db.execute(
                        sa
                        .select(OptoutRecord.id)
                        .where(
                            OptoutRecord.user_id == user_id,
                            OptoutRecord.channel.in_((channel, 'all')),
                            OptoutRecord.address_hash.in_(legacy_hashes),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if legacy_hit is not None:
                    return True
        else:
            for address in legacy_addresses:
                if await contact_privacy_service.is_opted_out(
                    db,
                    keyring=keyring,
                    owner_scope=owner_scope,
                    user_id=user_id,
                    enterprise_id=customer.enterprise_id,
                    channel=channel,
                    address=address,
                ):
                    return True

        if use_private_channels and keyring is not None and customer.lead_contact_id is not None:
            return await contact_privacy_service.is_private_contact_opted_out(
                db,
                keyring=keyring,
                lead_contact_id=customer.lead_contact_id,
                owner_scope=owner_scope,
                user_id=user_id,
                enterprise_id=customer.enterprise_id,
                channel=channel,
            )
        return False

    @staticmethod
    def _banned_words(content: str) -> list[str]:
        return [w for w in _BANNED_SUPERLATIVES if w in (content or '')]

    @staticmethod
    async def _freq_exceeded(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        channel: str,
        exclude_message_id: int | None = None,
    ) -> bool:
        since = timezone.now() - timedelta(days=_FREQ_WINDOW_DAYS)
        statement = (
            sa
            .select(sa.func.count())
            .select_from(OutreachMessage)
            .where(
                OutreachMessage.user_id == user_id,
                OutreachMessage.customer_id == customer_id,
                OutreachMessage.channel == channel,
                OutreachMessage.direction == 'outbound',
                sa.or_(
                    OutreachMessage.approval_status.in_(('pending_approval', 'approved')),
                    OutreachMessage.delivery_status.in_(('queued', 'sending', 'sent', 'delivered')),
                    OutreachMessage.status.in_(_QUOTA_STATUSES),
                ),
                OutreachMessage.created_time >= since,
            )
        )
        if exclude_message_id is not None:
            statement = statement.where(OutreachMessage.id != exclude_message_id)
        count = (await db.execute(statement)).scalar_one()
        return count >= _FREQ_MAX_PER_WINDOW

    @staticmethod
    def _quiet_hours_ok() -> bool:
        return _QUIET_START_HOUR <= timezone.now().hour < _QUIET_END_HOUR

    @classmethod
    async def check_compliance(
        cls,
        db: AsyncSession,
        *,
        keyring: GrowthPiiKeyring | None,
        use_private_channels: bool = False,
        user_id: int,
        customer: Customer,
        channel: str,
        content: str,
        exclude_message_id: int | None = None,
    ) -> dict[str, Any]:
        """返回 {blocked, block_status, reason, checks}。block_status ∈ blocked_optout/blocked_compliance。"""
        checks: dict[str, Any] = {'quiet_hours_ok': cls._quiet_hours_ok()}

        if await cls._customer_optout_hit(
            db,
            keyring=keyring,
            use_private_channels=use_private_channels,
            user_id=user_id,
            customer=customer,
            channel=channel,
        ):
            checks['optout'] = True
            return {'blocked': True, 'block_status': 'blocked_optout', 'reason': '客户已退订该渠道', 'checks': checks}
        checks['optout'] = False

        banned = cls._banned_words(content)
        if banned:
            checks['banned_words'] = banned
            return {
                'blocked': True,
                'block_status': 'blocked_compliance',
                'reason': f'话术含广告法极限词：{"、".join(banned)}',
                'checks': checks,
            }
        checks['banned_words'] = []

        if await cls._freq_exceeded(
            db,
            user_id=user_id,
            customer_id=customer.id,
            channel=channel,
            exclude_message_id=exclude_message_id,
        ):
            checks['freq_exceeded'] = True
            return {
                'blocked': True,
                'block_status': 'blocked_compliance',
                'reason': f'同客户同渠道一周内已达 {_FREQ_MAX_PER_WINDOW} 条频控上限',
                'checks': checks,
            }
        checks['freq_exceeded'] = False

        return {'blocked': False, 'block_status': None, 'reason': None, 'checks': checks}

    # ---------- 状态机 ----------

    @staticmethod
    async def _load_message(
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
        for_update: bool = False,
    ) -> OutreachMessage:
        # 审批/取材按 scope 门控：enterprise 下审批永远归 assignee 的主人维度（apply_scope 企业分支恒带
        # assignee 条件——见下方 list_pending_approvals 说明），经理不代审他人名下触达（GE5.2 铁律）。
        stmt = apply_scope(
            sa.select(OutreachMessage).where(OutreachMessage.id == message_id),
            OutreachMessage,
            user_id=user_id,
            scope=_approval_scope(scope),
        )
        if growth_project_id is not None:
            try:
                project_id = UUID(str(growth_project_id))
            except ValueError as exc:
                raise errors.RequestError(msg='获客项目 ID 无效') from exc
            stmt = stmt.where(OutreachMessage.growth_project_id == project_id)
        if for_update:
            stmt = stmt.with_for_update()
        m = (await db.execute(stmt)).scalar_one_or_none()
        if not m:
            raise errors.NotFoundError(msg='触达消息不存在或无权访问')
        m.approval_status = _legacy_approval_status(m)
        m.delivery_status = _legacy_delivery_status(m)
        m.content_version = int(m.content_version or 1)
        _sync_legacy_status(m)
        return m

    @staticmethod
    async def _customer_has_replied(db: AsyncSession, *, user_id: int, customer_id: int) -> bool:
        hit = (
            await db.execute(
                sa
                .select(OutreachMessage.id)
                .where(
                    OutreachMessage.user_id == user_id,
                    OutreachMessage.customer_id == customer_id,
                    OutreachMessage.direction == 'inbound',
                )
                .limit(1)
            )
        ).first()
        return hit is not None

    @staticmethod
    async def _is_first_contact(db: AsyncSession, *, user_id: int, customer_id: int, channel: str) -> bool:
        hit = (
            await db.execute(
                sa
                .select(OutreachMessage.id)
                .where(
                    OutreachMessage.user_id == user_id,
                    OutreachMessage.customer_id == customer_id,
                    OutreachMessage.channel == channel,
                    OutreachMessage.direction == 'outbound',
                )
                .limit(1)
            )
        ).first()
        return hit is None

    @classmethod
    async def draft_outreach(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        growth_project_id: str | UUID,
        customer_id: int,
        channel: str,
        content: str,
        idempotency_key: str,
        agent_id: str | None = None,
        subject: str | None = None,
        intent_note: str | None = None,
        content_assets: dict | None = None,
        opportunity_id: int | None = None,
        task_run_id: str | None = None,
        workflow_run_id: str | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """创建项目触达草稿；只落内容事实，不审批、不发送。"""
        normalized_channel = _normalize_outreach_channel(channel)
        _assert_outreach_payload_safe({
            'subject': subject,
            'content': content,
            'intent_note': intent_note,
            'content_assets': content_assets,
        })
        dedupe_key = _stable_key(user_id, 'outreach-draft', idempotency_key)
        existing = (
            await db.execute(
                sa.select(OutreachMessage).where(
                    OutreachMessage.user_id == user_id,
                    OutreachMessage.dedupe_key == dedupe_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            await cls._require_project_message(
                db,
                message=existing,
                growth_project_id=growth_project_id,
            )
            return _outreach_to_dict(existing)
        customer = await GrowthFunnelService._load_customer(
            db,
            user_id=user_id,
            customer_id=customer_id,
            scope=scope,
        )
        try:
            project_id = UUID(str(growth_project_id))
        except ValueError as exc:
            raise errors.RequestError(msg='获客项目 ID 无效') from exc
        if customer.growth_project_id != project_id:
            raise errors.NotFoundError(msg='客户不存在或无权访问')
        first_touch_candidate = await cls._is_first_contact(
            db,
            user_id=user_id,
            customer_id=customer_id,
            channel=normalized_channel,
        )
        message = OutreachMessage(
            customer_id=customer_id,
            opportunity_id=opportunity_id,
            user_id=user_id,
            growth_project_id=project_id,
            agent_id=agent_id,
            direction='outbound',
            channel=normalized_channel,
            subject=subject,
            content=content,
            content_assets=content_assets or {},
            status='draft',
            intent_note=intent_note,
            auto_approved=False,
            approval_status='draft',
            delivery_status='not_queued',
            approval_version=None,
            content_version=1,
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            compliance_check={},
            dedupe_key=dedupe_key,
            owner_scope=customer.owner_scope,
            enterprise_id=customer.enterprise_id,
            assignee=customer.assignee,
        )
        db.add(message)
        await db.flush()
        await cls._append_event(
            db,
            message=message,
            event_type='drafted',
            idempotency_key=f'draft:{idempotency_key}',
            actor_kind='agent' if agent_id else 'owner',
            actor_id=agent_id,
            metadata={
                'channel': normalized_channel,
                'first_touch_candidate': first_touch_candidate,
            },
        )
        return _outreach_to_dict(message)

    @classmethod
    async def submit_outreach(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        growth_project_id: str | UUID,
        message_id: int,
        expected_content_version: int,
        idempotency_key: str,
        whitelist_auto_send: bool = False,
        scope: GrowthScope | None = None,
        keyring: GrowthPiiKeyring | None = None,
    ) -> dict[str, Any]:
        """提交草稿：重做合规检查并进入待批、自动批准或拦截态。"""
        message = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            scope=scope,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        current_version = _assert_content_version(
            message,
            expected_content_version,
        )
        replay_key = f'submit:{idempotency_key}'[:200]
        replay = await db.scalar(
            sa.select(OutreachMessageEvent.id).where(
                OutreachMessageEvent.outreach_message_id == message.id,
                OutreachMessageEvent.idempotency_key == replay_key,
            )
        )
        if replay is not None:
            return _outreach_to_dict(message)
        if _legacy_approval_status(message) not in {'draft', 'pending_approval'}:
            raise errors.ConflictError(
                msg='当前触达状态不可再次提交',
                data={'error_code': 'GROWTH_OUTREACH_SUBMIT_STATE_CONFLICT'},
            )
        customer = await GrowthFunnelService._load_customer(
            db,
            user_id=user_id,
            customer_id=message.customer_id,
            scope=scope,
        )
        needs_keyring = (
            keyring is not None or settings.GROWTH_PII_NEW_WRITE_ENABLED or settings.GROWTH_PII_SHADOW_READ_ENABLED
        )
        active_keyring = keyring or (require_growth_pii_keyring() if needs_keyring else None)
        compliance = await cls.check_compliance(
            db,
            keyring=active_keyring,
            use_private_channels=(settings.GROWTH_PII_SHADOW_READ_ENABLED or keyring is not None),
            user_id=user_id,
            customer=customer,
            channel=message.channel,
            content=message.content,
            exclude_message_id=message.id,
        )
        # 当前草稿本身已落库，首次触达判定需排除自己。
        previous_outbound = await db.scalar(
            sa
            .select(OutreachMessage.id)
            .where(
                OutreachMessage.user_id == user_id,
                OutreachMessage.customer_id == message.customer_id,
                OutreachMessage.channel == message.channel,
                OutreachMessage.direction == 'outbound',
                OutreachMessage.id != message.id,
            )
            .limit(1)
        )
        is_first = previous_outbound is None
        replied = await cls._customer_has_replied(
            db,
            user_id=user_id,
            customer_id=message.customer_id,
        )
        if compliance['blocked']:
            message.approval_status = 'draft'
            message.delivery_status = compliance['block_status']
            message.auto_approved = False
            event_type = str(compliance['block_status'])
            note = f'触达被合规拦截（{compliance["reason"]}）'
        elif not is_first and whitelist_auto_send and replied:
            message.approval_status = 'approved'
            message.delivery_status = 'not_queued' if message.channel == 'manual_assist' else 'queued'
            message.auto_approved = True
            message.approved_at = timezone.now()
            await cls._freeze_approval_snapshot(db, message=message)
            event_type = 'approved'
            note = f'触达自动放行（白名单）：{message.intent_note or message.content[:40]}'
        else:
            message.approval_status = 'pending_approval'
            message.delivery_status = 'not_queued'
            message.auto_approved = False
            event_type = 'approval_requested'
            note = f'触达待主人审批：{message.intent_note or message.content[:40]}'
        message.compliance_check = {
            **compliance['checks'],
            'is_first_contact': is_first,
            'content_version': current_version,
        }
        _sync_legacy_status(message)
        await db.flush()
        await cls._append_event(
            db,
            message=message,
            event_type=event_type,
            idempotency_key=replay_key,
            actor_kind='agent',
            actor_id=message.agent_id,
            error_class=(str(compliance['block_status']) if compliance['blocked'] else None),
            metadata={
                'reason': compliance['reason'],
                'checks': compliance['checks'],
                'is_first_contact': is_first,
            },
        )
        if message.delivery_status == 'queued':
            await cls._append_event(
                db,
                message=message,
                event_type='queued',
                idempotency_key=f'{replay_key}:queued',
                actor_kind='system',
                actor_id='growth_dispatch_worker',
                metadata={'approval': 'auto'},
            )
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=message.customer_id,
            kind='outreach',
            content=note,
            opportunity_id=message.opportunity_id,
            actor_kind='agent',
            actor_id=message.agent_id,
            ref_table='outreach_message',
            ref_id=str(message.id),
        )
        return _outreach_to_dict(message)

    @classmethod
    async def send_outreach(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        channel: str,
        content: str,
        agent_id: str | None = None,
        subject: str | None = None,
        intent_note: str | None = None,
        content_assets: dict | None = None,
        opportunity_id: int | None = None,
        task_run_id: str | None = None,
        workflow_run_id: str | None = None,
        whitelist_auto_send: bool = False,
        scope: GrowthScope | None = None,
        keyring: GrowthPiiKeyring | None = None,
        growth_project_id: str | UUID | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """兼容的一步提交入口；内部严格复用 draft → submit。"""
        customer = await GrowthFunnelService._load_customer(
            db,
            user_id=user_id,
            customer_id=customer_id,
            scope=scope,
        )
        project_id = growth_project_id or customer.growth_project_id
        if project_id is None:
            # 存量无项目兼容：仍使用稳定正文键，S10 切流后删除此分支。
            normalized_channel = _normalize_outreach_channel(channel)
            legacy_key = idempotency_key or _stable_key(
                user_id,
                customer_id,
                normalized_channel,
                content,
            )
            project_id = customer.growth_project_id
            if project_id is None:
                return await cls._send_legacy_outreach(
                    db,
                    user_id=user_id,
                    customer=customer,
                    channel=normalized_channel,
                    content=content,
                    agent_id=agent_id,
                    subject=subject,
                    intent_note=intent_note,
                    content_assets=content_assets,
                    opportunity_id=opportunity_id,
                    task_run_id=task_run_id,
                    workflow_run_id=workflow_run_id,
                    whitelist_auto_send=whitelist_auto_send,
                    keyring=keyring,
                    idempotency_key=legacy_key,
                )
        stable_idempotency = idempotency_key or _stable_key(
            user_id,
            customer_id,
            channel,
            content,
        )
        draft = await cls.draft_outreach(
            db,
            user_id=user_id,
            growth_project_id=project_id,
            customer_id=customer_id,
            channel=channel,
            content=content,
            idempotency_key=stable_idempotency,
            agent_id=agent_id,
            subject=subject,
            intent_note=intent_note,
            content_assets=content_assets,
            opportunity_id=opportunity_id,
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            scope=scope,
        )
        return await cls.submit_outreach(
            db,
            user_id=user_id,
            growth_project_id=project_id,
            message_id=int(draft['id']),
            expected_content_version=int(draft['content_version'] or 1),
            idempotency_key=stable_idempotency,
            whitelist_auto_send=whitelist_auto_send,
            scope=scope,
            keyring=keyring,
        )

    @classmethod
    async def _send_legacy_outreach(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        customer: Customer,
        channel: str,
        content: str,
        agent_id: str | None,
        subject: str | None,
        intent_note: str | None,
        content_assets: dict | None,
        opportunity_id: int | None,
        task_run_id: str | None,
        workflow_run_id: str | None,
        whitelist_auto_send: bool,
        keyring: GrowthPiiKeyring | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """迁移期无项目旧客户兼容路径；新项目不得进入。"""
        _assert_outreach_payload_safe({
            'subject': subject,
            'content': content,
            'intent_note': intent_note,
            'content_assets': content_assets,
        })
        dedupe_key = _stable_key(user_id, 'legacy-outreach', idempotency_key)
        existing = (
            await db.execute(
                sa.select(OutreachMessage).where(
                    OutreachMessage.user_id == user_id,
                    OutreachMessage.dedupe_key == dedupe_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _outreach_to_dict(existing)
        needs_keyring = (
            keyring is not None or settings.GROWTH_PII_NEW_WRITE_ENABLED or settings.GROWTH_PII_SHADOW_READ_ENABLED
        )
        active_keyring = keyring or (require_growth_pii_keyring() if needs_keyring else None)
        compliance = await cls.check_compliance(
            db,
            keyring=active_keyring,
            use_private_channels=(settings.GROWTH_PII_SHADOW_READ_ENABLED or keyring is not None),
            user_id=user_id,
            customer=customer,
            channel=channel,
            content=content,
        )
        is_first = await cls._is_first_contact(
            db,
            user_id=user_id,
            customer_id=customer.id,
            channel=channel,
        )
        replied = await cls._customer_has_replied(
            db,
            user_id=user_id,
            customer_id=customer.id,
        )
        if compliance['blocked']:
            approval_status = 'draft'
            delivery_status = str(compliance['block_status'])
            auto_approved = False
        elif not is_first and whitelist_auto_send and replied:
            approval_status = 'approved'
            delivery_status = 'not_queued' if channel == 'manual_assist' else 'queued'
            auto_approved = True
        else:
            approval_status = 'pending_approval'
            delivery_status = 'not_queued'
            auto_approved = False
        message = OutreachMessage(
            customer_id=customer.id,
            opportunity_id=opportunity_id,
            user_id=user_id,
            growth_project_id=None,
            agent_id=agent_id,
            direction='outbound',
            channel=channel,
            subject=subject,
            content=content,
            content_assets=content_assets or {},
            status=approval_status,
            intent_note=intent_note,
            auto_approved=auto_approved,
            approval_status=approval_status,
            delivery_status=delivery_status,
            approval_version=(1 if approval_status == 'approved' else None),
            content_version=1,
            approved_at=(timezone.now() if approval_status == 'approved' else None),
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            compliance_check=compliance['checks'],
            dedupe_key=dedupe_key,
            owner_scope=customer.owner_scope,
            enterprise_id=customer.enterprise_id,
            assignee=customer.assignee,
        )
        _sync_legacy_status(message)
        db.add(message)
        await db.flush()
        if approval_status == 'approved':
            await cls._freeze_approval_snapshot(db, message=message)
        note = (
            f'触达被合规拦截（{compliance["reason"]}）'
            if compliance['blocked']
            else (
                f'触达自动放行（白名单）：{intent_note or content[:40]}'
                if approval_status == 'approved'
                else f'触达待主人审批：{intent_note or content[:40]}'
            )
        )
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer.id,
            kind='outreach',
            content=note,
            opportunity_id=opportunity_id,
            actor_kind='agent',
            actor_id=agent_id,
            ref_table='outreach_message',
            ref_id=str(message.id),
        )
        return _outreach_to_dict(message)

    @classmethod
    async def approve_outreach(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        approver_user_id: int,
        edited_content: str | None = None,
        expected_content_version: int | None = None,
        growth_project_id: str | UUID | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """owner 审批通过（可改话术后批，修改进版本留痕）。enterprise 下仅 assignee 主人可批。"""
        m = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            scope=scope,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        current_version = _assert_content_version(m, expected_content_version)
        if _legacy_approval_status(m) != 'pending_approval':
            raise errors.ConflictError(
                msg=f'仅待审批触达可批准（当前 {_legacy_approval_status(m)}）',
                data={'error_code': 'GROWTH_OUTREACH_APPROVAL_STATE_CONFLICT'},
            )
        if edited_content is not None and edited_content != m.content:
            _assert_outreach_payload_safe({'content': edited_content})
            revisions = list((m.content_assets or {}).get('revisions', []))
            revisions.append({'before': redact_pii_value(m.content), 'by': approver_user_id})
            m.content_assets = {
                **_public_content_assets(m.content_assets),
                'revisions': revisions,
            }
            m.content = edited_content
            current_version += 1
            m.content_version = current_version
        m.approval_status = 'approved'
        m.delivery_status = 'not_queued' if m.channel == 'manual_assist' else 'queued'
        m.auto_approved = False
        m.approval_user_id = approver_user_id
        m.approved_at = timezone.now()
        m.reject_reason = None
        await cls._freeze_approval_snapshot(db, message=m)
        _sync_legacy_status(m)
        await db.flush()
        await cls._append_event(
            db,
            message=m,
            event_type='approved',
            idempotency_key=f'approve:v{m.content_version}',
            actor_kind='owner',
            actor_id=str(approver_user_id),
            metadata={'edited': edited_content is not None},
        )
        if m.delivery_status == 'queued':
            await cls._append_event(
                db,
                message=m,
                event_type='queued',
                idempotency_key=f'queued:v{m.content_version}',
                actor_kind='system',
                actor_id='growth_dispatch_worker',
                metadata={'approval': 'owner'},
            )
        return _outreach_to_dict(m)

    @classmethod
    async def edit_outreach(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        growth_project_id: str | UUID,
        message_id: int,
        expected_content_version: int,
        content: str,
        content_assets: dict[str, Any] | None = None,
        subject: str | None = None,
        channel: str | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """Owner 改稿；任何旧批准立即失效并回到待审批。"""
        _assert_outreach_payload_safe({
            'subject': subject,
            'content': content,
            'content_assets': content_assets,
        })
        message = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            scope=scope,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        current_version = _assert_content_version(
            message,
            expected_content_version,
        )
        if _legacy_delivery_status(message) in {
            'sending',
            'sent',
            'delivered',
        }:
            raise errors.ConflictError(
                msg='触达已进入投递，不能继续改稿',
                data={'error_code': 'GROWTH_OUTREACH_ALREADY_DISPATCHED'},
            )
        normalized_channel = _normalize_outreach_channel(channel) if channel else message.channel
        revisions = list((message.content_assets or {}).get('revisions', []))
        revisions.append({
            'before': redact_pii_value(message.content),
            'content_version': current_version,
            'by': str(user_id),
        })
        message.content = content
        if subject is not None:
            message.subject = subject
        message.channel = normalized_channel
        message.content_assets = {
            **(content_assets or _public_content_assets(message.content_assets)),
            'revisions': revisions,
        }
        message.content_version = current_version + 1
        message.approval_status = 'pending_approval'
        message.delivery_status = 'not_queued'
        message.auto_approved = False
        message.approved_at = None
        message.approval_user_id = None
        _sync_legacy_status(message)
        await db.flush()
        await cls._append_event(
            db,
            message=message,
            event_type='approval_requested',
            idempotency_key=f'edit:v{message.content_version}',
            actor_kind='owner',
            actor_id=str(user_id),
            metadata={
                'invalidated_approval_version': message.approval_version,
                'channel': normalized_channel,
            },
        )
        return _outreach_to_dict(message)

    @classmethod
    async def reject_outreach(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        approver_user_id: int,
        reason: str,
        expected_content_version: int | None = None,
        growth_project_id: str | UUID | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """owner 拒绝（reason 回流 timeline，分身下轮学习）。enterprise 下仅 assignee 主人可拒。"""
        m = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            scope=scope,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        _assert_content_version(m, expected_content_version)
        if _legacy_approval_status(m) != 'pending_approval':
            raise errors.ConflictError(
                msg=f'仅待审批触达可拒绝（当前 {_legacy_approval_status(m)}）',
                data={'error_code': 'GROWTH_OUTREACH_APPROVAL_STATE_CONFLICT'},
            )
        safe_reason = redact_pii_value(reason)
        m.approval_status = 'rejected'
        m.delivery_status = 'not_queued'
        m.reject_reason = safe_reason
        m.approval_user_id = approver_user_id
        _sync_legacy_status(m)
        await db.flush()
        await cls._append_event(
            db,
            message=m,
            event_type='rejected',
            idempotency_key=f'reject:v{m.content_version}',
            actor_kind='owner',
            actor_id=str(approver_user_id),
            metadata={'reason': safe_reason},
        )
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=m.customer_id,
            kind='note',
            content=f'主人拒绝触达：{safe_reason}',
            actor_kind='owner',
            actor_id=str(approver_user_id),
            ref_table='outreach_message',
            ref_id=str(m.id),
        )
        return _outreach_to_dict(m)

    @classmethod
    async def build_send_material(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        expected_content_version: int | None = None,
        growth_project_id: str | UUID | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """manual_assist「复制发送」素材包（设计 §8.2 G4）：只返回 approved 话术和素材链接。

        联系方式必须由 Owner 另行选择一个私有渠道并调用专用 reveal；本接口不构成 PII 读取。
        仅 owner 自己的数据（跨户 → NotFound）；仅 approved 触达可取。enterprise 下仅 assignee 主人。
        """
        m = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            scope=scope,
            growth_project_id=growth_project_id,
        )
        _assert_content_version(m, expected_content_version)
        if m.channel != 'manual_assist':
            raise errors.ConflictError(
                msg='仅人工辅助触达可取复制发送素材包',
                data={'error_code': 'GROWTH_OUTREACH_NOT_MANUAL_ASSIST'},
            )
        if _legacy_approval_status(m) != 'approved':
            raise errors.ForbiddenError(msg=(f'仅已批准触达可取复制发送素材包（当前 {_legacy_approval_status(m)}）'))
        snapshot = cls._approved_snapshot(m)
        customer = await GrowthFunnelService._load_customer(
            db,
            user_id=user_id,
            customer_id=m.customer_id,
            scope=scope,
        )

        return {
            'message_id': m.id,
            'customer_id': m.customer_id,
            'company_name': customer.company_name,
            'lead_contact_id': customer.lead_contact_id,
            'channel': m.channel,
            'content_version': m.content_version,
            'approval_version': m.approval_version,
            'content': redact_pii_value(snapshot['content']),
            'content_assets': redact_pii_value(snapshot.get('content_assets') or {}),
            'intent_note': redact_pii_value(m.intent_note),
        }

    @classmethod
    async def attest_manual_send(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        growth_project_id: str | UUID,
        message_id: int,
        expected_content_version: int,
        actor_id: str,
        channel_actual: str,
        proof: dict[str, Any],
        idempotency_key: str,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """记录人工发送证明；不写 sent/delivered，不伪造渠道回执。"""
        message = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            scope=scope,
            growth_project_id=growth_project_id,
            for_update=True,
        )
        _assert_content_version(message, expected_content_version)
        cls._approved_snapshot(message)
        if message.channel != 'manual_assist':
            raise errors.ConflictError(
                msg='仅人工辅助触达可记录人工证明',
                data={'error_code': 'GROWTH_OUTREACH_NOT_MANUAL_ASSIST'},
            )
        normalized_channel = _normalize_outreach_channel(channel_actual)
        safe_proof = redact_pii_value(proof)
        event_key = f'manual:{idempotency_key}'[:200]
        existing = await db.scalar(
            sa.select(OutreachMessageEvent.id).where(
                OutreachMessageEvent.outreach_message_id == message.id,
                OutreachMessageEvent.idempotency_key == event_key,
            )
        )
        if existing is not None:
            return _outreach_to_dict(message)
        message.manual_attested_at = timezone.now()
        message.manual_attested_by = actor_id
        message.manual_attested_channel = normalized_channel
        await db.flush()
        await cls._append_event(
            db,
            message=message,
            event_type='manual_attested',
            idempotency_key=event_key,
            actor_kind='owner',
            actor_id=actor_id,
            metadata={
                'channel_actual': normalized_channel,
                'proof': safe_proof,
                'does_not_assert_delivery': True,
            },
        )
        await growth_attribution_service.record_outreach(
            db,
            message=message,
            event_type='outreach_sent',
            event_key=event_key,
            channel=normalized_channel,
            metadata={
                'manual_attested': True,
                'does_not_assert_delivery': True,
            },
        )
        await growth_attribution_service.record_cost(
            db,
            message=message,
            event_key=event_key,
            channel=normalized_channel,
            amount=Decimal(0),
            currency='CNY',
            metadata={'manual_assist': True},
        )
        return _outreach_to_dict(message)

    @classmethod
    async def mark_sending(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """发送 worker 接手（approved → sending）。"""
        m = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            for_update=True,
        )
        cls._approved_snapshot(m)
        if _legacy_delivery_status(m) not in {'queued', 'not_queued'}:
            raise errors.ConflictError(
                msg=f'当前投递态不可进入发送（{_legacy_delivery_status(m)}）',
                data={'error_code': 'GROWTH_OUTREACH_DELIVERY_STATE_CONFLICT'},
            )
        m.delivery_status = 'sending'
        _sync_legacy_status(m)
        await db.flush()
        await cls._append_event(
            db,
            message=m,
            event_type='sending',
            idempotency_key=(idempotency_key or f'sending:v{m.approval_version}'),
            actor_kind='system',
            actor_id='growth_dispatch_worker',
        )
        return _outreach_to_dict(m)

    @classmethod
    async def mark_sent(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        channel_actual: str | None = None,
        scope: GrowthScope | None = None,
        provider_event_id: str | None = None,
        cost_amount: Decimal | None = None,
        cost_currency: str | None = None,
        cost_known: bool = False,
    ) -> dict[str, Any]:
        """真实渠道受理/发送成功；manual_assist 证明必须走 attest_manual_send。"""
        normalized_channel = _normalize_outreach_channel(channel_actual) if channel_actual else None
        m = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            scope=scope,
            for_update=True,
        )
        cls._approved_snapshot(m)
        if m.channel == 'manual_assist' and scope is not None:
            raise errors.ConflictError(
                msg='人工辅助发送只能记录人工证明，不能伪造渠道送达',
                data={'error_code': 'GROWTH_MANUAL_ATTESTATION_REQUIRED'},
            )
        if _legacy_delivery_status(m) not in {'queued', 'sending'}:
            raise errors.ConflictError(
                msg=f'当前投递态不可标记已发送（{_legacy_delivery_status(m)}）',
                data={'error_code': 'GROWTH_OUTREACH_DELIVERY_STATE_CONFLICT'},
            )
        m.delivery_status = 'sent'
        m.sent_at = timezone.now()
        if normalized_channel:
            m.content_assets = {
                **(m.content_assets or {}),
                'channel_actual': normalized_channel,
            }
        _sync_legacy_status(m)
        await db.flush()
        await cls._append_event(
            db,
            message=m,
            event_type='sent',
            idempotency_key=(f'provider:{provider_event_id}' if provider_event_id else f'sent:v{m.approval_version}'),
            actor_kind='provider' if provider_event_id else 'system',
            actor_id=normalized_channel or m.channel,
            metadata={'channel_actual': normalized_channel or m.channel},
        )
        event_key = f'provider:{provider_event_id}' if provider_event_id else f'sent:v{m.approval_version}'
        actual_channel = normalized_channel or m.channel
        await growth_attribution_service.record_outreach(
            db,
            message=m,
            event_type='outreach_sent',
            event_key=event_key,
            channel=actual_channel,
        )
        await growth_attribution_service.record_cost(
            db,
            message=m,
            event_key=event_key,
            channel=actual_channel,
            amount=cost_amount if cost_known else None,
            currency=(cost_currency or 'CNY') if cost_known else None,
        )
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=m.customer_id,
            kind='outreach',
            content=f'已通过 {normalized_channel or m.channel} 发送',
            actor_kind='agent',
            actor_id=m.agent_id,
            ref_table='outreach_message',
            ref_id=str(m.id),
        )
        return _outreach_to_dict(m)

    @classmethod
    async def record_delivery_receipt(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        provider_event_id: str,
        outcome: str,
        channel_actual: str | None = None,
        error_class: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        """记录 provider 投递回执；渠道受理、送达和失败是三个独立事实。"""
        if outcome not in {'sent', 'delivered', 'failed'}:
            raise errors.RequestError(msg='渠道回执结果无效')
        normalized_channel = _normalize_outreach_channel(channel_actual) if channel_actual else None
        message = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            for_update=True,
        )
        cls._approved_snapshot(message)
        if message.channel == 'manual_assist':
            raise errors.ConflictError(
                msg='人工辅助发送不接受渠道送达回执',
                data={'error_code': 'GROWTH_MANUAL_ATTESTATION_REQUIRED'},
            )
        event_key = f'provider:{provider_event_id}'[:200]
        existing = await db.scalar(
            sa.select(OutreachMessageEvent.id).where(
                OutreachMessageEvent.outreach_message_id == message.id,
                OutreachMessageEvent.idempotency_key == event_key,
            )
        )
        if existing is not None:
            return _outreach_to_dict(message)
        current_delivery = _legacy_delivery_status(message)
        allowed_previous = {
            'sent': {'queued', 'sending'},
            'delivered': {'sent'},
            'failed': {'queued', 'sending', 'sent'},
        }
        if current_delivery not in allowed_previous[outcome]:
            raise errors.ConflictError(
                msg=f'当前投递态不可记录 {outcome} 回执（{current_delivery}）',
                data={'error_code': 'GROWTH_OUTREACH_DELIVERY_STATE_CONFLICT'},
            )
        message.delivery_status = outcome
        if outcome in {'sent', 'delivered'} and message.sent_at is None:
            message.sent_at = timezone.now()
        if outcome == 'failed':
            message.error_message = str(redact_pii_value(detail or '渠道投递失败'))
        if normalized_channel:
            message.content_assets = {
                **(message.content_assets or {}),
                'channel_actual': normalized_channel,
            }
        _sync_legacy_status(message)
        await db.flush()
        await cls._append_event(
            db,
            message=message,
            event_type=outcome,
            idempotency_key=event_key,
            actor_kind='provider',
            actor_id=normalized_channel or message.channel,
            error_class=error_class if outcome == 'failed' else None,
            metadata={
                'channel_actual': normalized_channel or message.channel,
                'detail': redact_pii_value(detail) if detail else None,
            },
        )
        if outcome == 'sent':
            actual_channel = normalized_channel or message.channel
            await growth_attribution_service.record_outreach(
                db,
                message=message,
                event_type='outreach_sent',
                event_key=event_key,
                channel=actual_channel,
            )
            await growth_attribution_service.record_cost(
                db,
                message=message,
                event_key=event_key,
                channel=actual_channel,
                amount=None,
                currency=None,
            )
        return _outreach_to_dict(message)

    @classmethod
    async def mark_failed(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        error: str,
        error_class: str = 'send_failed',
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """发送失败（如实回报错误；sending/approved → failed）。"""
        m = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            for_update=True,
        )
        if _legacy_delivery_status(m) not in {
            'not_queued',
            'queued',
            'sending',
        }:
            raise errors.ConflictError(
                msg=f'当前投递态不可标记失败（{_legacy_delivery_status(m)}）',
                data={'error_code': 'GROWTH_OUTREACH_DELIVERY_STATE_CONFLICT'},
            )
        m.delivery_status = 'failed'
        m.error_message = redact_pii_value(error)
        _sync_legacy_status(m)
        await db.flush()
        await cls._append_event(
            db,
            message=m,
            event_type='failed',
            idempotency_key=(idempotency_key or f'failed:{error_class}:v{m.approval_version}'),
            actor_kind='system',
            actor_id='growth_dispatch_worker',
            error_class=error_class,
            metadata={'message': m.error_message},
        )
        return _outreach_to_dict(m)

    @classmethod
    async def mark_blocked(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        delivery_status: str,
        error_class: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """发送前硬门禁拦截；保留原审批事实。"""
        if delivery_status not in {'blocked_optout', 'blocked_compliance'}:
            raise errors.RequestError(msg='触达拦截状态无效')
        message = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            for_update=True,
        )
        message.delivery_status = delivery_status
        message.error_message = str(redact_pii_value(reason))
        _sync_legacy_status(message)
        await db.flush()
        await cls._append_event(
            db,
            message=message,
            event_type=delivery_status,
            idempotency_key=idempotency_key,
            actor_kind='system',
            actor_id='growth_dispatch_worker',
            error_class=error_class,
            metadata={'reason': message.error_message},
        )
        return _outreach_to_dict(message)

    @classmethod
    async def schedule_retry(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """仅对服务端保证去重的瞬时失败重新排队。"""
        message = await cls._load_message(
            db,
            user_id=user_id,
            message_id=message_id,
            for_update=True,
        )
        message.delivery_status = 'queued'
        message.error_message = str(redact_pii_value(reason))
        _sync_legacy_status(message)
        await db.flush()
        await cls._append_event(
            db,
            message=message,
            event_type='retry_scheduled',
            idempotency_key=idempotency_key,
            actor_kind='system',
            actor_id='growth_dispatch_worker',
            error_class='retryable_transport_error',
            metadata={
                'reason': message.error_message,
                'requires_provider_dedupe': True,
            },
        )
        return _outreach_to_dict(message)

    @classmethod
    async def dispatch_preflight(  # noqa: C901
        cls,
        db: AsyncSession,
        *,
        message: OutreachMessage,
        now_hour: int,
        keyring: GrowthPiiKeyring | None = None,
    ) -> dict[str, Any]:
        """worker 发送前重新校验版本、退订、时段、频控、预算、权益、项目和目标地址。"""
        try:
            snapshot = cls._approved_snapshot(message)
        except errors.ConflictError:
            return {
                'allowed': False,
                'action': 'blocked_compliance',
                'error_class': 'stale_approval',
                'reason': '批准版本已失效，请重新审核',
            }
        if message.growth_project_id is None:
            if not (_QUIET_START_HOUR <= now_hour < _QUIET_END_HOUR):
                return {
                    'allowed': False,
                    'action': 'retry_scheduled',
                    'error_class': 'quiet_hours',
                    'reason': '当前处于静默时段',
                }
            return {
                'allowed': True,
                'snapshot': snapshot,
                'legacy': True,
            }
        project = await db.get(GrowthProject, message.growth_project_id)
        if project is None or project.status != 'active':
            return {
                'allowed': False,
                'action': 'blocked_compliance',
                'error_class': 'project_not_active',
                'reason': '获客项目未处于运行中',
            }
        if _is_quiet_hour(
            now_hour,
            start=project.quiet_hours_start,
            end=project.quiet_hours_end,
        ):
            return {
                'allowed': False,
                'action': 'retry_scheduled',
                'error_class': 'quiet_hours',
                'reason': '当前处于项目静默时段',
            }
        subject_type = 'enterprise' if project.owner_scope == 'enterprise' else 'owner'
        subject_id = str(project.enterprise_id) if subject_type == 'enterprise' else project.owner_hasn_id
        entitlement = await app_catalog_service.get_active_entitlement(
            db,
            app_id='growth',
            subject_type=subject_type,
            subject_id=subject_id,
        )
        if entitlement is None:
            return {
                'allowed': False,
                'action': 'blocked_compliance',
                'error_class': 'entitlement_required',
                'reason': '获客权益已暂停或失效',
            }
        day_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        daily_usage = await db.scalar(
            sa
            .select(sa.func.count())
            .select_from(GrowthAttributionEvent)
            .where(
                GrowthAttributionEvent.growth_project_id == project.id,
                GrowthAttributionEvent.event_type == 'outreach_sent',
                GrowthAttributionEvent.occurred_time >= day_start,
            )
        )
        if int(daily_usage or 0) >= project.daily_outreach_limit:
            return {
                'allowed': False,
                'action': 'blocked_compliance',
                'error_class': 'daily_outreach_limit',
                'reason': '项目今日触达频控已达上限',
            }
        quota_value = (entitlement.quota_json or {}).get('monthly_outreach')
        if quota_value is not None:
            try:
                monthly_quota = int(quota_value)
            except (TypeError, ValueError):
                monthly_quota = 0
            monthly_usage = await db.scalar(
                sa
                .select(sa.func.count())
                .select_from(GrowthAttributionEvent)
                .where(
                    GrowthAttributionEvent.growth_project_id == project.id,
                    GrowthAttributionEvent.event_type == 'outreach_sent',
                    GrowthAttributionEvent.occurred_time >= month_start,
                )
            )
            if monthly_quota <= 0 or int(monthly_usage or 0) >= monthly_quota:
                return {
                    'allowed': False,
                    'action': 'blocked_compliance',
                    'error_class': 'entitlement_quota_exhausted',
                    'reason': '获客权益的月度触达配额已用尽',
                }
        if project.monthly_budget is not None:
            unknown_cost_count = await db.scalar(
                sa
                .select(sa.func.count())
                .select_from(GrowthAttributionEvent)
                .where(
                    GrowthAttributionEvent.growth_project_id == project.id,
                    GrowthAttributionEvent.event_type == 'cost',
                    GrowthAttributionEvent.occurred_time >= month_start,
                    sa.or_(
                        GrowthAttributionEvent.amount.is_(None),
                        GrowthAttributionEvent.meta_data['cost_state'].astext == 'unknown',
                    ),
                )
            )
            if int(unknown_cost_count or 0) > 0:
                return {
                    'allowed': False,
                    'action': 'blocked_compliance',
                    'error_class': 'cost_unknown_budget_guard',
                    'reason': '存在未知成本用量，确认成本前暂停自动发送',
                }
            spent = await db.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(GrowthAttributionEvent.amount), 0)).where(
                    GrowthAttributionEvent.growth_project_id == project.id,
                    GrowthAttributionEvent.event_type == 'cost',
                    GrowthAttributionEvent.occurred_time >= month_start,
                )
            )
            if spent is not None and spent >= project.monthly_budget:
                return {
                    'allowed': False,
                    'action': 'blocked_compliance',
                    'error_class': 'monthly_budget_exhausted',
                    'reason': '项目月度预算已用尽',
                }
        customer = await db.get(Customer, message.customer_id)
        if customer is None or customer.growth_project_id != project.id:
            return {
                'allowed': False,
                'action': 'failed',
                'error_class': 'customer_target_missing',
                'reason': '客户目标不存在或已移出项目',
            }
        needs_keyring = (
            keyring is not None or settings.GROWTH_PII_NEW_WRITE_ENABLED or settings.GROWTH_PII_SHADOW_READ_ENABLED
        )
        active_keyring = keyring or (require_growth_pii_keyring() if needs_keyring else None)
        compliance = await cls.check_compliance(
            db,
            keyring=active_keyring,
            use_private_channels=(settings.GROWTH_PII_SHADOW_READ_ENABLED or keyring is not None),
            user_id=message.user_id,
            customer=customer,
            channel=message.channel,
            content=str(snapshot.get('content') or ''),
            exclude_message_id=message.id,
        )
        if compliance['blocked']:
            return {
                'allowed': False,
                'action': compliance['block_status'],
                'error_class': str(compliance['block_status']),
                'reason': compliance['reason'],
            }
        target = snapshot.get('target')
        target_channel_id = target.get('contact_channel_id') if isinstance(target, dict) else None
        if message.channel != 'manual_assist':
            channel_row = (
                await db.get(ContactChannel, int(target_channel_id)) if target_channel_id is not None else None
            )
            if channel_row is None or channel_row.status != 'active' or channel_row.retention_until <= timezone.now():
                return {
                    'allowed': False,
                    'action': 'failed',
                    'error_class': 'contact_channel_unavailable',
                    'reason': '目标联系方式已失效或未授权',
                }
            if (
                not isinstance(target, dict)
                or channel_row.encryption_key_version != target.get('encryption_key_version')
                or channel_row.hash_key_version != target.get('hash_key_version')
            ):
                return {
                    'allowed': False,
                    'action': 'failed',
                    'error_class': 'contact_channel_version_changed',
                    'reason': '目标联系方式版本已变化，请重新审核',
                }
        return {
            'allowed': True,
            'snapshot': snapshot,
            'legacy': False,
        }

    @classmethod
    async def _maybe_trigger_followup(
        cls, db: AsyncSession, *, customer: Customer, owner_hasn_id: str | None
    ) -> dict[str, Any]:
        """J3 即时跟进：客户回复后，若已绑定跟进任务则复用 hasn_task run_now 触发即时跟进。

        run_now 仅置 next_run_at=now，由持有 runtime 的节点本地 tick 拾取（中心不 tick；设计 §14.2）。
        旁路逻辑，不影响入站落库；返回 {'triggered': bool, 'reason': str}：
        - no_followup_task：未绑定跟进任务 → 不触发（兜底：任务自身 interval 节奏照常推进，不丢跟进）
        - debounced：10 分钟窗口内已触发过 → 合并（同客户仅触发一次）
        - task_not_runnable：任务非 scheduled/paused（如待审批/已拒/已完）→ 如实不触发、不占防抖
          （任务可运行后下条回复可即时触发）。
          注：节点本地运行中的任务在云端仍为 scheduled，run_now 正常成功置位，
          运行重叠由节点侧 R1 天然兜底，与本分支无关。
        - task_not_found：followup_task_id 失效 → 如实不触发
        - run_now：成功触发，记录防抖时刻
        """
        task_uuid = customer.followup_task_id
        if not task_uuid:
            return {'triggered': False, 'reason': 'no_followup_task'}
        if not owner_hasn_id:
            return {'triggered': False, 'reason': 'no_owner'}
        now = timezone.now()
        last = customer.last_followup_trigger_at
        if last is not None and (now - last) < timedelta(minutes=_FOLLOWUP_DEBOUNCE_MINUTES):
            return {'triggered': False, 'reason': 'debounced'}
        try:
            await agent_task_service.run_now(db, owner_id=owner_hasn_id, task_uuid=task_uuid)
        except errors.NotFoundError:
            return {'triggered': False, 'reason': 'task_not_found'}
        except errors.RequestError:
            # 任务非 scheduled/paused（待审批/已拒/已完等）：如实不触发，不占防抖，任务可运行后下条回复可即时触发
            return {'triggered': False, 'reason': 'task_not_runnable'}
        customer.last_followup_trigger_at = now
        return {'triggered': True, 'reason': 'run_now'}

    @classmethod
    async def record_inbound_reply(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        channel: str,
        content: str,
        agent_id: str | None = None,
        provider_event_id: str | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """记录客户回复（inbound）：落 outreach_message + activity(reply) + 客户态置 engaged 清零静默。

        触发跟进任务 run_now 即时跟进（J3）属 M6 worker，本服务只落事实。
        """
        customer = await GrowthFunnelService._load_customer(
            db,
            user_id=user_id,
            customer_id=customer_id,
            scope=scope,
        )
        normalized_channel = _normalize_outreach_channel(channel)
        dedupe_key = (
            _stable_key(user_id, 'inbound-reply', provider_event_id)
            if provider_event_id
            else _stable_key(
                user_id,
                'inbound-reply',
                customer_id,
                normalized_channel,
                content,
                uuid4(),
            )
        )
        existing = (
            await db.execute(
                sa.select(OutreachMessage).where(
                    OutreachMessage.user_id == user_id,
                    OutreachMessage.dedupe_key == dedupe_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            result = _outreach_to_dict(existing)
            result['followup_trigger'] = {
                'triggered': False,
                'reason': 'duplicate_event',
            }
            return result
        safe_content = str(redact_pii_value(content))
        now = timezone.now()
        message = OutreachMessage(
            customer_id=customer_id,
            user_id=user_id,
            growth_project_id=customer.growth_project_id,
            agent_id=agent_id,
            direction='inbound',
            channel=normalized_channel,
            content=safe_content,
            content_assets={},
            status='replied',
            auto_approved=False,
            approval_status='draft',
            delivery_status='not_queued',
            approval_version=None,
            content_version=1,
            replied_at=now,
            compliance_check={},
            dedupe_key=dedupe_key,
            owner_scope=customer.owner_scope,
            enterprise_id=customer.enterprise_id,
            assignee=customer.assignee,
        )
        db.add(message)
        # 客户态：有回应 + 清零静默轮次。
        customer.lifecycle_status = 'engaged'
        customer.silent_round_count = 0
        customer.last_activity_at = now
        await db.flush()
        await cls._append_event(
            db,
            message=message,
            event_type='replied',
            idempotency_key=(f'provider:{provider_event_id}' if provider_event_id else f'reply:{message.id}'),
            actor_kind='provider',
            actor_id=normalized_channel,
            metadata={'channel': normalized_channel},
        )
        activity = await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer_id,
            kind='reply',
            content=safe_content[:500],
            actor_kind='owner',  # 客户侧（非分身），用 owner 占位区别于 agent 主动
            actor_id=None,
            ref_table='outreach_message',
            ref_id=str(message.id),
        )
        await growth_attribution_service.record_outreach(
            db,
            message=message,
            event_type='replied',
            event_key=f'provider:{provider_event_id}' if provider_event_id else f'reply:{message.id}',
            channel=normalized_channel,
            metadata={'activity_id': activity.id},
        )
        # M6 通知卡片：客户回复 → 提醒主人（J3 即时跟进的人侧提醒）。owner hasn_id 由 user_id 解析。
        owner_human = await identity.get_human_by_user_id(db, user_id=user_id)
        owner_hasn_id = owner_human.hasn_id if owner_human else None
        if owner_hasn_id:
            await growth_notification_service.inbound_reply_received(
                db,
                owner_hasn_id=owner_hasn_id,
                customer_id=customer_id,
                channel=normalized_channel,
            )
        # J3：即时跟进 run_now（防抖 10min；复用任务同步接缝下行，无跟进任务则 interval 兜底）。
        trigger = await cls._maybe_trigger_followup(db, customer=customer, owner_hasn_id=owner_hasn_id)
        await db.flush()
        result = _outreach_to_dict(message)
        result['followup_trigger'] = trigger
        return result

    @staticmethod
    async def list_pending_approvals(
        db: AsyncSession,
        *,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
    ) -> list[dict[str, Any]]:
        # 「我名下待批队列」：enterprise 下恒按 assignee=自己（经理不代审，团队审批态总览另走 team_approval_overview）。
        stmt = apply_scope(sa.select(OutreachMessage), OutreachMessage, user_id=user_id, scope=_approval_scope(scope))
        if growth_project_id is not None:
            try:
                project_id = UUID(str(growth_project_id))
            except ValueError as exc:
                raise errors.RequestError(msg='获客项目 ID 无效') from exc
            stmt = stmt.where(OutreachMessage.growth_project_id == project_id)
        rows = (
            (
                await db.execute(
                    stmt
                    .where(
                        sa.or_(
                            OutreachMessage.approval_status == 'pending_approval',
                            sa.and_(
                                OutreachMessage.approval_status.is_(None),
                                OutreachMessage.status == 'pending_approval',
                            ),
                        )
                    )
                    .order_by(OutreachMessage.created_time.asc(), OutreachMessage.id.asc())
                    .limit(min(limit, 200))
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        customer_ids = sorted({row.customer_id for row in rows})
        customers = {
            customer.id: customer
            for customer in (
                (await db.execute(sa.select(Customer).where(Customer.id.in_(customer_ids)))).scalars().all()
                if customer_ids
                else []
            )
        }
        events = await GrowthOutreachService._event_views(
            db,
            message_ids=[row.id for row in rows],
        )
        result: list[dict[str, Any]] = []
        for message in rows:
            view = _outreach_to_dict(message)
            customer = customers.get(message.customer_id)
            view['target_customer'] = (
                mask_contact_fields(
                    {
                        'id': customer.id,
                        'customer_no': customer.customer_no,
                        'company_name': customer.company_name,
                        'contact_name': customer.contact_name,
                        'email': customer.email,
                        'phone': customer.phone,
                        'wechat': customer.wechat,
                    },
                    reveal=False,
                )
                if customer is not None
                else None
            )
            view['events'] = events.get(message.id, [])
            result.append(view)
        return result

    @staticmethod
    async def team_approval_overview(
        db: AsyncSession,
        *,
        user_id: int,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
    ) -> list[dict[str, Any]]:
        """经理「团队审批态」只读总览（GE5.2）：按 assignee 聚合待批数 + 最早等待时长。写仍归 assignee 主人。

        仅企业经理可见有意义数据；个人/销售/无 scope 返回空（前端不渲染该 tab）。
        """
        if scope is None or not scope.is_enterprise or not scope.is_manager:
            return []
        conditions = [
            OutreachMessage.owner_scope == 'enterprise',
            OutreachMessage.enterprise_id == scope.enterprise_id,
            OutreachMessage.approval_status == 'pending_approval',
        ]
        if growth_project_id is not None:
            try:
                conditions.append(OutreachMessage.growth_project_id == UUID(str(growth_project_id)))
            except ValueError as exc:
                raise errors.RequestError(msg='获客项目 ID 无效') from exc
        rows = (
            await db.execute(
                sa
                .select(
                    OutreachMessage.assignee,
                    sa.func.count(),
                    sa.func.min(OutreachMessage.created_time),
                )
                .where(*conditions)
                .group_by(OutreachMessage.assignee)
            )
        ).all()
        return [
            {'assignee': assignee, 'pending_count': int(cnt), 'earliest_waiting_at': earliest}
            for assignee, cnt, earliest in rows
        ]

    @staticmethod
    async def list_customer_outreach(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        limit: int = 50,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
    ) -> list[dict[str, Any]]:
        # 先按 scope 门控客户可见性，通过后按 customer_id 取触达历史（已门控）。
        await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
        conditions = [OutreachMessage.customer_id == customer_id]
        if growth_project_id is not None:
            try:
                conditions.append(OutreachMessage.growth_project_id == UUID(str(growth_project_id)))
            except ValueError as exc:
                raise errors.RequestError(msg='获客项目 ID 无效') from exc
        rows = (
            (
                await db.execute(
                    sa
                    .select(OutreachMessage)
                    .where(*conditions)
                    .order_by(OutreachMessage.created_time.desc(), OutreachMessage.id.desc())
                    .limit(min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        events = await GrowthOutreachService._event_views(
            db,
            message_ids=[row.id for row in rows],
        )
        result = []
        for message in rows:
            view = _outreach_to_dict(message)
            view['events'] = events.get(message.id, [])
            result.append(view)
        return result

    # ---------- 退订登记 ----------

    @staticmethod
    async def register_optout(
        db: AsyncSession,
        *,
        user_id: int,
        keyring: GrowthPiiKeyring | None = None,
        channel: str = 'all',
        address: str | None = None,
        address_hash: str | None = None,
        customer_id: int | None = None,
        reason: str | None = None,
        source: str | None = None,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """按切流开关登记退订；启用 PII 新写后只写当前 HMAC 版本。"""
        if address_hash is not None:
            raise errors.RequestError(
                msg='旧 SHA256 退订仅支持读取兼容',
                data={'error_code': 'GROWTH_OPTOUT_LEGACY_HASH_READ_ONLY'},
            )
        if not address:
            raise errors.RequestError(msg='退订需提供联系方式')
        owner_scope = 'enterprise' if scope and scope.enterprise_id is not None else 'personal'
        if not source:
            raise errors.RequestError(msg='退订必须提供来源')
        safe_reason = redact_pii_value(reason)
        safe_source = redact_pii_value(source)
        if keyring is None and not settings.GROWTH_PII_NEW_WRITE_ENABLED:
            raise errors.ConflictError(
                msg='联系人 PII 新写尚未启用',
                data={'error_code': 'GROWTH_PII_NEW_WRITE_DISABLED'},
            )

        active_keyring = keyring or require_growth_pii_keyring()
        semantic_exists = await contact_privacy_service.is_opted_out(
            db,
            keyring=active_keyring,
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=scope.enterprise_id if scope else None,
            channel=channel,
            address=address,
        )
        rec, row_created = await contact_privacy_service.register_optout(
            db,
            keyring=active_keyring,
            owner_scope=owner_scope,
            user_id=user_id,
            enterprise_id=scope.enterprise_id if scope else None,
            channel=channel,
            address=address,
            reason=safe_reason,
            source=safe_source,
            customer_id=customer_id,
        )
        created = not semantic_exists and row_created
        if customer_id is not None and created:
            await GrowthFunnelService._add_activity(
                db,
                user_id=user_id,
                customer_id=customer_id,
                kind='note',
                content=f'客户退订（{channel}）：{safe_reason or "未注明原因"}',
                actor_kind='owner',
                actor_id=None,
            )
        return {'id': rec.id, 'channel': rec.channel, 'created': created}


growth_outreach_service = GrowthOutreachService()
