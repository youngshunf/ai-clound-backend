"""M6 发送 worker：扫 approved 触达自动分发（设计 §8.3 / 实施 91 M6）。

闸门（全部硬约束，零 fake；任一不通过则该条保持 approved，待下个 tick 或主人手动处理）：
- **manual_assist**：owner 手动复制发送（`build_send_material` → 标记已发送），worker 不接管 → 跳过。
- **quiet hours**：服务端时窗 [09:00, 21:00) 外挂起（保持 approved，下个 tick 再扫），不在静默时段打扰客户。
- **微信 J1**：owner `channel_setting.wechat_auto_send_confirmed=true`（M7 UI 开关 + 二次确认）才允许进 worker，
  否则跳过（恒走 manual_assist，主人手动发）。缺省（无设置行）= 未确认 = 安全默认关。
- **渠道发送**：经渠道 sender 真实发送；当前云端无可用 transport（飞书等 IM 经分身绑定的 Hermes runtime 渠道
  gateway 发送，属下行接缝，M0 对齐 / M8 物化）→ `channel_unavailable` → mark_failed + 建议回退 manual_assist
  （**建议而非静默切换**，主人见 failed + 建议后改用复制发送）。

worker 不感知节点本地运行态；运行重叠由节点侧机制兜底。本模块只做云端编排与状态回写，零 fake：
无 transport 即如实 channel_unavailable，绝不假装已发送。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.outreach_service import (
    growth_outreach_service,
)
from backend.utils.timezone import timezone

# owner 手动复制发送渠道：worker 不接管（owner 驱动）。
_MANUAL_CHANNEL = 'manual_assist'

# 渠道 sender 注册表：channel -> async (db, 冻结包) -> 真实 provider 结果。
# 默认空——真实 IM/邮件 transport（飞书 gateway 等下行接缝）在 M8 物化时注册；未注册渠道一律 channel_unavailable。
ChannelSenderFn = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any]]]
_CHANNEL_SENDERS: dict[str, ChannelSenderFn] = {}


def register_channel_sender(channel: str, sender: ChannelSenderFn) -> None:
    """注册某渠道的真实发送通道（M8 物化时由 IM/邮件适配器调用）。"""
    _CHANNEL_SENDERS[channel] = sender


async def _wechat_auto_send_confirmed(db: AsyncSession, user_id: int) -> bool:
    """owner 是否已确认微信自动发送（J1 闸门）。无设置行 = 未确认（安全默认关）。"""
    row = (
        await db.execute(
            text('SELECT wechat_auto_send_confirmed FROM hasn_growth.channel_setting WHERE user_id = :u'),
            {'u': user_id},
        )
    ).scalar_one_or_none()
    return bool(row)


async def set_wechat_auto_send(db: AsyncSession, *, user_id: int, confirmed: bool) -> None:
    """落 owner 微信自动发送确认（M7 UI 二次确认开关写入；upsert 幂等）。"""
    await db.execute(
        text(
            'INSERT INTO hasn_growth.channel_setting (user_id, wechat_auto_send_confirmed) VALUES (:u, :c) '
            'ON CONFLICT (user_id) DO UPDATE SET wechat_auto_send_confirmed = EXCLUDED.wechat_auto_send_confirmed, '
            'updated_time = now()'
        ),
        {'u': user_id, 'c': confirmed},
    )


async def get_channel_setting(db: AsyncSession, *, user_id: int) -> dict[str, Any]:
    """owner 渠道设置（缺省=安全默认：微信自动发送关）。M7 渠道开关页读取。"""
    return {'wechat_auto_send_confirmed': await _wechat_auto_send_confirmed(db, user_id)}


async def _attempt_send(
    db: AsyncSession,
    message: OutreachMessage,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """尝试经渠道 sender 真实发送。无可用 transport → channel_unavailable（零 fake，不假装已发送）。"""
    sender = _CHANNEL_SENDERS.get(message.channel)
    if sender is None:
        return {
            'ok': False,
            'error': (
                f'渠道 {message.channel} 暂无可用发送通道（channel_unavailable）；'
                '建议回退 manual_assist 由主人手动复制发送'
            ),
        }
    return await sender(
        db,
        {
            'message_id': message.id,
            'growth_project_id': str(message.growth_project_id),
            'customer_id': message.customer_id,
            'channel': message.channel,
            'approval_version': message.approval_version,
            'content_version': message.content_version,
            'idempotency_key': (f'outreach:{message.id}:approval:{message.approval_version}'),
            'snapshot': snapshot,
        },
    )


class GrowthDispatchService:
    """获客发送 worker：扫 approved → 按渠道闸门分发 → 状态回写（sent/failed）。"""

    @staticmethod
    async def dispatch_approved_batch(  # noqa: C901
        db: AsyncSession, *, limit: int = 50, now_hour: int | None = None
    ) -> dict[str, int]:
        """扫一批 approved 出站触达并分发。返回统计（旁路，可观测）。

        now_hour：测试可注入小时数覆盖 quiet hours 判定；缺省取服务端当前小时。
        """
        hour = now_hour if now_hour is not None else timezone.now().hour

        messages = (
            (
                await db.execute(
                    sa
                    .select(OutreachMessage)
                    .where(
                        OutreachMessage.direction == 'outbound',
                        sa.or_(
                            OutreachMessage.approval_status == 'approved',
                            sa.and_(
                                OutreachMessage.approval_status.is_(None),
                                OutreachMessage.status == 'approved',
                            ),
                        ),
                        sa.or_(
                            OutreachMessage.delivery_status.in_(('not_queued', 'queued')),
                            OutreachMessage.delivery_status.is_(None),
                        ),
                    )
                    .order_by(OutreachMessage.id)
                    .limit(max(1, min(limit, 200)))
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )

        stat = {
            'scanned': 0,
            'sent': 0,
            'failed': 0,
            'skipped_manual': 0,
            'queued_quiet_hours': 0,
            'wechat_unconfirmed': 0,
            'blocked_project': 0,
            'blocked_entitlement': 0,
            'blocked_budget': 0,
            'blocked_optout': 0,
            'blocked_compliance': 0,
            'invalid_target': 0,
            'retry_scheduled': 0,
        }
        for m in messages:
            stat['scanned'] += 1
            if m.channel == _MANUAL_CHANNEL:
                stat['skipped_manual'] += 1
                continue
            if m.channel == 'wechat' and not await _wechat_auto_send_confirmed(db, m.user_id):
                stat['wechat_unconfirmed'] += 1  # J1 未确认 → 恒 manual_assist，主人手动发
                continue

            preflight = await growth_outreach_service.dispatch_preflight(
                db,
                message=m,
                now_hour=hour,
            )
            if not preflight['allowed']:
                action = str(preflight['action'])
                error_class = str(preflight['error_class'])
                reason = str(preflight['reason'])
                event_key = f'preflight:{error_class}:v{m.approval_version}'
                if action == 'retry_scheduled':
                    await growth_outreach_service.schedule_retry(
                        db,
                        user_id=m.user_id,
                        message_id=m.id,
                        reason=reason,
                        idempotency_key=event_key,
                    )
                    stat['queued_quiet_hours'] += 1
                    stat['retry_scheduled'] += 1
                elif action in {'blocked_optout', 'blocked_compliance'}:
                    await growth_outreach_service.mark_blocked(
                        db,
                        user_id=m.user_id,
                        message_id=m.id,
                        delivery_status=action,
                        error_class=error_class,
                        reason=reason,
                        idempotency_key=event_key,
                    )
                    if error_class == 'project_not_active':
                        stat['blocked_project'] += 1
                    elif error_class == 'entitlement_required':
                        stat['blocked_entitlement'] += 1
                    elif error_class == 'monthly_budget_exhausted':
                        stat['blocked_budget'] += 1
                    elif action == 'blocked_optout':
                        stat['blocked_optout'] += 1
                    else:
                        stat['blocked_compliance'] += 1
                else:
                    await growth_outreach_service.mark_failed(
                        db,
                        user_id=m.user_id,
                        message_id=m.id,
                        error=reason,
                        error_class=error_class,
                        idempotency_key=event_key,
                    )
                    stat['invalid_target'] += 1
                    stat['failed'] += 1
                continue

            # 认领（approved → sending）后尝试真实发送，结果如实回写。
            dispatch_key = f'dispatch:v{m.approval_version}'
            await growth_outreach_service.mark_sending(
                db,
                user_id=m.user_id,
                message_id=m.id,
                idempotency_key=dispatch_key,
            )
            try:
                result = await _attempt_send(
                    db,
                    m,
                    dict(preflight['snapshot']),
                )
            except Exception as exc:
                result = {
                    'ok': False,
                    'error': f'渠道发送异常：{type(exc).__name__}',
                    'error_class': 'transport_exception',
                    'retryable': False,
                    'dedupe_guaranteed': False,
                }
            if result.get('ok'):
                raw_cost = result.get('cost_amount')
                await growth_outreach_service.mark_sent(
                    db,
                    user_id=m.user_id,
                    message_id=m.id,
                    channel_actual=result.get('channel_actual') or m.channel,
                    provider_event_id=result.get('provider_event_id'),
                    cost_amount=(Decimal(str(raw_cost)) if raw_cost is not None else None),
                    cost_currency=result.get('cost_currency'),
                    cost_known=result.get('cost_known') is True,
                )
                stat['sent'] += 1
            elif result.get('retryable') and result.get('dedupe_guaranteed'):
                await growth_outreach_service.schedule_retry(
                    db,
                    user_id=m.user_id,
                    message_id=m.id,
                    reason=result.get('error') or '渠道瞬时失败',
                    idempotency_key=(f'retry:{result.get("provider_event_id") or dispatch_key}'),
                )
                stat['retry_scheduled'] += 1
            else:
                await growth_outreach_service.mark_failed(
                    db,
                    user_id=m.user_id,
                    message_id=m.id,
                    error=result.get('error') or 'channel_unavailable',
                    error_class=result.get('error_class') or 'channel_unavailable',
                    idempotency_key=(f'failed:{result.get("provider_event_id") or dispatch_key}'),
                )
                stat['failed'] += 1
        return stat


growth_dispatch_service = GrowthDispatchService()
