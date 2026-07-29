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
from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.optout_record import OptoutRecord
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.contact_privacy_service import contact_privacy_service
from backend.app.hasn_growth.service.funnel_service import GrowthFunnelService
from backend.app.hasn_growth.service.growth_notification import growth_notification_service
from backend.app.hasn_growth.service.pii import redact_pii_value
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


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


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
        'growth_project_id': (
            str(m.growth_project_id) if m.growth_project_id is not None else None
        ),
        'customer_id': m.customer_id,
        'opportunity_id': m.opportunity_id,
        'agent_id': m.agent_id,
        'direction': m.direction,
        'channel': m.channel,
        'subject': m.subject,
        'content': m.content,
        'content_assets': m.content_assets,
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
                legacy_addresses = tuple(
                    address
                    for address in (legacy_contact.email, legacy_contact.phone)
                    if address
                )
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
    async def _freq_exceeded(db: AsyncSession, *, user_id: int, customer_id: int, channel: str) -> bool:
        since = timezone.now() - timedelta(days=_FREQ_WINDOW_DAYS)
        count = (
            await db.execute(
                sa
                .select(sa.func.count())
                .select_from(OutreachMessage)
                .where(
                    OutreachMessage.user_id == user_id,
                    OutreachMessage.customer_id == customer_id,
                    OutreachMessage.channel == channel,
                    OutreachMessage.direction == 'outbound',
                    OutreachMessage.status.in_(_QUOTA_STATUSES),
                    OutreachMessage.created_time >= since,
                )
            )
        ).scalar_one()
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

        if await cls._freq_exceeded(db, user_id=user_id, customer_id=customer.id, channel=channel):
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
        db: AsyncSession, *, user_id: int, message_id: int, scope: GrowthScope | None = None
    ) -> OutreachMessage:
        # 审批/取材按 scope 门控：enterprise 下审批永远归 assignee 的主人维度（apply_scope 企业分支恒带
        # assignee 条件——见下方 list_pending_approvals 说明），经理不代审他人名下触达（GE5.2 铁律）。
        stmt = apply_scope(
            sa.select(OutreachMessage).where(OutreachMessage.id == message_id),
            OutreachMessage,
            user_id=user_id,
            scope=_approval_scope(scope),
        )
        m = (await db.execute(stmt)).scalar_one_or_none()
        if not m:
            raise errors.NotFoundError(msg='触达消息不存在或无权访问')
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
    ) -> dict[str, Any]:
        """分身发起触达：合规检查 → 落 outreach_message（状态机定 status）→ 记 activity。触达继承客户归属。

        whitelist_auto_send：主人对「客户×渠道」开了自动放行（M6/owner 设置驱动，本期由调用方传入）。
        首触达永不豁免（G4）。返回 outreach 字典（含 status）；被拦截也落库返回（分身学习）。
        """
        normalized_channel = _normalize_outreach_channel(channel)
        _assert_outreach_payload_safe({
            'subject': subject,
            'content': content,
            'intent_note': intent_note,
            'content_assets': content_assets,
        })
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)

        needs_keyring = (
            keyring is not None or settings.GROWTH_PII_NEW_WRITE_ENABLED or settings.GROWTH_PII_SHADOW_READ_ENABLED
        )
        active_keyring = keyring or (require_growth_pii_keyring() if needs_keyring else None)
        compliance = await cls.check_compliance(
            db,
            keyring=active_keyring,
            use_private_channels=settings.GROWTH_PII_SHADOW_READ_ENABLED or keyring is not None,
            user_id=user_id,
            customer=customer,
            channel=normalized_channel,
            content=content,
        )

        # 幂等去重：同 owner+客户+渠道+正文 视为同一条（防分身重复发）。
        dedupe_key = hashlib.sha256(
            f'{user_id}|{customer_id}|{normalized_channel}|{content}'.encode()
        ).hexdigest()[:64]
        existing = (
            await db.execute(
                sa.select(OutreachMessage).where(
                    OutreachMessage.user_id == user_id, OutreachMessage.dedupe_key == dedupe_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _outreach_to_dict(existing)

        if compliance['blocked']:
            status = compliance['block_status']
            auto_approved = False
        else:
            is_first = await cls._is_first_contact(
                db,
                user_id=user_id,
                customer_id=customer_id,
                channel=normalized_channel,
            )
            replied = await cls._customer_has_replied(db, user_id=user_id, customer_id=customer_id)
            if not is_first and whitelist_auto_send and replied:
                status = 'approved'
                auto_approved = True
            else:
                # 首触达必审；白名单未命中也必审。
                status = 'pending_approval'
                auto_approved = False

        now = timezone.now()
        message = OutreachMessage(
            customer_id=customer_id,
            opportunity_id=opportunity_id,
            user_id=user_id,
            growth_project_id=customer.growth_project_id,
            agent_id=agent_id,
            direction='outbound',
            channel=normalized_channel,
            subject=subject,
            content=content,
            content_assets=content_assets or {},
            status=status,
            intent_note=intent_note,
            auto_approved=auto_approved,
            approved_at=now if status == 'approved' else None,
            task_run_id=task_run_id,
            workflow_run_id=workflow_run_id,
            compliance_check=compliance['checks'],
            dedupe_key=dedupe_key,
            owner_scope=customer.owner_scope,
            enterprise_id=customer.enterprise_id,
            assignee=customer.assignee,
        )
        db.add(message)
        await db.flush()

        # 时间线留痕（被拦截也留，分身下轮学习）。
        if compliance['blocked']:
            note = f'触达被合规拦截（{compliance["reason"]}）'
        elif status == 'approved':
            note = f'触达自动放行（白名单）：{intent_note or content[:40]}'
        else:
            note = f'触达待主人审批：{intent_note or content[:40]}'
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer_id,
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
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """owner 审批通过（可改话术后批，修改进版本留痕）。enterprise 下仅 assignee 主人可批。"""
        m = await cls._load_message(db, user_id=user_id, message_id=message_id, scope=scope)
        if m.status != 'pending_approval':
            raise errors.ForbiddenError(msg=f'仅待审批触达可批准（当前 {m.status}）')
        if edited_content is not None and edited_content != m.content:
            _assert_outreach_payload_safe({'content': edited_content})
            revisions = list((m.content_assets or {}).get('revisions', []))
            revisions.append({'before': redact_pii_value(m.content), 'by': approver_user_id})
            m.content_assets = {**(m.content_assets or {}), 'revisions': revisions}
            m.content = edited_content
        m.status = 'approved'
        m.auto_approved = False
        m.approval_user_id = approver_user_id
        m.approved_at = timezone.now()
        await db.flush()
        return _outreach_to_dict(m)

    @classmethod
    async def reject_outreach(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        message_id: int,
        approver_user_id: int,
        reason: str,
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """owner 拒绝（reason 回流 timeline，分身下轮学习）。enterprise 下仅 assignee 主人可拒。"""
        m = await cls._load_message(db, user_id=user_id, message_id=message_id, scope=scope)
        if m.status != 'pending_approval':
            raise errors.ForbiddenError(msg=f'仅待审批触达可拒绝（当前 {m.status}）')
        safe_reason = redact_pii_value(reason)
        m.status = 'rejected'
        m.reject_reason = safe_reason
        m.approval_user_id = approver_user_id
        await db.flush()
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
        scope: GrowthScope | None = None,
    ) -> dict[str, Any]:
        """manual_assist「复制发送」素材包（设计 §8.2 G4）：只返回 approved 话术和素材链接。

        联系方式必须由 Owner 另行选择一个私有渠道并调用专用 reveal；本接口不构成 PII 读取。
        仅 owner 自己的数据（跨户 → NotFound）；仅 approved 触达可取。enterprise 下仅 assignee 主人。
        """
        m = await cls._load_message(db, user_id=user_id, message_id=message_id, scope=scope)
        if m.status != 'approved':
            raise errors.ForbiddenError(msg=f'仅已批准触达可取复制发送素材包（当前 {m.status}）')
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=m.customer_id, scope=scope)

        return {
            'message_id': m.id,
            'customer_id': m.customer_id,
            'company_name': customer.company_name,
            'lead_contact_id': customer.lead_contact_id,
            'channel': m.channel,
            'content': redact_pii_value(m.content),  # 已批准话术（含主人改稿）
            'content_assets': redact_pii_value(m.content_assets or {}),  # 素材链接（图/文件等）
            'intent_note': redact_pii_value(m.intent_note),
        }

    @classmethod
    async def mark_sending(cls, db: AsyncSession, *, user_id: int, message_id: int) -> dict[str, Any]:
        """发送 worker 接手（approved → sending）。"""
        m = await cls._load_message(db, user_id=user_id, message_id=message_id)
        if m.status != 'approved':
            raise errors.ForbiddenError(msg=f'仅已批准触达可进入发送（当前 {m.status}）')
        m.status = 'sending'
        await db.flush()
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
    ) -> dict[str, Any]:
        """发送成功（manual_assist 主人「已发送」或 worker 成功；approved/sending → sent）。"""
        normalized_channel = (
            _normalize_outreach_channel(channel_actual)
            if channel_actual
            else None
        )
        m = await cls._load_message(db, user_id=user_id, message_id=message_id, scope=scope)
        if m.status not in ('approved', 'sending'):
            raise errors.ForbiddenError(msg=f'仅已批准/发送中触达可标记已发送（当前 {m.status}）')
        m.status = 'sent'
        m.sent_at = timezone.now()
        if normalized_channel:
            m.content_assets = {
                **(m.content_assets or {}),
                'channel_actual': normalized_channel,
            }
        await db.flush()
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
    async def mark_failed(cls, db: AsyncSession, *, user_id: int, message_id: int, error: str) -> dict[str, Any]:
        """发送失败（如实回报错误；sending/approved → failed）。"""
        m = await cls._load_message(db, user_id=user_id, message_id=message_id)
        if m.status not in ('approved', 'sending'):
            raise errors.ForbiddenError(msg=f'仅已批准/发送中触达可标记失败（当前 {m.status}）')
        m.status = 'failed'
        m.error_message = redact_pii_value(error)
        await db.flush()
        return _outreach_to_dict(m)

    @classmethod
    async def _maybe_trigger_followup(
        cls, db: AsyncSession, *, customer: Customer, owner_hasn_id: str | None
    ) -> dict[str, Any]:
        """J3 即时跟进：客户回复后，若已绑定跟进任务则复用 hasn_task run_now 触发即时跟进。

        run_now 仅置 next_run_at=now，由持有 runtime 的节点本地 tick 拾取（中心不 tick；设计 §14.2）。
        旁路逻辑，不影响入站落库；返回 {'triggered': bool, 'reason': str}：
        - no_followup_task：未绑定跟进任务 → 不触发（兜底：任务自身 interval 节奏照常推进，不丢跟进）
        - debounced：10 分钟窗口内已触发过 → 合并（同客户仅触发一次）
        - task_not_runnable：任务非 scheduled/paused（如待审批/已拒/已完）→ 如实不触发、不占防抖（任务可运行后下条回复可即时触发）。
          注：节点本地运行中的任务在云端仍为 scheduled，run_now 正常成功置位，运行重叠由节点侧 R1 天然兜底，与本分支无关。
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
    ) -> dict[str, Any]:
        """记录客户回复（inbound）：落 outreach_message + activity(reply) + 客户态置 engaged 清零静默。

        触发跟进任务 run_now 即时跟进（J3）属 M6 worker，本服务只落事实。
        """
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id)
        normalized_channel = _normalize_outreach_channel(channel)
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
            replied_at=now,
            compliance_check={},
        )
        db.add(message)
        # 客户态：有回应 + 清零静默轮次。
        customer.lifecycle_status = 'engaged'
        customer.silent_round_count = 0
        customer.last_activity_at = now
        await db.flush()
        await GrowthFunnelService._add_activity(
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
        # M6 通知卡片：客户回复 → 提醒主人（J3 即时跟进的人侧提醒）。owner hasn_id 由 user_id 解析。
        owner_hasn_id = (
            await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))
        ).scalar_one_or_none()
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
        db: AsyncSession, *, user_id: int, limit: int = 50, offset: int = 0, scope: GrowthScope | None = None
    ) -> list[dict[str, Any]]:
        # 「我名下待批队列」：enterprise 下恒按 assignee=自己（经理不代审，团队审批态总览另走 team_approval_overview）。
        stmt = apply_scope(sa.select(OutreachMessage), OutreachMessage, user_id=user_id, scope=_approval_scope(scope))
        rows = (
            (
                await db.execute(
                    stmt
                    .where(OutreachMessage.status == 'pending_approval')
                    .order_by(OutreachMessage.created_time.asc(), OutreachMessage.id.asc())
                    .limit(min(limit, 200))
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return [_outreach_to_dict(m) for m in rows]

    @staticmethod
    async def team_approval_overview(
        db: AsyncSession, *, user_id: int, scope: GrowthScope | None = None
    ) -> list[dict[str, Any]]:
        """经理「团队审批态」只读总览（GE5.2）：按 assignee 聚合待批数 + 最早等待时长。写仍归 assignee 主人。

        仅企业经理可见有意义数据；个人/销售/无 scope 返回空（前端不渲染该 tab）。
        """
        if scope is None or not scope.is_enterprise or not scope.is_manager:
            return []
        rows = (
            await db.execute(
                sa
                .select(
                    OutreachMessage.assignee,
                    sa.func.count(),
                    sa.func.min(OutreachMessage.created_time),
                )
                .where(
                    OutreachMessage.owner_scope == 'enterprise',
                    OutreachMessage.enterprise_id == scope.enterprise_id,
                    OutreachMessage.status == 'pending_approval',
                )
                .group_by(OutreachMessage.assignee)
            )
        ).all()
        return [
            {'assignee': assignee, 'pending_count': int(cnt), 'earliest_waiting_at': earliest}
            for assignee, cnt, earliest in rows
        ]

    @staticmethod
    async def list_customer_outreach(
        db: AsyncSession, *, user_id: int, customer_id: int, limit: int = 50, scope: GrowthScope | None = None
    ) -> list[dict[str, Any]]:
        # 先按 scope 门控客户可见性，通过后按 customer_id 取触达历史（已门控）。
        await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
        rows = (
            (
                await db.execute(
                    sa
                    .select(OutreachMessage)
                    .where(OutreachMessage.customer_id == customer_id)
                    .order_by(OutreachMessage.created_time.desc(), OutreachMessage.id.desc())
                    .limit(min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        return [_outreach_to_dict(m) for m in rows]

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
