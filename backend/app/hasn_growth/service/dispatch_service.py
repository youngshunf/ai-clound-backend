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

from typing import Any, Awaitable, Callable

import sqlalchemy as sa

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.outreach_message import OutreachMessage
from backend.app.hasn_growth.service.outreach_service import (
    _QUIET_END_HOUR,
    _QUIET_START_HOUR,
    growth_outreach_service,
)
from backend.utils.timezone import timezone

# owner 手动复制发送渠道：worker 不接管（owner 驱动）。
_MANUAL_CHANNEL = 'manual_assist'

# 渠道 sender 注册表：channel -> async (db, message) -> {'ok': bool, 'channel_actual'?: str, 'error'?: str}。
# 默认空——真实 IM/邮件 transport（飞书 gateway 等下行接缝）在 M8 物化时注册；未注册渠道一律 channel_unavailable。
ChannelSenderFn = Callable[[AsyncSession, OutreachMessage], Awaitable[dict[str, Any]]]
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


async def _attempt_send(db: AsyncSession, message: OutreachMessage) -> dict[str, Any]:
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
    return await sender(db, message)


class GrowthDispatchService:
    """获客发送 worker：扫 approved → 按渠道闸门分发 → 状态回写（sent/failed）。"""

    @staticmethod
    async def dispatch_approved_batch(
        db: AsyncSession, *, limit: int = 50, now_hour: int | None = None
    ) -> dict[str, int]:
        """扫一批 approved 出站触达并分发。返回统计（旁路，可观测）。

        now_hour：测试可注入小时数覆盖 quiet hours 判定；缺省取服务端当前小时。
        """
        hour = now_hour if now_hour is not None else timezone.now().hour
        quiet_ok = _QUIET_START_HOUR <= hour < _QUIET_END_HOUR

        messages = (
            await db.execute(
                sa.select(OutreachMessage)
                .where(OutreachMessage.status == 'approved', OutreachMessage.direction == 'outbound')
                .order_by(OutreachMessage.id)
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()

        stat = {
            'scanned': 0,
            'sent': 0,
            'failed': 0,
            'skipped_manual': 0,
            'queued_quiet_hours': 0,
            'wechat_unconfirmed': 0,
        }
        for m in messages:
            stat['scanned'] += 1
            if m.channel == _MANUAL_CHANNEL:
                stat['skipped_manual'] += 1
                continue
            if not quiet_ok:
                stat['queued_quiet_hours'] += 1  # 保持 approved，下个 tick 再扫
                continue
            if m.channel == 'wechat' and not await _wechat_auto_send_confirmed(db, m.user_id):
                stat['wechat_unconfirmed'] += 1  # J1 未确认 → 恒 manual_assist，主人手动发
                continue

            # 认领（approved → sending）后尝试真实发送，结果如实回写。
            await growth_outreach_service.mark_sending(db, user_id=m.user_id, message_id=m.id)
            result = await _attempt_send(db, m)
            if result.get('ok'):
                await growth_outreach_service.mark_sent(
                    db, user_id=m.user_id, message_id=m.id, channel_actual=result.get('channel_actual') or m.channel
                )
                stat['sent'] += 1
            else:
                await growth_outreach_service.mark_failed(
                    db, user_id=m.user_id, message_id=m.id, error=result.get('error') or 'channel_unavailable'
                )
                stat['failed'] += 1
        return stat


growth_dispatch_service = GrowthDispatchService()
