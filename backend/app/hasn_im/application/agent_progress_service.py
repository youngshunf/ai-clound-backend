"""分身回复进度的瞬态转发（`hasn.agent.progress`）。

场景：分身回复的不是自己主人（跨主人 1:1 / 群聊）时，对端主人的节点收不到任何在途信号——
流式帧 `push_runtime_stream` 按 owner 隔离、只到分身所在节点的主人。本模块把「分身开始处理 /
已调用 N 个工具 / 本轮结束」这三类**状态**转给会话受众里的其它主人。

两条硬边界：

- **不带正文**。发送端 daemon 的出站披露闸（J-S3）拿**完整回复文本**判决，拦截即整条不外发；
  边流边送未完成正文会让拦截形同虚设。协议层因此不留正文字段（见设计文档 §3.2）。
- **不落库、不入离线队列**。分类为 `TRANSIENT`（见 `offline_frame_policy`），离线即丢——
  离线期间补投在途状态没有意义，最终回复本身走 `hasn.message.new` 的 durable 链恢复。

受众口径与消息投递同源（`compute_audience_owner_ids`），它同时也是鉴权判据：不在受众里的
主人收不到，`from_id` 不是会话参与者的伪造帧直接丢弃。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnConversations
from backend.app.hasn.service import conversation_projection as cp
from backend.app.hasn_core import HasnAgents
from backend.app.hasn_im.application import message_service
from backend.app.hasn_im.application.node_session_service import node_session_service
from backend.common.log import log


async def _load_conversation(db: AsyncSession, conversation_id: str) -> HasnConversations | None:
    """按云端权威会话 id 读会话；id 非法（非 UUID）或不存在都返回 None。"""
    try:
        return await db.get(HasnConversations, conversation_id)
    except Exception:  # noqa: BLE001 非法 id 形态由 SQLAlchemy 抛型别异常，瞬态帧一律丢弃
        return None


async def _participant_ids(db: AsyncSession, conv: HasnConversations) -> set[str]:
    """会话参与者 hasn_id 集合（direct 两方 / group 活动名册）。"""
    if conv.type == 'group':
        members = await message_service.list_group_members(db, str(conv.id))
        return {m.member_id for m in members if m.member_id}
    projection = cp.build_conversation_projection(conv)
    return {p['hasn_id'] for p in projection.get('participants', []) if p.get('hasn_id')}


async def _owner_of(db: AsyncSession, hasn_id: str) -> str | None:
    """分身 → 主人；本身就是主人（`h_`）则返回自己。"""
    if hasn_id.startswith('h_'):
        return hasn_id
    result = await db.execute(select(HasnAgents.owner_id).where(HasnAgents.hasn_id == hasn_id))
    return result.scalar_one_or_none()


async def relay_agent_progress(
    db: AsyncSession,
    *,
    from_id: str,
    conversation_id: str,
    payload: dict[str, Any],
) -> list[str]:
    """把一帧分身回复进度推给会话受众里的**其它**主人，返回本帧的转发目标主人列表。

    发送分身自己的主人不在其中：它的 daemon 本地 `push_runtime_stream` 已有完整过程，
    再从云端收一份只会和本地流打架。

    返回的是**转发目标**而非投递成功数——目标离线就是丢弃（瞬态帧不补投），在线与否
    不改变「这一帧该给谁」这个判决。
    """
    conv = await _load_conversation(db, conversation_id)
    if conv is None:
        return []

    participants = await _participant_ids(db, conv)
    if from_id not in participants:
        # 伪造 / 过期帧：分身不在这个会话里，静默丢弃（瞬态事件不值得占用错误通道）。
        log.warning('[AgentProgress] from_id=%s 不是会话 %s 的参与者，丢弃进度帧', from_id, conversation_id)
        return []

    audience = await cp.compute_audience_owner_ids(db, conv)
    sender_owner = await _owner_of(db, from_id)
    targets = [owner for owner in audience if owner != sender_owner]

    for owner in targets:
        # best-effort：目标离线返回 False（帧被 TRANSIENT 策略丢弃、不入队），照常继续下一个。
        await node_session_service.push_to_owner(owner, payload)
    return targets


__all__ = ['relay_agent_progress']
