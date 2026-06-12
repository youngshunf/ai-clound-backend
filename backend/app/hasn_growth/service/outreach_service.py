"""获客触达状态机 + 营销合规服务（设计 07 §8.2 G4 + §10.3）。

触达审批状态机（业务态，**不走 ask_gate**，对齐任务系统 D4）：
    agent outreach.send → 服务端合规检查 → 首触达必 pending_approval（不可豁免）/
    白名单（主人开自动放行 且 客户曾回复）→ approved(auto_approved=true) / 其余 → pending_approval。
    owner approve / edit-then-approve / reject(reason) → 发送 worker（M6）接手。

合规硬闸（§10.3）：optout 命中（无豁免）/ 广告法极限词 / 同客户同渠道频控（≤2/周）→ 拦截落库
（blocked_optout / blocked_compliance），分身下轮经 timeline 学习。quiet hours / 微信个人号开关
属发送 worker（M6）排队与渠道策略，本服务记录于 compliance_check 供下游裁决。

PII 边界（§10.2）：本服务只处理话术正文（不含明文联系方式）；optout 命中判定在服务端用 customer
明文联系方式哈希比对，明文不出参、不进 LLM。
"""

from __future__ import annotations

import hashlib
import re

from datetime import timedelta
from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.optout_record import OptoutRecord
from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.funnel_service import GrowthFunnelService
from backend.app.hasn_growth.service.growth_notification import growth_notification_service
from backend.common.exception import errors
from backend.utils.timezone import timezone

# 广告法极限词（节选，playbook 可收紧不可放宽）——命中 → blocked_compliance 回分身改写。
_BANNED_SUPERLATIVES = (
    '国家级', '最高级', '最佳', '第一品牌', '全网第一', '全国第一', '世界第一',
    '100%', '百分之百', '绝对', '顶级', '独一无二', '唯一', '最大', '最好', '最低价',
    '史上最', '全球领先', '永久', '万能', '彻底根治', '稳赚',
)

# 同客户同渠道频控：默认 ≤2 条/周（§10.3）。
_FREQ_WINDOW_DAYS = 7
_FREQ_MAX_PER_WINDOW = 2

# 频控计数纳入的「占用配额」状态（已发/在途/待审/已批都算占用，拒绝/拦截/失败不算）。
_QUOTA_STATUSES = ('pending_approval', 'approved', 'sending', 'sent', 'replied')

# quiet hours 默认窗口 [09:00, 21:00)（§10.3，客户时区缺省随主人时区，此处用服务端时区近似）。
_QUIET_START_HOUR = 9
_QUIET_END_HOUR = 21


def _norm_address(value: str | None) -> str | None:
    """联系方式归一：邮箱小写去空格；电话留数字。其余原样 trim。"""
    if not value:
        return None
    v = value.strip()
    if '@' in v:
        return v.lower()
    digits = re.sub(r'\D', '', v)
    return digits or v.lower()


def _address_hash(value: str | None) -> str | None:
    norm = _norm_address(value)
    if not norm:
        return None
    return hashlib.sha256(norm.encode('utf-8')).hexdigest()


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


def _outreach_to_dict(m: OutreachMessage) -> dict[str, Any]:
    return {
        'id': m.id,
        'customer_id': m.customer_id,
        'opportunity_id': m.opportunity_id,
        'agent_id': m.agent_id,
        'direction': m.direction,
        'channel': m.channel,
        'subject': m.subject,
        'content': m.content,
        'content_assets': m.content_assets,
        'status': m.status,
        'intent_note': m.intent_note,
        'auto_approved': m.auto_approved,
        'approval_user_id': m.approval_user_id,
        'approved_at': m.approved_at,
        'reject_reason': m.reject_reason,
        'sent_at': m.sent_at,
        'replied_at': m.replied_at,
        'error_message': m.error_message,
        'compliance_check': m.compliance_check,
        'task_run_id': m.task_run_id,
        'workflow_run_id': m.workflow_run_id,
        'created_time': m.created_time,
    }


class GrowthOutreachService:
    """触达状态机 + 合规闸门，全 user_id 隔离，跨户 → NotFound。"""

    # ---------- 合规 ----------

    @staticmethod
    async def _customer_optout_hit(
        db: AsyncSession, *, user_id: int, customer: Customer, channel: str
    ) -> bool:
        """客户任一联系方式在该渠道（或 all）登记退订 → 命中（硬闸，无豁免）。"""
        hashes = [h for h in (_address_hash(customer.email), _address_hash(customer.phone), _address_hash(customer.wechat)) if h]
        if not hashes:
            return False
        hit = (
            await db.execute(
                sa.select(OptoutRecord.id)
                .where(
                    OptoutRecord.user_id == user_id,
                    OptoutRecord.address_hash.in_(hashes),
                    OptoutRecord.channel.in_([channel, 'all']),
                )
                .limit(1)
            )
        ).first()
        return hit is not None

    @staticmethod
    def _banned_words(content: str) -> list[str]:
        return [w for w in _BANNED_SUPERLATIVES if w in (content or '')]

    @staticmethod
    async def _freq_exceeded(db: AsyncSession, *, user_id: int, customer_id: int, channel: str) -> bool:
        since = timezone.now() - timedelta(days=_FREQ_WINDOW_DAYS)
        count = (
            await db.execute(
                sa.select(sa.func.count())
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
        cls, db: AsyncSession, *, user_id: int, customer: Customer, channel: str, content: str
    ) -> dict[str, Any]:
        """返回 {blocked, block_status, reason, checks}。block_status ∈ blocked_optout/blocked_compliance。"""
        checks: dict[str, Any] = {'quiet_hours_ok': cls._quiet_hours_ok()}

        if await cls._customer_optout_hit(db, user_id=user_id, customer=customer, channel=channel):
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
    async def _load_message(db: AsyncSession, *, user_id: int, message_id: int) -> OutreachMessage:
        m = (
            await db.execute(
                sa.select(OutreachMessage).where(
                    OutreachMessage.id == message_id, OutreachMessage.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if not m:
            raise errors.NotFoundError(msg='触达消息不存在或无权访问')
        return m

    @staticmethod
    async def _customer_has_replied(db: AsyncSession, *, user_id: int, customer_id: int) -> bool:
        hit = (
            await db.execute(
                sa.select(OutreachMessage.id)
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
                sa.select(OutreachMessage.id)
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
    ) -> dict[str, Any]:
        """分身发起触达：合规检查 → 落 outreach_message（状态机定 status）→ 记 activity。

        whitelist_auto_send：主人对「客户×渠道」开了自动放行（M6/owner 设置驱动，本期由调用方传入）。
        首触达永不豁免（G4）。返回 outreach 字典（含 status）；被拦截也落库返回（分身学习）。
        """
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id)

        compliance = await cls.check_compliance(
            db, user_id=user_id, customer=customer, channel=channel, content=content
        )

        # 幂等去重：同 owner+客户+渠道+正文 视为同一条（防分身重复发）。
        dedupe_key = hashlib.sha256(f'{user_id}|{customer_id}|{channel}|{content}'.encode('utf-8')).hexdigest()[:64]
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
            is_first = await cls._is_first_contact(db, user_id=user_id, customer_id=customer_id, channel=channel)
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
            agent_id=agent_id,
            direction='outbound',
            channel=channel,
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
    ) -> dict[str, Any]:
        """owner 审批通过（可改话术后批，修改进版本留痕）。"""
        m = await cls._load_message(db, user_id=user_id, message_id=message_id)
        if m.status != 'pending_approval':
            raise errors.ForbiddenError(msg=f'仅待审批触达可批准（当前 {m.status}）')
        if edited_content is not None and edited_content != m.content:
            revisions = list((m.content_assets or {}).get('revisions', []))
            revisions.append({'before': m.content, 'by': approver_user_id})
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
        cls, db: AsyncSession, *, user_id: int, message_id: int, approver_user_id: int, reason: str
    ) -> dict[str, Any]:
        """owner 拒绝（reason 回流 timeline，分身下轮学习）。"""
        m = await cls._load_message(db, user_id=user_id, message_id=message_id)
        if m.status != 'pending_approval':
            raise errors.ForbiddenError(msg=f'仅待审批触达可拒绝（当前 {m.status}）')
        m.status = 'rejected'
        m.reject_reason = reason
        m.approval_user_id = approver_user_id
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=m.customer_id,
            kind='note',
            content=f'主人拒绝触达：{reason}',
            actor_kind='owner',
            actor_id=str(approver_user_id),
            ref_table='outreach_message',
            ref_id=str(m.id),
        )
        return _outreach_to_dict(m)

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
        cls, db: AsyncSession, *, user_id: int, message_id: int, channel_actual: str | None = None
    ) -> dict[str, Any]:
        """发送成功（manual_assist 主人「已发送」或 worker 成功；approved/sending → sent）。"""
        m = await cls._load_message(db, user_id=user_id, message_id=message_id)
        if m.status not in ('approved', 'sending'):
            raise errors.ForbiddenError(msg=f'仅已批准/发送中触达可标记已发送（当前 {m.status}）')
        m.status = 'sent'
        m.sent_at = timezone.now()
        if channel_actual:
            m.content_assets = {**(m.content_assets or {}), 'channel_actual': channel_actual}
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=m.customer_id,
            kind='outreach',
            content=f'已通过 {channel_actual or m.channel} 发送',
            actor_kind='agent',
            actor_id=m.agent_id,
            ref_table='outreach_message',
            ref_id=str(m.id),
        )
        return _outreach_to_dict(m)

    @classmethod
    async def mark_failed(
        cls, db: AsyncSession, *, user_id: int, message_id: int, error: str
    ) -> dict[str, Any]:
        """发送失败（如实回报错误；sending/approved → failed）。"""
        m = await cls._load_message(db, user_id=user_id, message_id=message_id)
        if m.status not in ('approved', 'sending'):
            raise errors.ForbiddenError(msg=f'仅已批准/发送中触达可标记失败（当前 {m.status}）')
        m.status = 'failed'
        m.error_message = error
        await db.flush()
        return _outreach_to_dict(m)

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
        now = timezone.now()
        message = OutreachMessage(
            customer_id=customer_id,
            user_id=user_id,
            agent_id=agent_id,
            direction='inbound',
            channel=channel,
            content=content,
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
            content=content[:500],
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
                customer_name=customer.contact_name or customer.company_name,
                channel=channel,
                excerpt=content[:120],
            )
        return _outreach_to_dict(message)

    @staticmethod
    async def list_pending_approvals(
        db: AsyncSession, *, user_id: int, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                sa.select(OutreachMessage)
                .where(OutreachMessage.user_id == user_id, OutreachMessage.status == 'pending_approval')
                .order_by(OutreachMessage.created_time.asc(), OutreachMessage.id.asc())
                .limit(min(limit, 200))
                .offset(offset)
            )
        ).scalars().all()
        return [_outreach_to_dict(m) for m in rows]

    @staticmethod
    async def list_customer_outreach(
        db: AsyncSession, *, user_id: int, customer_id: int, limit: int = 50
    ) -> list[dict[str, Any]]:
        await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id)
        rows = (
            await db.execute(
                sa.select(OutreachMessage)
                .where(OutreachMessage.user_id == user_id, OutreachMessage.customer_id == customer_id)
                .order_by(OutreachMessage.created_time.desc(), OutreachMessage.id.desc())
                .limit(min(limit, 200))
            )
        ).scalars().all()
        return [_outreach_to_dict(m) for m in rows]

    # ---------- 退订登记 ----------

    @staticmethod
    async def register_optout(
        db: AsyncSession,
        *,
        user_id: int,
        channel: str = 'all',
        address: str | None = None,
        address_hash: str | None = None,
        customer_id: int | None = None,
        reason: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """登记退订（UNIQUE(user_id, channel, address_hash) 幂等）。address 明文仅用于算 hash，不落库。"""
        ahash = address_hash or _address_hash(address)
        if not ahash:
            raise errors.RequestError(msg='退订需提供联系方式或其哈希')
        existing = (
            await db.execute(
                sa.select(OptoutRecord).where(
                    OptoutRecord.user_id == user_id,
                    OptoutRecord.channel == channel,
                    OptoutRecord.address_hash == ahash,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {'id': existing.id, 'channel': existing.channel, 'created': False}
        rec = OptoutRecord(
            user_id=user_id,
            channel=channel,
            address_hash=ahash,
            customer_id=customer_id,
            reason=reason,
            source=source,
        )
        db.add(rec)
        await db.flush()
        if customer_id is not None:
            await GrowthFunnelService._add_activity(
                db,
                user_id=user_id,
                customer_id=customer_id,
                kind='note',
                content=f'客户退订（{channel}）：{reason or "未注明原因"}',
                actor_kind='owner',
                actor_id=None,
            )
        return {'id': rec.id, 'channel': rec.channel, 'created': True}


growth_outreach_service = GrowthOutreachService()
