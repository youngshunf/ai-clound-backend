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

import re

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_memory.model import HasnOwnerMemory, HasnOwnerMemoryContribution
from backend.app.hasn_memory.service.transaction_lock import acquire_memory_transaction_lock
from backend.common.log import log
from backend.database.db import async_db_session
from backend.database.result import affected_rows
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 注入式 LLM completion：messages -> 合并后的 USER.md 文本。便于单测打桩。
LlmComplete = Callable[[list[dict[str, str]]], Awaitable[str]]

_MERGE_MAX_TOKENS = 2000
# pending 合并兜底重试 sweeper（MEMFIX-3）：只重试「最老 pending 已超过此秒数」的 owner，
# 避开刚 contribute 的同步内联合并（决策「保持同步内联，只加重试兜底」）。
_SWEEP_MIN_AGE_SECONDS = 120
_SWEEP_MAX_OWNERS = 50  # 单轮最多处理 owner 数（防一轮 sweep 跑太久占住 celery worker）


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

    async def _existing_user_md(self, db: AsyncSession, *, owner_id: str) -> str:
        """取该 owner 现有 USER.md 作首次合并基线：选最完整（最长非空）的一份。

        同一 owner 的各 Agent 在建档时 user_md 都是同一份 USER.md 模板（含 称呼/Owner HASN ID
        身份行）；取最长一份即可拿到含身份与已积累事实的基线，喂给 LLM 防止初始化信息被抹掉。
        """
        rows = (await db.execute(sa.select(HasnAgents.user_md).where(HasnAgents.owner_id == owner_id))).scalars().all()
        candidates = [(r or '').strip() for r in rows]
        candidates = [c for c in candidates if c]
        return max(candidates, key=len) if candidates else ''

    async def merge_owner_memory(
        self, db: AsyncSession, *, owner_id: str, llm_complete: LlmComplete | None = None
    ) -> dict[str, Any]:
        """合并该 owner 的所有 pending contribution，产出新 owner_memory 并下发。

        返回 {merged: bool, version, contributions_merged, agents_updated}。
        无 pending 时直接返回 merged=False。LLM 失败抛错（调用方决定是否吞）。
        """
        await acquire_memory_transaction_lock(db, f'owner-memory:{owner_id}')
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
        # 合并基线 = 现有 owner_memory；首次合并时它还是空的，此时回退到该 owner 现有 USER.md
        # （主人档案，含建档身份行 称呼/Owner HASN ID 与已积累事实）作基线喂给 LLM。否则 LLM 仅凭
        # 新观察重写 user_md，会把昵称/HASN_ID 等初始化信息整段抹掉（本次修复的数据丢失 bug）。
        current_content = existing.content if existing and existing.content else ''
        if not current_content:
            current_content = await self._existing_user_md(db, owner_id=owner_id)
        observations = [c.content.strip() for c in pending if c.content and c.content.strip()]

        complete = llm_complete or _default_llm_complete
        merged_content = (await complete(_merge_messages(current_content, observations))).strip()
        if not merged_content:
            raise ValueError('owner memory merge produced empty content')

        # 身份兜底：LLM 合并仍可能漏掉建档身份事实（称呼/Owner HASN ID）。从权威来源
        # （HasnHumans.nickname + owner_id）补回，确保主人昵称/HASN_ID 永不因合并丢失；
        # 对已被旧逻辑抹掉身份的存量 owner，下一次合并即自动补回（自愈）。
        nickname = (
            await db.execute(sa.select(HasnHumans.nickname).where(HasnHumans.hasn_id == owner_id).limit(1))
        ).scalar_one_or_none()
        merged_content = _ensure_identity_lines(merged_content, nickname=nickname or '', owner_id=owner_id)

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
        agents_updated = affected_rows(result)
        await db.flush()

        # 画像下行 WSPUSH（KIND_AGENTS）：覆盖 user_md + bump profile_revision 后，主动推该 owner
        # 在线节点「agents 维度变了」→ daemon 收到即全量重拉 agents 镜像（刷新本地 USER.md/SOUL.md）
        # 并把新 USER.md 写进在线 hermes 工作区，不等下次派发即生效。best-effort：推送失败不拖垮合并
        # （离线设备靠重连握手 + Runtime 轮询 profile_revision 兜底追平）。
        try:
            from backend.app.hasn.service.sync_invalidate_service import KIND_AGENTS, bump_owner

            await bump_owner(KIND_AGENTS, db, owner_id)
        except Exception as exc:  # 推送 best-effort，不拖垮合并下发
            log.warning(f'owner memory merge: WSPUSH agents invalidate failed owner={owner_id}: {exc}')

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

    async def sweep_pending_merges(
        self,
        *,
        min_age_seconds: int = _SWEEP_MIN_AGE_SECONDS,
        max_owners: int = _SWEEP_MAX_OWNERS,
        owner_ids: list[str] | None = None,
        llm_complete: LlmComplete | None = None,
    ) -> dict[str, Any]:
        """扫描有滞留 pending contribution 的 owner，逐个重跑合并（同步内联失败的兜底重试）。

        同步内联合并（contribute 热路径）若因 LLM 网关挂/余额不足失败，贡献留 pending。本
        sweeper 由 celery beat 周期触发，把这些滞留贡献重新合并下发——配合 failover 模型链，
        网关恢复后下一轮即合并成功，杜绝「采访完 coverage 永不更新」。

        只挑「最老 pending 已超过 ``min_age_seconds``」的 owner，避开刚 contribute、同步内联
        正在处理的同一批观察（防与热路径双合并）。逐 owner 独立事务：单个 owner 合并失败（LLM
        仍挂）不影响其余、pending 留到下轮再试（零 fake，不产生假合并）。

        ``owner_ids`` 可选：限定只扫这些 owner（用于运维定向补合并 / 测试隔离，不波及其他 owner
        的真实数据）；缺省扫全部有滞留 pending 的 owner。

        返回 {candidates, merged, no_pending, failed}。
        """
        cutoff = timezone.now() - timedelta(seconds=max(0, min_age_seconds))
        async with async_db_session() as db:
            candidate_query = (
                sa
                .select(HasnOwnerMemoryContribution.owner_id)
                .where(HasnOwnerMemoryContribution.status == 'pending')
                .group_by(HasnOwnerMemoryContribution.owner_id)
                .having(sa.func.min(HasnOwnerMemoryContribution.created_time) <= cutoff)
                .order_by(sa.func.min(HasnOwnerMemoryContribution.created_time).asc())
                .limit(max(1, max_owners))
            )
            if owner_ids is not None:
                candidate_query = candidate_query.where(HasnOwnerMemoryContribution.owner_id.in_(owner_ids))
            candidates = list((await db.execute(candidate_query)).scalars().all())

        summary = {'candidates': len(candidates), 'merged': 0, 'no_pending': 0, 'failed': 0}
        for owner_id in candidates:
            try:
                async with async_db_session.begin() as db:
                    outcome = await self.merge_owner_memory(db, owner_id=owner_id, llm_complete=llm_complete)
                if outcome.get('merged'):
                    summary['merged'] += 1
                else:
                    # 候选时有 pending，到合并时已无（同步内联抢先处理）——非失败，正常竞态。
                    summary['no_pending'] += 1
            except Exception as exc:  # noqa: PERF203 — 每个 owner 独立 try：单 owner 合并失败不得拖垮其余
                summary['failed'] += 1
                log.warning(f'owner memory sweep retry failed for {owner_id}: {exc}')
        return summary


def _merge_messages(current: str, observations: list[str]) -> list[dict[str, str]]:
    joined = '\n\n---\n\n'.join(observations) if observations else '(无)'
    return [
        {
            'role': 'system',
            'content': (
                '你负责维护一个人（主人）的个人记忆档案 USER.md，供其多个 AI 分身共享；'
                '该文件由 Hermes 记忆工具按 § 分隔读写，不是普通 Markdown。'
                '把新观察合并进现有档案：去重、消解冲突（新观察更可信）、保持事实、简洁、可长期复用。'
                '\n【最高优先·绝不丢失】现有档案是增量演进的，不是重写：'
                '\n- 必须保留现有档案里已有的全部事实，尤其是主人身份与建档信息——'
                '「称呼/昵称」「Owner HASN ID」等建档身份行无论如何都要原样保留，绝不能删除或改写；'
                '\n- 新观察只用于「新增事实」或「更新已有事实」，不得作为「丢弃现有事实」的理由；'
                '\n- 若现有档案里有占位提示（如「待补充」「待观察」），用真实观察替换它；没有真实观察时保留占位。'
                '\n输出格式（必须严格遵守）：'
                '\n- 每条事实独占一段，尽量用「要点: 内容」一行表达；'
                '\n- 段与段之间用单独一行的 § 分隔（即换行、§、换行）；'
                '\n- 不要用 Markdown 标题(#)/列表符号(- )、不要代码围栏、不要任何解释；'
                '\n- 总长控制在 1300 字符以内（身份行优先保留，超长时压缩描述类内容而非删身份）。'
                '\n只输出合并后的 USER.md 正文。'
            ),
        },
        {
            'role': 'user',
            'content': (
                f'现有 USER.md（§ 记录格式，须整体保留其事实，尤其身份行）：\n{current or "(空)"}\n\n'
                f'各分身上传的新观察（合并进来，只增不删）：\n{joined}\n\n'
                '返回更新后的 USER.md（仍用 § 记录格式，且包含现有档案中的全部身份/事实）：'
            ),
        },
    ]


# 匹配建档身份行：行首「称呼:/昵称:」（半/全角冒号）。用于判断 LLM 合并结果是否已含身份。
_NICKNAME_LABEL_RE = re.compile(r'(?m)^\s*(?:称呼|昵称)\s*[:：]')


def _ensure_identity_lines(content: str, *, nickname: str, owner_id: str) -> str:
    """确保合并结果含主人身份行（称呼 / Owner HASN ID），缺则从权威来源补回。

    LLM 合并可能漏掉建档身份事实（历史上甚至把它整段抹掉）。这里以 HasnHumans.nickname 与
    owner_id 为权威，把缺失的身份行补到档案最前面，确保主人昵称/HASN_ID 永不因合并丢失——
    对已被旧逻辑抹掉身份的存量 owner，下一次合并即自愈。纯函数（不碰 DB），便于单测。
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
