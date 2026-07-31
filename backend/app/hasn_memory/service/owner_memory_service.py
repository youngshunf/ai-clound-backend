"""Owner 记忆（USER.md 合并态）云端存储服务。

**doc19 §10 退役（2026-07-31）**：云端 LLM 内联合并整条下线——`merge_owner_memory`、
`sweep_pending_merges`、合并提示词 `_merge_messages` 与 `owner_memory_retry_pending_merges`
celery beat 全部删除。理由见 doc19 §5.1（福仔拍板）：合并需要判断力，判断力应该在分身身上；
主脑合并完会用人话向主人汇报；成本归位到主人自己的 LLM 额度。

现在的分工：

- **合并在主脑分身的设备上做**，整轮结果经云端合并闸 `merge_gate_service.apply` 提交；
- 本 service 退为「贡献流入 + 合并态读出」：`contribute` 只落 contribution(pending)，
  **不再内联合并**；`get_owner_memory` / `list_contributions` 是纯读；
- `owner_memory` 表继续作为合并态存储与 MEMPUSH（→ 各 Agent user_md）下发源，写者换成合并闸。

**显式承认的体验回退**（doc19 §10）：一条贡献进 USER.md 由「即时」变成「最长至下次整理」
（主脑离线更久）。缓解手段是主人「立即整理」+ 云端合并待办（§5.5），**不是**在响应里假装
已经合并——`contribute` 返回 `pending_merge=True` 如实说明，零 fake。

`_ensure_identity_lines` 保留：它是**合并闸**写 `owner_memory` 前的身份兜底（称呼 / Owner
HASN ID 从权威来源补回），与谁在跑 LLM 无关，纯函数。
"""

from __future__ import annotations

import re

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_memory.model import HasnOwnerMemory, HasnOwnerMemoryContribution

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: 贡献入流后给主人的**唯一**如实说法。REST `/memory/contribute` 与 MCP
#: `hasn.owner.memory.contribute` 共用一份，避免两处措辞漂移出「已合并」之类的假承诺。
MEMORY_CONTRIBUTE_PENDING_NOTE = (
    '已记录这条观察，会在下次记忆整理时并入主人档案（整理由主脑分身在它所在的设备上执行）。'
)


class OwnerMemoryService:
    async def contribute(self, db: AsyncSession, *, owner_id: str, agent_hasn_id: str, content: str) -> dict[str, Any]:
        """Agent 上传一条 USER.md 观察，落 contribution(pending)。

        **只落贡献，不合并**（doc19 §5.1 / §10）：合并由主脑分身在它自己的设备上做。调用方须
        对主人如实说「已记下来了，会在下次整理时并入」，禁止编造「已合并 / 后台异步合并完成」。
        """
        text = (content or '').strip()
        if not text:
            return {'accepted': False, 'reason': 'empty_content'}
        contribution = HasnOwnerMemoryContribution(
            owner_id=owner_id,
            agent_hasn_id=agent_hasn_id,
            content=text,
            status='pending',
        )
        db.add(contribution)
        await db.flush()
        return {'accepted': True, 'contribution_id': contribution.id}

    async def get_owner_memory(self, db: AsyncSession, *, owner_id: str) -> dict[str, Any]:
        """读取该 owner 当前合并记忆（下发给 Agent）。"""
        row = (
            await db.execute(sa.select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner_id).limit(1))
        ).scalar_one_or_none()
        if row is None:
            return {'content': None, 'version': 0}
        return {'content': row.content, 'version': int(row.version or 1)}

    async def list_contributions(self, db: AsyncSession, *, owner_id: str, limit: int = 50) -> dict[str, Any]:
        """列出该 owner 的记忆贡献流（owner 透明视图，按时间倒序）。"""
        rows = list(
            (
                await db.execute(
                    sa
                    .select(HasnOwnerMemoryContribution)
                    .where(HasnOwnerMemoryContribution.owner_id == owner_id)
                    .order_by(HasnOwnerMemoryContribution.id.desc())
                    .limit(max(1, min(int(limit), 200)))
                )
            )
            .scalars()
            .all()
        )
        pending_count = (
            await db.execute(
                sa
                .select(sa.func.count())
                .select_from(HasnOwnerMemoryContribution)
                .where(
                    HasnOwnerMemoryContribution.owner_id == owner_id,
                    HasnOwnerMemoryContribution.status == 'pending',
                )
            )
        ).scalar_one()
        items = [
            {
                'id': int(r.id),
                'agent_hasn_id': r.agent_hasn_id,
                'content': r.content,
                'status': r.status,
                'merged_into_version': r.merged_into_version,
                'created_time': r.created_time,
            }
            for r in rows
        ]
        return {'items': items, 'pending_count': int(pending_count or 0)}


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
