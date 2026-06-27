"""Owner 记忆合并服务（ADR 2026-05-30 §5.4）。

各 Agent 把本地 USER.md 观察上传为 contribution(pending)；合并 worker 取该 owner
所有 pending contribution + 当前 owner_memory，调 LLM 做「合并 + 结构化压缩」，写新
owner_memory(version++)，把贡献标 merged，并把该 owner 所有 Agent 的 user_md 覆盖为
新内容、bump profile_revision —— Runtime 轮询 revision 变化后重新拉取下发。

零 mock 零 fake：默认走真实 new-api 网关（统一 backend.common.llm 客户端）；
LLM 失败则保留 contribution=pending、不动 owner_memory（不产生假合并）。

ADR-15 收编：本 service 从 `app/hasn/service/owner_memory_service.py` 迁入 `app/hasn_memory`；
身份表 HasnAgents 仍在 app/hasn（跨模块引用），记忆两表已迁入 app/hasn_memory.model。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_core import HasnAgents
from backend.app.hasn_memory.model import HasnOwnerMemory, HasnOwnerMemoryContribution
from backend.common.log import log
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 注入式 LLM completion：messages -> 合并后的 USER.md 文本。便于单测打桩。
LlmComplete = Callable[[list[dict[str, str]]], Awaitable[str]]

_MERGE_MAX_TOKENS = 2000


async def _default_llm_complete(messages: list[dict[str, str]]) -> str:
    """默认 LLM completion：走统一 new-api 客户端（默认模型 settings.LLM_DEFAULT_MODEL）。"""
    from backend.common.llm import llm_client

    return await llm_client.complete(messages, max_tokens=_MERGE_MAX_TOKENS)


class OwnerMemoryService:
    async def contribute(self, db: AsyncSession, *, owner_id: str, agent_hasn_id: str, content: str) -> dict[str, Any]:
        """Agent 上传一条 USER.md 观察，落 contribution(pending)。"""
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
                    sa.select(HasnOwnerMemoryContribution)
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
                sa.select(sa.func.count())
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

    async def merge_owner_memory(
        self, db: AsyncSession, *, owner_id: str, llm_complete: LlmComplete | None = None
    ) -> dict[str, Any]:
        """合并该 owner 的所有 pending contribution，产出新 owner_memory 并下发。

        返回 {merged: bool, version, contributions_merged, agents_updated}。
        无 pending 时直接返回 merged=False。LLM 失败抛错（调用方决定是否吞）。
        """
        pending = list(
            (
                await db.execute(
                    sa
                    .select(HasnOwnerMemoryContribution)
                    .where(
                        HasnOwnerMemoryContribution.owner_id == owner_id,
                        HasnOwnerMemoryContribution.status == 'pending',
                    )
                    .order_by(HasnOwnerMemoryContribution.id.asc())
                )
            )
            .scalars()
            .all()
        )
        if not pending:
            return {'merged': False, 'version': None, 'contributions_merged': 0, 'agents_updated': 0}

        existing = (
            await db.execute(sa.select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner_id).limit(1))
        ).scalar_one_or_none()
        current_content = existing.content if existing and existing.content else ''
        observations = [c.content.strip() for c in pending if c.content and c.content.strip()]

        complete = llm_complete or _default_llm_complete
        merged_content = (await complete(_merge_messages(current_content, observations))).strip()
        if not merged_content:
            raise ValueError('owner memory merge produced empty content')

        now = timezone.now()
        new_version = (int(existing.version) if existing else 0) + 1
        await db.execute(
            pg_insert(HasnOwnerMemory)
            .values(
                owner_id=owner_id,
                content=merged_content,
                version=new_version,
                token_count=_estimate_tokens(merged_content),
                last_merged_time=now,
            )
            .on_conflict_do_update(
                index_elements=['owner_id'],
                set_={
                    'content': merged_content,
                    'version': new_version,
                    'token_count': _estimate_tokens(merged_content),
                    'last_merged_time': now,
                },
            )
        )

        contribution_ids = [c.id for c in pending]
        await db.execute(
            sa
            .update(HasnOwnerMemoryContribution)
            .where(HasnOwnerMemoryContribution.id.in_(contribution_ids))
            .values(status='merged', merged_into_version=new_version)
        )

        # 下发：覆盖该 owner 所有 Agent 的 user_md，并 bump profile_revision
        # （Runtime 轮询 /profile/revision 变化后重新拉取覆盖本地 USER.md）。
        result = await db.execute(
            sa
            .update(HasnAgents)
            .where(HasnAgents.owner_id == owner_id)
            .values(
                user_md=merged_content,
                profile_revision=HasnAgents.profile_revision + 1,
            )
        )
        agents_updated = int(result.rowcount or 0)
        await db.flush()

        log.info(
            f'owner memory merged: owner={owner_id} version={new_version} '
            f'contributions={len(contribution_ids)} agents_updated={agents_updated}'
        )
        return {
            'merged': True,
            'version': new_version,
            'contributions_merged': len(contribution_ids),
            'agents_updated': agents_updated,
        }


def _merge_messages(current: str, observations: list[str]) -> list[dict[str, str]]:
    joined = '\n\n---\n\n'.join(observations) if observations else '(无)'
    return [
        {
            'role': 'system',
            'content': (
                '你负责维护一个人（主人）的个人记忆档案 USER.md，供其多个 AI 分身共享；'
                '该文件由 Hermes 记忆工具按 § 分隔读写，不是普通 Markdown。'
                '把新观察合并进现有档案：去重、消解冲突（新观察更可信）、保持事实、简洁、可长期复用。'
                '\n输出格式（必须严格遵守）：'
                '\n- 每条事实独占一段，尽量用「要点: 内容」一行表达；'
                '\n- 段与段之间用单独一行的 § 分隔（即换行、§、换行）；'
                '\n- 不要用 Markdown 标题(#)/列表符号(- )、不要代码围栏、不要任何解释；'
                '\n- 总长控制在 1300 字符以内。'
                '\n只输出合并后的 USER.md 正文。'
            ),
        },
        {
            'role': 'user',
            'content': (
                f'现有 USER.md（§ 记录格式）：\n{current or "(空)"}\n\n'
                f'各分身上传的新观察（合并进来）：\n{joined}\n\n'
                '返回更新后的 USER.md（仍用 § 记录格式）：'
            ),
        },
    ]


def _estimate_tokens(text: str) -> int:
    # 粗略估算：中文按字符、英文按 ~4 字符/token，取保守上界。
    return max(1, len(text) // 3)


owner_memory_service = OwnerMemoryService()
