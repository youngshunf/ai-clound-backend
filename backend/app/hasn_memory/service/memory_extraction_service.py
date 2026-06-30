"""单一云端记忆提取服务（doc16 Phase C2）。

记忆提取从「边缘 + 云端两路」收敛为**单一云端管线**：消息（Phase A 已落 `hasn_messages`）
与工作会话摘要（Phase B `summary_checkpoint_json`）进云端后，由本服务统一提取语义事实，
写入云端权威记忆 `hasn_memory.semantic_fact`（经 `semantic_fact_service`）。

强约束（doc16 §10）对齐：
- **输入只取 owner 输入 + 任务结果/摘要，跳过 agent verbose**：消息侧仅取 `from_type=1`（人类，
  即主人本人撰写）的文本消息；agent 大段输出（`from_type=2`）压根不喂。agent 自身记忆靠分身
  主动 `hasn.memory.save`，**本提取不写 `agent_self`**（PolicyGate 拒）。
- **平台廉价模型、平台吸收成本**：经 `llm_client`（平台 base_url/api_key），不记主人配额。
- **candidate schema + PolicyGate + confidence gate**（doc 03 §6/§7/§9）：宽容解析候选 →
  置信度闸（<0.4 丢弃）→ PolicyGate（拒 agent_self / 缺 subject 的 peer）→ 写云端权威记忆。
- **增量 + 幂等**：每 owner 一行 `memory_extraction_cursor` 水位，按 `hasn_messages.id` 单调
  推进；重复触发不重复提取同一窗口。

LLM 边界（`llm_complete`）可注入：真实路径走 `llm_client`；测试注入确定性桩，真实联调
DB 读 → 宽容解析 → 闸门 → DB 写 全流程（与 `owner_memory_service.merge_owner_memory` 同范式）。
"""
from __future__ import annotations

import json

from time import time
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_sessions import HasnSessions
from backend.app.hasn_memory.model.memory_extraction_cursor import MemoryExtractionCursor
from backend.app.hasn_memory.service.semantic_fact_service import semantic_fact_service
from backend.common.log import log
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    LlmComplete = Callable[[list[dict[str, str]]], Awaitable[str]]

# 闸门阈值（doc 03 §7.4）：低于 _DISCARD 直接丢弃；缺省置信度 _DEFAULT（与本地 crate 一致）。
_DISCARD_CONFIDENCE = 0.4
_DEFAULT_CANDIDATE_CONFIDENCE = 0.6
# 单次提取的批量上限（messages / sessions / candidates），防单 owner 巨量拖垮一轮 sweep。
_MAX_MESSAGES_PER_RUN = 80
_MAX_SESSIONS_PER_RUN = 20
_MAX_CANDIDATES_PER_RUN = 40
_EXTRACT_MAX_TOKENS = 1500
_SWEEP_MAX_OWNERS = 50

# 候选键宽容（doc 03 §6 + 本地 memory_extract.rs parse_candidates 同口径）。
_PREDICATE_KEYS = ('predicate', 'key', 'attribute', 'relation')
_OBJECT_KEYS = ('object_json', 'object', 'payload', 'value')
_TEXT_KEYS = ('text', 'summary', 'content')

# 提取提示词：忠实移植本地 EXTRACTION_SYSTEM_PROMPT，适配云端单一管线（输入=主人输入+会话摘要，
# 不抽 agent_self——分身自身记忆靠主动 save）。
_EXTRACTION_SYSTEM_PROMPT = (
    '你是唤星（HASN）的记忆提取器。从给定的「主人输入 / 工作会话摘要」片段中，抽取值得长期记住的'
    '「语义事实」：主人稳定的偏好、身份与属性、长期目标、与某人/某事的关系，以及项目或客观世界的事实。'
    '只抽取明确、可长期复用的信息；闲聊寒暄、一次性指令、临时情绪与瞬时状态不要抽取，也不要臆测或编造。'
    '不要抽取关于「你自己（分身）」的事实（agent_self）——那由分身自己记录。\n\n'
    '只输出一个 JSON 对象，格式如下：\n'
    '{"candidates":[{\n'
    '  "subject_kind":"owner|peer|world",   // 事实主体（不含 agent_self）\n'
    '  "subject_id":"<可选，peer/world 的 id；owner 留空自动补>",\n'
    '  "scope_kind":"global|conversation|topic",   // 长期通用→global；仅当前语境→conversation\n'
    '  "predicate":"<小写蛇形动词短语，如 prefers_concise_replies / works_on_project / lives_in>",\n'
    '  "object_json":{"text":"<事实内容>"},   // 结构化对象，至少含 text\n'
    '  "confidence":0.0,   // 明确事实≥0.8，含糊给 0.4~0.6\n'
    '  "rationale":"<可选，简短依据>"\n'
    '}]}\n\n'
    '规则：\n'
    '- 没有任何值得沉淀的事实时，必须返回 {"candidates":[]}，不要编造。\n'
    '- predicate 用小写蛇形动词短语；object_json 必须是对象，最简写法 {"text":"..."}。\n'
    '- 明确、稳定的事实给 confidence ≥ 0.8；不确定的给 0.4~0.6。\n'
)


def _now_ms() -> int:
    return int(time() * 1000)


async def _default_llm_complete(messages: list[dict[str, str]]) -> str:
    """默认 LLM 提取调用：平台 `llm_client`（平台 base_url/api_key，平台吸收成本，不记主人配额）。

    走 settings 默认 failover 链（廉价 flash 模型优先），请求 JSON 对象输出。
    """
    from backend.common.llm import llm_client

    return await llm_client.complete(
        messages,
        response_format={'type': 'json_object'},
        max_tokens=_EXTRACT_MAX_TOKENS,
    )


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in _TEXT_KEYS:
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ''
    return str(value)


def _extract_predicate(item: dict[str, Any]) -> str | None:
    for key in _PREDICATE_KEYS:
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_object(item: dict[str, Any]) -> Any:
    """取候选 object（宽容多键名；标量包成 {"text": ...} 而非丢弃，对齐本地 parse）。"""
    for key in _OBJECT_KEYS:
        val = item.get(key)
        if isinstance(val, (dict, list)):
            return val
        if isinstance(val, str) and val.strip():
            return {'text': val.strip()}
    for key in _TEXT_KEYS:
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return {'text': val.strip()}
    return None


def _parse_candidates(raw: str) -> list[dict[str, Any]]:
    """宽容解析 LLM 输出（容忍 ```json 代码围栏 / 顶层 list / candidates 包裹）。"""
    text = (raw or '').strip()
    if text.startswith('```'):
        # 去掉 ```json ... ``` 围栏
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        log.warning('memory extraction: LLM 输出非合法 JSON，本轮跳过')
        return []
    items = parsed.get('candidates') if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        predicate = _extract_predicate(item)
        obj = _extract_object(item)
        if not predicate or obj is None:
            continue
        try:
            confidence = float(item.get('confidence', _DEFAULT_CANDIDATE_CONFIDENCE))
        except (TypeError, ValueError):
            confidence = _DEFAULT_CANDIDATE_CONFIDENCE
        out.append(
            {
                'subject_kind': str(item.get('subject_kind') or 'owner'),
                'subject_id': item.get('subject_id') or None,
                'scope_kind': str(item.get('scope_kind') or 'global'),
                'predicate': predicate,
                'object': obj,
                'confidence': confidence,
                'rationale': item.get('rationale') or None,
            }
        )
        if len(out) >= _MAX_CANDIDATES_PER_RUN:
            break
    return out


def _policy_decision(candidate: dict[str, Any]) -> str:
    """PolicyGate（doc 03 §9 的云端提取子集）：返回 accept / 拒因。

    - 置信度 < _DISCARD_CONFIDENCE → reject_low_conf（doc 03 §7.4 直接丢弃）。
    - subject_kind=agent_self → reject_agent_self（C2 不抽分身自身记忆，归分身主动 save）。
    - subject_kind=peer 缺 subject_id → reject_no_subject。
    - 其余（owner/peer-with-id/world）→ accept。
    """
    confidence = candidate.get('confidence', _DEFAULT_CANDIDATE_CONFIDENCE)
    if confidence < _DISCARD_CONFIDENCE:
        return 'reject_low_conf'
    subject_kind = candidate.get('subject_kind')
    if subject_kind == 'agent_self':
        return 'reject_agent_self'
    if subject_kind == 'peer' and not candidate.get('subject_id'):
        return 'reject_no_subject'
    return 'accept'


def _epoch_ms_of(value: Any) -> int:
    """timestamptz / datetime → epoch ms（用于会话摘要水位比较）。"""
    if value is None:
        return 0
    try:
        return int(value.timestamp() * 1000)
    except (AttributeError, OverflowError, ValueError):
        return 0


class MemoryExtractionService:
    """单一云端记忆提取（owner 隔离，增量 + 幂等）。"""

    async def extract_for_owner(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        llm_complete: LlmComplete | None = None,
    ) -> dict[str, Any]:
        """对单个 owner 跑一次提取：读新消息 + 新会话摘要 → 提取 → 闸门 → 写云端权威记忆 → 推进水位。

        在调用方提供的事务内执行（写库随该事务提交）。owner_id 由调用方（凭据 / sweep）强制。
        返回 {messages, summaries, candidates, written, discarded_low_conf, policy_rejected, ...}。
        """
        cursor = await db.get(MemoryExtractionCursor, owner_id)
        last_message_id = int(cursor.last_message_id) if cursor else 0
        last_checkpoint_at = int(cursor.last_session_checkpoint_at) if cursor else 0

        messages, max_message_id = await self._fetch_owner_messages(db, owner_id, last_message_id)
        summaries, max_checkpoint_at = await self._fetch_session_summaries(db, owner_id, last_checkpoint_at)

        summary = {
            'owner_id': owner_id,
            'messages': len(messages),
            'summaries': len(summaries),
            'candidates': 0,
            'written': 0,
            'discarded_low_conf': 0,
            'policy_rejected': 0,
        }

        transcript = self._build_transcript(messages, summaries)
        if transcript:
            complete = llm_complete or _default_llm_complete
            raw = await complete(
                [
                    {'role': 'system', 'content': _EXTRACTION_SYSTEM_PROMPT},
                    {'role': 'user', 'content': transcript},
                ]
            )
            candidates = _parse_candidates(raw)
            summary['candidates'] = len(candidates)
            source_refs = [f'msg:{m["id"]}' for m in messages]
            for cand in candidates:
                decision = _policy_decision(cand)
                if decision == 'reject_low_conf':
                    summary['discarded_low_conf'] += 1
                    continue
                if decision != 'accept':
                    summary['policy_rejected'] += 1
                    continue
                await semantic_fact_service.save_fact(
                    db,
                    owner_id=owner_id,
                    agent_id=None,  # 非 agent_self；不绑分身
                    predicate=cand['predicate'],
                    object_value=cand['object'],
                    subject_kind=cand['subject_kind'],
                    subject_id=cand.get('subject_id'),
                    confidence=cand['confidence'],
                    scope_kind=cand.get('scope_kind') or 'global',
                    source_refs=source_refs,
                    rationale=cand.get('rationale'),
                )
                summary['written'] += 1

        # 推进水位（即使本轮无候选也推进，避免重复重读同一窗口）。
        new_last_message_id = max(last_message_id, max_message_id)
        new_checkpoint_at = max(last_checkpoint_at, max_checkpoint_at)
        await self._advance_cursor(
            db,
            owner_id=owner_id,
            cursor=cursor,
            last_message_id=new_last_message_id,
            last_checkpoint_at=new_checkpoint_at,
            facts_written=summary['written'],
        )
        return summary

    async def _fetch_owner_messages(
        self, db: AsyncSession, owner_id: str, last_message_id: int
    ) -> tuple[list[dict[str, Any]], int]:
        """取 owner 本人撰写、未提取过的文本消息（from_type=1 人类、content_type=1 文本、id 增量）。"""
        stmt = (
            sa.select(HasnMessages.id, HasnMessages.content)
            .where(
                HasnMessages.from_id == owner_id,
                HasnMessages.from_type == 1,
                HasnMessages.content_type == 1,
                HasnMessages.id > last_message_id,
            )
            .order_by(HasnMessages.id.asc())
            .limit(_MAX_MESSAGES_PER_RUN)
        )
        rows = (await db.execute(stmt)).all()
        messages: list[dict[str, Any]] = []
        max_id = last_message_id
        for row in rows:
            text = row.content.get('text', '') if isinstance(row.content, dict) else ''
            max_id = max(max_id, int(row.id))
            if isinstance(text, str) and text.strip():
                messages.append({'id': int(row.id), 'text': text.strip()})
        return messages, max_id

    async def _fetch_session_summaries(
        self, db: AsyncSession, owner_id: str, last_checkpoint_at: int
    ) -> tuple[list[dict[str, Any]], int]:
        """取 owner 名下、有摘要且摘要水位推进过的工作会话（summary_only，禁全量转录）。"""
        stmt = (
            sa.select(HasnSessions.session_id, HasnSessions.summary_checkpoint_json, HasnSessions.updated_time)
            .where(
                HasnSessions.owner_id == owner_id,
                HasnSessions.summary_checkpoint_json.isnot(None),
            )
            .order_by(HasnSessions.updated_time.asc())
            .limit(_MAX_SESSIONS_PER_RUN * 2)
        )
        rows = (await db.execute(stmt)).all()
        summaries: list[dict[str, Any]] = []
        max_checkpoint = last_checkpoint_at
        for row in rows:
            checkpoint_ms = _epoch_ms_of(row.updated_time)
            if checkpoint_ms <= last_checkpoint_at:
                continue
            text = _coerce_text(row.summary_checkpoint_json)
            max_checkpoint = max(max_checkpoint, checkpoint_ms)
            if text.strip():
                summaries.append({'session_id': row.session_id, 'text': text.strip()})
            if len(summaries) >= _MAX_SESSIONS_PER_RUN:
                break
        return summaries, max_checkpoint

    def _build_transcript(self, messages: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> str:
        lines: list[str] = [f'主人说：{m["text"]}' for m in messages]
        lines.extend(f'工作会话摘要：{s["text"]}' for s in summaries)
        return '\n'.join(lines).strip()

    async def _advance_cursor(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        cursor: MemoryExtractionCursor | None,
        last_message_id: int,
        last_checkpoint_at: int,
        facts_written: int,
    ) -> None:
        now = _now_ms()
        if cursor is None:
            db.add(
                MemoryExtractionCursor(
                    owner_id=owner_id,
                    last_message_id=last_message_id,
                    last_session_checkpoint_at=last_checkpoint_at,
                    facts_written=facts_written,
                    last_run_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            cursor.last_message_id = last_message_id
            cursor.last_session_checkpoint_at = last_checkpoint_at
            cursor.facts_written = int(cursor.facts_written) + facts_written
            cursor.last_run_at = now
            cursor.updated_at = now
        await db.flush()

    async def sweep_extractions(
        self,
        *,
        max_owners: int = _SWEEP_MAX_OWNERS,
        owner_ids: list[str] | None = None,
        llm_complete: LlmComplete | None = None,
    ) -> dict[str, Any]:
        """扫描有未提取消息的 owner，逐 owner 独立事务跑一次提取（触发于消息上行 / 会话摘要事件）。

        ``owner_ids`` 可选：限定只扫这些 owner（运维定向 / 测试隔离，不波及其他 owner 真实数据）。
        逐 owner 独立 try：单 owner 失败（LLM 挂等）不拖垮其余，下轮再试（零 fake）。
        返回 {candidates_owners, processed, written, failed}。
        """
        async with async_db_session() as db:
            candidate_query = (
                sa.select(HasnMessages.from_id)
                .outerjoin(MemoryExtractionCursor, MemoryExtractionCursor.owner_id == HasnMessages.from_id)
                .where(
                    HasnMessages.from_type == 1,
                    HasnMessages.content_type == 1,
                    HasnMessages.id > sa.func.coalesce(MemoryExtractionCursor.last_message_id, 0),
                )
                .group_by(HasnMessages.from_id)
                .order_by(HasnMessages.from_id.asc())
                .limit(max(1, max_owners))
            )
            if owner_ids is not None:
                candidate_query = candidate_query.where(HasnMessages.from_id.in_(owner_ids))
            owners = list((await db.execute(candidate_query)).scalars().all())

        result = {'candidate_owners': len(owners), 'processed': 0, 'written': 0, 'failed': 0}
        for owner_id in owners:
            try:
                async with async_db_session.begin() as db:
                    outcome = await self.extract_for_owner(db, owner_id=owner_id, llm_complete=llm_complete)
                result['processed'] += 1
                result['written'] += outcome['written']
            except Exception as exc:  # noqa: PERF203 — 每 owner 独立 try：单 owner 失败不拖垮整轮
                result['failed'] += 1
                log.warning(f'memory extraction failed for {owner_id}: {exc}')
        return result


memory_extraction_service = MemoryExtractionService()
