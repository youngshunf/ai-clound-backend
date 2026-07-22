"""hasn_im.consumers.push_notifier · best-effort 消费者：移动推送唤醒（§7.3-3）

消费 ``im.message.committed`` → 计算受众 owner（剔除发送方自己）→ 每个 owner 下发一次
友盟 U-Push 唤醒。**收编原 B6 ``tasks/push_message`` 的两条不变式**：
- **1 秒会话合并去重**（§7.3-3 / 不变式 §8.6）：同一 ``conversation_id`` 1 秒内只下发一次，
  Redis ``hasn_push_dedup:{conv_id}`` SET NX EX=1——首条抢到锁触发整轮扇出，其余合并跳过，
  避免高频对话刷屏。
- **payload 不带正文**（不变式 §4）：只带 title/body 占位 + trace_id，真实消息由客户端拉取。

best-effort（§7.2）：成功 = 已尝试投递（成败均推进 cursor）；不重试不进 DLQ、不参与 retention。
单个 owner 下发失败只记 warn 不牵连其它 owner（push_dispatcher 内部已做网络重试，再抛会重复下发）。
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service import conversation_projection as cp
from backend.app.hasn_im.consumers.base import ConsumerClass, IntegrationEvent
from backend.app.hasn_im.consumers.facts import IM_MESSAGE_COMMITTED, MessageCommittedFacts
from backend.app.services.push_dispatcher import DispatchResult, PushDispatchError
from backend.app.services.push_dispatcher import dispatch as _real_dispatch
from backend.database.redis import redis_client as _real_redis

log = logging.getLogger(__name__)

_DEDUP_KEY_PREFIX = 'hasn_push_dedup:'
_DEDUP_TTL_SECONDS = 1
# 推送占位文案——不变式 §4：不下发消息正文，客户端收到推送后主动拉消息列表。
_DEFAULT_TITLE = '新消息'
_DEFAULT_BODY = '您有一条新消息'

# dispatch 端口形状：(db, hasn_id, payload) -> DispatchResult；默认现网友盟实现，测试可注入替身。
DispatchFn = Callable[..., Awaitable[DispatchResult]]


@dataclass(slots=True)
class PushNotifier:
    """best-effort：已提交消息 → 受众 owner 移动端 U-Push 唤醒（1s 会话去重 + 无正文，§7.3-3）。"""

    # Redis 客户端（1 秒会话合并去重）；默认现网单例，测试可注入替身。
    redis: Any = field(default_factory=lambda: _real_redis)
    # 推送下发函数；默认现网友盟 dispatch，测试可注入替身。
    dispatch_fn: DispatchFn = field(default=_real_dispatch)

    @property
    def name(self) -> str:
        return 'push_notifier'

    @property
    def consumer_class(self) -> ConsumerClass:
        return ConsumerClass.BEST_EFFORT

    async def handle(self, event: IntegrationEvent, db: AsyncSession) -> None:
        """对每个受众 owner（剔除发送方）下发一次 U-Push（1s 会话去重合并整轮扇出）。"""
        if event.event_type != IM_MESSAGE_COMMITTED:
            return
        facts = MessageCommittedFacts.from_event(event)

        conv = await cp._fetch_conversation(db, facts.conversation_id)
        if conv is None:
            return
        members = await cp._load_group_members(db, str(conv.id)) if conv.type == 'group' else None
        audience = await cp.compute_audience_owner_ids(db, conv, members=members)

        # 发送方自己的设备不需要被自己的消息唤醒——剔除发送方 owner（对齐原 U-Push 只推 to_id）。
        resolved = await cp._resolve_owner_ids(db, [facts.sender_hasn_id])
        sender_owner_id = resolved.get(facts.sender_hasn_id)
        targets = [o for o in audience if o != sender_owner_id]
        if not targets:
            return

        # 1 秒会话合并去重：首条抢到锁触发整轮扇出，窗口内后续消息整体跳过（不逐 owner 去重）。
        dedup_key = f'{_DEDUP_KEY_PREFIX}{facts.conversation_id}'
        acquired = await self.redis.set(dedup_key, '1', nx=True, ex=_DEDUP_TTL_SECONDS)
        if not acquired:
            log.info(
                '[push_notifier] conv=%s 1s 窗口内已下发，合并跳过 (message_id=%s)',
                facts.conversation_id, facts.message_id,
            )
            return

        payload = _push_payload(facts.conversation_id)
        for owner_id in targets:
            try:
                result = await self.dispatch_fn(db, hasn_id=owner_id, payload=payload)
            except PushDispatchError:
                # 单个 owner 下发失败不牵连其它 owner；best-effort 记 warn 不重试（dispatcher 已重试过）。
                log.warning(
                    '[push_notifier] dispatch 失败 owner=%s conv=%s',
                    owner_id, facts.conversation_id,
                )
                continue
            if result.sent == 0:
                log.info('[push_notifier] owner=%s 无 token，conv=%s', owner_id, facts.conversation_id)


def _push_payload(conversation_id: str) -> dict[str, Any]:
    """U-Push payload（不带正文，trace_id 同一会话稳定，便于客户端归并）。"""
    return {
        'title': _DEFAULT_TITLE,
        'body': _DEFAULT_BODY,
        'trace_id': f'conv:{conversation_id}',
    }
