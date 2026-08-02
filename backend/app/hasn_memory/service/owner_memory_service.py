"""Owner 记忆（USER.md 合并态）云端存储服务。

**doc19 §10 退役（2026-07-31）**：云端 LLM 内联合并整条下线——`merge_owner_memory`、
`sweep_pending_merges`、合并提示词 `_merge_messages` 与 `owner_memory_retry_pending_merges`
celery beat 全部删除。理由见 doc19 §5.1（福仔拍板）：合并需要判断力，判断力应该在分身身上；
主脑合并完会用人话向主人汇报；成本归位到主人自己的 LLM 额度。

现在的分工：

- **事实写入发生在 hasn-node 本地**，云端不再接收 contribution；
- **合并在主脑分身的设备上做**，整轮结果经云端合并闸 `merge_gate_service.apply` 提交；
- 本 service 只读 owner 合并态，并保留主人手工编辑标记；
- `owner_memory` 表继续作为 MEMPUSH（→ 各 Agent USER.md）下发源，唯一业务写者是合并闸。

`_ensure_identity_lines` 保留：它是**合并闸**写 `owner_memory` 前的身份兜底（称呼 / Owner
HASN ID 从权威来源补回），与谁在跑 LLM 无关，纯函数。
"""

from __future__ import annotations

import re

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_memory.model import HasnOwnerMemory

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class OwnerMemoryService:
    async def mark_owner_edited(self, db: AsyncSession, *, owner_id: str) -> None:
        """标记「主人手工改过档案正文」（doc19 §4.6 正文直编逃生口 · D-20）。

        **只有主人手工写入才调这里**。判据不是「谁改了 `hasn_agents.user_md`」，而是
        「这次写入是不是主人自己敲的正文」——三条系统写入路径都**不得**置位：

        - **MEMPUSH 下发**：合并 apply 成功后 `merge_gate_service._apply_owner_memory`
          用一条 bulk UPDATE 覆盖该 owner 全部分身的 `user_md`，不经本方法；
        - **合并 apply 写回**：同上，且它在落新正文时**复位**本标位（手工版本已被本轮
          重算消费）；
        - **系统兜底改写**：`hasn_agents_service.refresh_seeded_agent_display_names` 的
          昵称刷新、建档播种（`register_hasn_agent` / 模板物化 / `create_agent_cloud_first`）
          都直接改 ORM 字段或走建档路径，同样不经本方法。

        置反了的代价是**单向不可逆**：每轮合并后都被误标成「主人改过」，下一轮重算 prompt
        就永久携带「保留主人手工表述」的强调段，且再也复位不掉——档案从此被一版旧正文钉死。

        **绝不动 `version`**：它是提交云端合并闸的 CAS 基线，而基线取自主脑那台设备的本地
        `owner_portraits.version`（`hasn-mcp/src/memory.rs::compute_plan`）。这里一动，主脑
        下一轮提交必然 409 `version_conflict`，症状只表现成「主脑很久没整理了」。建行分支给
        `version=0`（= 尚未合并过），与本地 `mark_owner_portrait_edited` 的建行值同口径。

        也**不写 `content`**：云端这一列是合并态（MEMPUSH 下发源），主人手改的正文权威副本在
        `hasn_agents.user_md`；把手改正文塞进合并态会让「正文变了但 version 没动」的行出现，
        破坏合并态与轮次水位的对应关系。
        """
        await db.execute(
            pg_insert(HasnOwnerMemory)
            .values(owner_id=owner_id, content=None, version=0, owner_edited=True)
            .on_conflict_do_update(index_elements=['owner_id'], set_={'owner_edited': True})
        )
        await db.flush()

    async def get_owner_memory(self, db: AsyncSession, *, owner_id: str) -> dict[str, Any]:
        """读取该 owner 当前合并记忆（下发给 Agent）。"""
        row = (
            await db.execute(sa.select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner_id).limit(1))
        ).scalar_one_or_none()
        if row is None:
            return {'content': None, 'version': 0, 'owner_edited': False}
        # `version=0` 如实返回 0（= 尚未合并过），不折成 1：主人先手工直编、还没整理过时行就是
        # 这个状态（`mark_owner_edited` 的建行分支），谎报成 1 会让完整度判定误以为画像前进了一版。
        return {
            'content': row.content,
            'version': int(row.version or 0),
            'owner_edited': bool(row.owner_edited),
        }


# 匹配建档身份行：行首「称呼:/昵称:」（半/全角冒号）。用于判断合并结果是否已含身份。
_NICKNAME_LABEL_RE = re.compile(r'(?m)^\s*(?:称呼|昵称)\s*[:：]')


def _ensure_identity_lines(content: str, *, nickname: str, owner_id: str) -> str:
    """确保合并结果含主人身份行（称呼 / Owner HASN ID），缺则从权威来源补回。

    主脑的 LLM 重算同样可能漏掉建档身份事实（历史上云端合并甚至把它整段抹掉）。合并闸落库前
    以 HasnHumans.nickname 与 owner_id 为权威，把缺失的身份行补到档案最前面，确保主人昵称 /
    HASN_ID 永不因某一轮合并丢失——已被旧逻辑抹掉身份的存量 owner，下一次合并即自愈。
    纯函数（不碰 DB），便于单测。
    """
    text = (content or '').strip()
    prepend: list[str] = []
    nick = (nickname or '').strip()
    if nick and not _NICKNAME_LABEL_RE.search(text):
        prepend.append(f'称呼: {nick}')
    oid = (owner_id or '').strip()
    if oid and oid not in text:
        prepend.append(f'Owner HASN ID: {oid}')
    if not prepend:
        return text
    head = '\n§\n'.join(prepend)
    return f'{head}\n§\n{text}' if text else head


def _estimate_tokens(text: str) -> int:
    # 粗略估算：中文按字符、英文按 ~4 字符/token，取保守上界。
    return max(1, len(text) // 3)


owner_memory_service = OwnerMemoryService()
