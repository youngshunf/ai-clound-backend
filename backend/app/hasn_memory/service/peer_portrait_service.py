"""Peer 画像合成服务（doc17 PEERSYN-P2 · G2/G3）。

把某 owner 对某对方（peer）**跨全部分身**积累的语义事实合成为一份画像叙述，落
`hasn_memory.peer_portrait`（owner 视角唯一）。**多 Agent 合并**天然发生在事实层：peer 事实
`agent_id IS NULL`、owner-scoped，`recall_facts(subject_kind='peer', subject_id=peer)` 一次
把该 owner 名下所有分身对同一对方的观察全取回 → 合成成一份，不分分身来源（doc17 §5.1）。

**基线演化**：取现有 portrait 作基线喂给 LLM，只增量更新、不推倒重来（同 owner_memory 合并）；
version 单调递增。**同主人分身跳过**：对方若是本 owner 名下自己的分身（同主人 A2A：
delegate_task/constellation），runtime 侧本就不注入 peer 画像（仅 ExternalPeer 注入，doc17
§5.6），故这里直接跳过合成，不产生无用画像。

零 mock 零 fake：默认走统一 new-api 客户端（`backend.common.llm`）；LLM 失败抛错，
调用方（sweeper）逐 owner 独立事务吞掉、留待下轮重试，绝不写空/假画像。

下行：合成后由 P3 `_emit_peer_portrait_event` 发 `memory.peer_portrait.upserted`
（namespace='portraits'）→ daemon 拉取落本地镜像 → runtime 注入。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import time
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_core import HasnAgents
from backend.app.hasn_memory.model.peer_portrait import PeerPortrait
from backend.app.hasn_memory.model.semantic_fact import SemanticFact
from backend.app.hasn_memory.service.semantic_fact_service import semantic_fact_service
from backend.common.log import log
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 注入式 LLM completion：messages -> 合成后的画像正文。便于单测打桩（同 owner_memory）。
LlmComplete = Callable[[list[dict[str, str]]], Awaitable[str]]

_SYNTHESIZE_MAX_TOKENS = 1200
_MAX_FACTS = 200  # 单个 peer 单次合成最多纳入的 active 事实数
_MAX_PORTRAIT_CHARS = 900  # 画像正文软上限（注入 identity_peer 用，控注入预算）
_SWEEP_MAX_PAIRS = 80  # 单轮 sweep 最多处理 (owner, peer) 对数（防占住 celery worker）


def _now_ms() -> int:
    return int(time() * 1000)


def _peer_kind(peer_hasn_id: str) -> str:
    """按 HASN ID 前缀判定对方类型：a_ 为分身，其余（h_）为人类（doc17 §5.6）。"""
    return 'agent' if (peer_hasn_id or '').startswith('a_') else 'human'


def _estimate_tokens(text: str) -> int:
    # 粗略估算：中文按字符、英文按 ~4 字符/token，取保守上界（同 owner_memory）。
    return max(1, len(text) // 3)


async def _default_llm_complete(messages: list[dict[str, str]]) -> str:
    """默认 LLM completion：走统一 new-api 客户端（默认模型 settings.LLM_DEFAULT_MODEL）。"""
    from backend.common.llm import llm_client

    return await llm_client.complete(messages, max_tokens=_SYNTHESIZE_MAX_TOKENS)


class PeerPortraitService:
    """Peer 画像合成 + upsert + 脏扫描（owner 隔离强制）。"""

    async def _is_same_owner_agent(self, db: AsyncSession, *, owner_id: str, peer_hasn_id: str) -> bool:
        """对方是否为本 owner 名下自己的分身（同主人 A2A → runtime 不注入 peer 画像）。"""
        if not peer_hasn_id.startswith('a_'):
            return False
        found = (
            await db.execute(
                sa.select(HasnAgents.hasn_id)
                .where(HasnAgents.owner_id == owner_id, HasnAgents.hasn_id == peer_hasn_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        return found is not None

    async def _existing_portrait(self, db: AsyncSession, *, owner_id: str, peer_hasn_id: str) -> PeerPortrait | None:
        return (
            await db.execute(
                sa.select(PeerPortrait).where(
                    PeerPortrait.owner_id == owner_id,
                    PeerPortrait.peer_hasn_id == peer_hasn_id,
                )
            )
        ).scalar_one_or_none()

    async def get_portrait(self, db: AsyncSession, *, owner_id: str, peer_hasn_id: str) -> dict[str, Any] | None:
        """读取一份画像（owner 隔离）。无则 None。"""
        row = await self._existing_portrait(db, owner_id=owner_id, peer_hasn_id=peer_hasn_id)
        return _serialize(row) if row is not None else None

    async def synthesize_peer_portrait(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        peer_hasn_id: str,
        llm_complete: LlmComplete | None = None,
    ) -> dict[str, Any]:
        """合成/演化某 owner 对某 peer 的画像并 upsert（version++）。

        返回 {synthesized: bool, reason?, version?, source_fact_count?, peer_kind?, portrait?}。
        - 对方是本 owner 名下分身 → synthesized=False, reason='same_owner_agent'（不注入，跳过）；
        - 无 active peer 事实 → synthesized=False, reason='no_facts'（不写空画像）；
        - LLM 产空 → 抛错（调用方吞并留待重试，零 fake）。
        """
        owner_id = (owner_id or '').strip()
        peer_hasn_id = (peer_hasn_id or '').strip()
        if not owner_id or not peer_hasn_id:
            raise ValueError('owner_id 与 peer_hasn_id 必填')
        if owner_id == peer_hasn_id:
            return {'synthesized': False, 'reason': 'self'}
        if await self._is_same_owner_agent(db, owner_id=owner_id, peer_hasn_id=peer_hasn_id):
            return {'synthesized': False, 'reason': 'same_owner_agent'}

        facts = await semantic_fact_service.recall_facts(
            db, owner_id=owner_id, subject_kind='peer', subject_id=peer_hasn_id, limit=_MAX_FACTS
        )
        if not facts:
            return {'synthesized': False, 'reason': 'no_facts'}

        existing = await self._existing_portrait(db, owner_id=owner_id, peer_hasn_id=peer_hasn_id)
        baseline = (existing.portrait_text if existing and existing.portrait_text else '').strip()

        complete = llm_complete or _default_llm_complete
        portrait_text = (await complete(_synthesize_messages(peer_hasn_id, baseline, facts))).strip()
        if not portrait_text:
            raise ValueError('peer portrait synthesis produced empty content')
        if len(portrait_text) > _MAX_PORTRAIT_CHARS:
            portrait_text = portrait_text[:_MAX_PORTRAIT_CHARS].rstrip()

        peer_kind = _peer_kind(peer_hasn_id)
        # 最后交互时间 = 纳入事实里最新的 updated_at（epoch ms）
        last_interaction_at = max((int(f.get('updated_at') or 0) for f in facts), default=None) or None
        now = _now_ms()
        new_version = (int(existing.version) if existing else 0) + 1
        token_count = _estimate_tokens(portrait_text)

        await db.execute(
            pg_insert(PeerPortrait)
            .values(
                owner_id=owner_id,
                peer_hasn_id=peer_hasn_id,
                peer_kind=peer_kind,
                portrait_text=portrait_text,
                language='zh',
                version=new_version,
                revised_by='system',
                source_fact_count=len(facts),
                last_synthesized_at=now,
                last_interaction_at=last_interaction_at,
                token_count=token_count,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=['owner_id', 'peer_hasn_id'],
                set_={
                    'peer_kind': peer_kind,
                    'portrait_text': portrait_text,
                    'version': new_version,
                    'source_fact_count': len(facts),
                    'last_synthesized_at': now,
                    'last_interaction_at': last_interaction_at,
                    'token_count': token_count,
                    'updated_at': now,
                },
            )
        )
        await db.flush()
        row = await self._existing_portrait(db, owner_id=owner_id, peer_hasn_id=peer_hasn_id)

        log.info(
            f'peer portrait synthesized: owner={owner_id} peer={peer_hasn_id} '
            f'kind={peer_kind} version={new_version} facts={len(facts)}'
        )
        return {
            'synthesized': True,
            'version': new_version,
            'peer_kind': peer_kind,
            'source_fact_count': len(facts),
            'portrait': _serialize(row) if row is not None else None,
        }

    async def _dirty_pairs(
        self, db: AsyncSession, *, max_pairs: int, owner_ids: list[str] | None = None
    ) -> list[tuple[str, str]]:
        """方案B 脏判定：找「有新 peer 事实但画像未追上」的 (owner, peer) 对。

        对每个 (owner_id, peer_subject) 聚合 active peer 事实的 MAX(updated_at)，与 portrait
        的 last_synthesized_at 比对：画像缺失、或最新事实晚于上次合成 → 该对需（重）合成。

        ``owner_ids`` 可选：限定只扫这些 owner（运维定向补合成 / 测试隔离，不波及其他 owner 的真实数据）。
        """
        fact_where = [SemanticFact.subject_kind == 'peer', SemanticFact.status == 'active']
        if owner_ids is not None:
            fact_where.append(SemanticFact.owner_id.in_(owner_ids))
        fact_agg = (
            sa.select(
                SemanticFact.owner_id.label('owner_id'),
                SemanticFact.subject_id.label('peer_hasn_id'),
                sa.func.max(SemanticFact.updated_at).label('max_fact_updated'),
            )
            .where(*fact_where)
            .group_by(SemanticFact.owner_id, SemanticFact.subject_id)
            .subquery()
        )
        stmt = (
            sa.select(fact_agg.c.owner_id, fact_agg.c.peer_hasn_id)
            .select_from(
                fact_agg.outerjoin(
                    PeerPortrait,
                    sa.and_(
                        PeerPortrait.owner_id == fact_agg.c.owner_id,
                        PeerPortrait.peer_hasn_id == fact_agg.c.peer_hasn_id,
                    ),
                )
            )
            .where(
                sa.or_(
                    PeerPortrait.last_synthesized_at.is_(None),
                    fact_agg.c.max_fact_updated > PeerPortrait.last_synthesized_at,
                )
            )
            .order_by(fact_agg.c.max_fact_updated.asc())
            .limit(max(1, max_pairs))
        )
        rows = (await db.execute(stmt)).all()
        return [(r[0], r[1]) for r in rows]

    async def sweep_peer_portraits(
        self,
        *,
        max_pairs: int = _SWEEP_MAX_PAIRS,
        owner_ids: list[str] | None = None,
        llm_complete: LlmComplete | None = None,
    ) -> dict[str, Any]:
        """扫描「事实已更新但画像未追上」的 (owner, peer) 对，逐对（重）合成 + 下行发射。

        逐对独立事务：单对合成失败（LLM 挂）不影响其余、留到下轮再试（零 fake）。同主人分身、
        无事实的对会被 synthesize 内部跳过（记 skipped）。合成成功后发下行事件（P3）。

        ``owner_ids`` 可选：限定只扫这些 owner（运维定向补合成 / 测试隔离，不波及其他 owner 的真实数据）。

        返回 {candidates, synthesized, skipped, failed}。
        """
        async with async_db_session() as db:
            pairs = await self._dirty_pairs(db, max_pairs=max_pairs, owner_ids=owner_ids)

        summary = {'candidates': len(pairs), 'synthesized': 0, 'skipped': 0, 'failed': 0}
        for owner_id, peer_hasn_id in pairs:
            try:
                async with async_db_session.begin() as db:
                    outcome = await self.synthesize_peer_portrait(
                        db, owner_id=owner_id, peer_hasn_id=peer_hasn_id, llm_complete=llm_complete
                    )
                    if outcome.get('synthesized'):
                        await self._emit_peer_portrait_event(db, owner_id=owner_id, portrait=outcome['portrait'])
                if outcome.get('synthesized'):
                    summary['synthesized'] += 1
                else:
                    summary['skipped'] += 1
            except Exception as exc:  # noqa: PERF203 — 每对独立 try：单对失败不得拖垮其余
                summary['failed'] += 1
                log.warning(f'peer portrait sweep failed for owner={owner_id} peer={peer_hasn_id}: {exc}')
        return summary

    async def _emit_peer_portrait_event(self, db: AsyncSession, *, owner_id: str, portrait: dict[str, Any]) -> None:
        """P3 占位：合成后发 memory.peer_portrait.upserted 下行事件（下一切片实现）。"""
        # 由 PEERSYN-P3 实现（此处留空，P2 只负责合成 + upsert，可独立测试）。
        return


def _synthesize_messages(peer_hasn_id: str, baseline: str, facts: list[dict[str, Any]]) -> list[dict[str, str]]:
    """构造合成提示词：把跨分身的 peer 事实（+现有画像基线）合成一份画像叙述。"""
    lines: list[str] = []
    for f in facts:
        predicate = str(f.get('predicate') or '').strip()
        obj = f.get('object')
        obj_text = obj if isinstance(obj, str) else _compact_json(obj)
        if predicate:
            lines.append(f'- {predicate}: {obj_text}')
    joined = '\n'.join(lines) if lines else '（暂无）'
    return [
        {
            'role': 'system',
            'content': (
                '你负责维护「主人视角下对某位对方的画像」，供主人的多个 AI 分身在与该对方对话时共享。'
                '把下面这些「主人名下各分身对同一对方积累的观察事实」合并成一份连贯、简洁、可长期复用的画像。'
                '\n要求：'
                '\n- 以第三人称客观描述该对方是谁、性格/偏好/沟通风格、与主人的关系与互动要点；'
                '\n- 去重、消解冲突（较新的观察更可信）；只陈述由观察支撑的事实，不臆测、不编造；'
                '\n- 若给了「现有画像」，在其基础上增量演进：保留仍成立的事实，用新观察更新或补充，不推倒重来；'
                '\n- 用中文，纯叙述文字，不要 Markdown 标题/列表符号/代码围栏、不要解释性前后缀；'
                f'\n- 总长控制在 {_MAX_PORTRAIT_CHARS} 字符以内。'
                '\n只输出画像正文。'
            ),
        },
        {
            'role': 'user',
            'content': (
                f'对方标识：{peer_hasn_id}\n\n'
                f'现有画像（在其基础上演进；首次为空）：\n{baseline or "(空)"}\n\n'
                f'各分身积累的观察事实：\n{joined}\n\n'
                '返回合并演进后的画像正文：'
            ),
        },
    ]


def _compact_json(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _serialize(row: PeerPortrait) -> dict[str, Any]:
    return {
        'owner_id': row.owner_id,
        'peer_hasn_id': row.peer_hasn_id,
        'peer_kind': row.peer_kind,
        'portrait_text': row.portrait_text,
        'language': row.language,
        'version': int(row.version or 1),
        'revised_by': row.revised_by,
        'source_fact_count': int(row.source_fact_count or 0),
        'last_synthesized_at': row.last_synthesized_at,
        'last_interaction_at': row.last_interaction_at,
        'token_count': int(row.token_count or 0),
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


peer_portrait_service = PeerPortraitService()
