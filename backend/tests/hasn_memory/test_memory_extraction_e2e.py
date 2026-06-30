"""单一云端记忆提取 worker 真实 PG E2E（禁 mock，doc16 Phase C2）。

验证云端提取管线（消息上行 + 会话摘要 → 提取 → 闸门 → 云端权威记忆 semantic_fact）：

纯函数闸门（无需 DB，永远运行）：
- `_parse_candidates` 宽容解析（代码围栏 / 顶层 list / 多键名 / 标量包 text / 缺省置信度）；
- `_policy_decision` 闸门（低置信度丢弃 / 拒 agent_self / 拒缺 subject 的 peer / 放行 owner·peer·world）。

真实 PG 全链路（活体 DB 15432，无 DB 跳过不伪造）：
- 只取 owner 本人撰写文本（from_type=1）+ 工作会话摘要，**跳过 agent verbose**（from_type=2 不喂）；
- 注入确定性 LLM 桩 → 候选经闸门 → 仅 accept 的写 semantic_fact（agent_self / 低置信度 / 缺 subject 的 peer 不落库）；
- 写入事实不绑分身（agent_id 必空）、带来源 msg 引用；游标按 message id 单调推进；
- 幂等：游标推进后重跑不重复提取（不再调 LLM、零新写入）；
- owner 隔离：定向 owner_ids 不波及其他 owner（无事实、无游标）。

运行：
    DATABASE_PORT=15432 pytest backend/tests/hasn_memory/test_memory_extraction_e2e.py
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend.app.hasn_memory.service.memory_extraction_service import (
    _parse_candidates,
    _policy_decision,
    memory_extraction_service,
)
from backend.utils.timezone import timezone

# ── 确定性候选桩：覆盖 accept / 低置信度丢弃 / agent_self 拒 / 缺 subject 的 peer 拒 ──────────
_OWNER_TEXTS = (
    '我喜欢简洁直接的回复，别绕弯子。',
    '我现在全职在做唤星 HASN 平台。',
)
_AGENT_VERBOSE = '好的主人，我这就为你详细分析三种方案的优劣并逐条展开论证……（大段分身输出）'
_SESSION_SUMMARY = '本次工作会话：完成了云端记忆提取 worker 的设计与落地。'

_CANDIDATES_JSON = json.dumps(
    {
        'candidates': [
            {
                'subject_kind': 'owner',
                'scope_kind': 'global',
                'predicate': 'prefers_concise_replies',
                'object_json': {'text': '喜欢简洁直接的回复'},
                'confidence': 0.92,
            },
            {
                'subject_kind': 'owner',
                'scope_kind': 'global',
                'predicate': 'works_on_project',
                'object_json': {'text': '正在做唤星 HASN 平台'},
                'confidence': 0.88,
            },
            {  # agent_self → PolicyGate 拒（C2 不抽分身自身记忆）
                'subject_kind': 'agent_self',
                'scope_kind': 'global',
                'predicate': 'likes_tool',
                'object_json': {'text': 'ripgrep'},
                'confidence': 0.95,
            },
            {  # peer 带 subject_id → accept
                'subject_kind': 'peer',
                'subject_id': 'h_peer_invest_x',
                'scope_kind': 'global',
                'predicate': 'is_investor',
                'object_json': {'text': '某基金投资人'},
                'confidence': 0.8,
            },
            {  # peer 缺 subject_id → PolicyGate 拒
                'subject_kind': 'peer',
                'scope_kind': 'global',
                'predicate': 'unknown_peer',
                'object_json': {'text': '缺 subject_id'},
                'confidence': 0.8,
            },
            {  # 置信度 < 0.4 → 直接丢弃
                'subject_kind': 'owner',
                'scope_kind': 'global',
                'predicate': 'vague_guess',
                'object_json': {'text': '也许喜欢茶'},
                'confidence': 0.2,
            },
        ]
    },
    ensure_ascii=False,
)


# ── 纯函数闸门（无需 DB）─────────────────────────────────────────────────────────
def test_parse_candidates_tolerant() -> None:
    """宽容解析：代码围栏 / 多键名 / 标量包 {text} / 顶层 list / 缺省置信度 / 丢弃残缺。"""
    fenced = '```json\n{"candidates":[{"key":"likes_tea","value":"乌龙","confidence":0.9}]}\n```'
    out = _parse_candidates(fenced)
    assert len(out) == 1
    assert out[0]['predicate'] == 'likes_tea'
    assert out[0]['object'] == {'text': '乌龙'}  # 标量 value 包成 {text}
    assert out[0]['confidence'] == pytest.approx(0.9)

    top_list = '[{"predicate":"p","object_json":{"text":"x"}}]'
    out2 = _parse_candidates(top_list)
    assert out2[0]['subject_kind'] == 'owner'  # 缺省主体 owner
    assert out2[0]['confidence'] == pytest.approx(0.6)  # 缺省置信度

    assert _parse_candidates('not json at all') == []
    assert _parse_candidates('{"candidates":[{"object":"无 predicate"}]}') == []  # 缺 predicate 丢弃


def test_policy_gate_decisions() -> None:
    """PolicyGate：低置信度丢弃 / 拒 agent_self / 拒缺 subject 的 peer / 放行 owner·peer·world。"""
    assert _policy_decision({'subject_kind': 'owner', 'confidence': 0.2}) == 'reject_low_conf'
    assert _policy_decision({'subject_kind': 'agent_self', 'confidence': 0.95}) == 'reject_agent_self'
    assert _policy_decision({'subject_kind': 'peer', 'confidence': 0.9}) == 'reject_no_subject'
    assert _policy_decision({'subject_kind': 'peer', 'subject_id': 'h_x', 'confidence': 0.9}) == 'accept'
    assert _policy_decision({'subject_kind': 'owner', 'confidence': 0.9}) == 'accept'
    assert _policy_decision({'subject_kind': 'world', 'subject_id': 'geo:bj', 'confidence': 0.9}) == 'accept'


# ── 真实 PG 全链路 ──────────────────────────────────────────────────────────────
async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


@pytest.mark.asyncio(loop_scope='module')
async def test_extraction_worker_real_db() -> None:
    """真实 PG：seed owner 输入 + 会话摘要 + agent verbose → sweep → 仅 accept 落库；
    跳过 agent verbose；游标单调推进；幂等重跑零新写；owner 隔离。事务真提交，测试后清理。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete, func, select

    from backend.app.hasn.model.hasn_messages import HasnMessages
    from backend.app.hasn.model.hasn_sessions import HasnSessions
    from backend.app.hasn_memory.model.memory_extraction_cursor import MemoryExtractionCursor
    from backend.app.hasn_memory.model.semantic_fact import SemanticFact
    from backend.database.db import async_db_session

    owner = f'h_extract_{uuid.uuid4().hex[:16]}'
    other_owner = f'h_extract_o_{uuid.uuid4().hex[:14]}'
    agent_id = f'a_extract_{uuid.uuid4().hex[:14]}'
    conv = uuid.uuid4()
    other_conv = uuid.uuid4()
    sess_id = f's_{uuid.uuid4().hex[:20]}'

    captured: dict[str, str | None] = {'transcript': None}
    calls = {'n': 0}

    async def stub(messages: list[dict[str, str]]) -> str:  # noqa: RUF029 — 注入契约要求 awaitable
        calls['n'] += 1
        captured['transcript'] = messages[-1]['content']
        return _CANDIDATES_JSON

    try:
        # seed：owner 文本消息 + agent verbose（from_type=2，应被跳过）+ 另一 owner 消息 + owner 会话摘要
        async with async_db_session.begin() as db:
            for txt in _OWNER_TEXTS:
                db.add(
                    HasnMessages(
                        conversation_id=conv,
                        from_id=owner,
                        from_type=1,
                        to_id=agent_id,
                        to_type=2,
                        content_type=1,
                        content={'text': txt},
                    )
                )
            db.add(
                HasnMessages(
                    conversation_id=conv,
                    from_id=agent_id,
                    from_type=2,
                    to_id=owner,
                    to_type=1,
                    content_type=1,
                    content={'text': _AGENT_VERBOSE},
                )
            )
            db.add(
                HasnMessages(
                    conversation_id=other_conv,
                    from_id=other_owner,
                    from_type=1,
                    to_id=agent_id,
                    to_type=2,
                    content_type=1,
                    content={'text': '别的用户随便说的一句话'},
                )
            )
            sess = HasnSessions(
                session_id=sess_id,
                owner_id=owner,
                hasn_id=agent_id,
                session_kind='task',
                session_scope='summary_only',
                origin_type='system',  # chk_origin_type 拒空串，须给合法来源
                summary_checkpoint_json={'summary': _SESSION_SUMMARY},
            )
            sess.updated_time = timezone.now()  # init=False，需显式置摘要水位才被拾取
            db.add(sess)

        # 定向 sweep（只扫 owner，不波及 other_owner 真实数据）
        result = await memory_extraction_service.sweep_extractions(owner_ids=[owner], llm_complete=stub)
        assert result['candidate_owners'] == 1
        assert result['processed'] == 1
        assert result['written'] == 3  # 2 owner + 1 peer-with-id（agent_self/低置信/缺 subject 不写）
        assert result['failed'] == 0
        assert calls['n'] == 1

        # transcript：owner 文本 + 会话摘要在内，agent verbose 不在（跳过 agent verbose）
        transcript = captured['transcript']
        assert transcript is not None
        for txt in _OWNER_TEXTS:
            assert txt in transcript
        assert _SESSION_SUMMARY in transcript
        assert _AGENT_VERBOSE not in transcript
        assert '主人说' in transcript
        assert '工作会话摘要' in transcript

        # 事实落库：恰 3 条；无 agent_self；agent_id 必空；peer subject_id 正确；owner subject_id=owner；带来源
        async with async_db_session() as db:
            rows = (await db.execute(select(SemanticFact).where(SemanticFact.owner_id == owner))).scalars().all()
        assert len(rows) == 3
        assert {r.predicate for r in rows} == {'prefers_concise_replies', 'works_on_project', 'is_investor'}
        assert all(r.subject_kind != 'agent_self' for r in rows)
        assert all(r.agent_id is None for r in rows)
        peer = next(r for r in rows if r.predicate == 'is_investor')
        assert peer.subject_kind == 'peer'
        assert peer.subject_id == 'h_peer_invest_x'
        owner_fact = next(r for r in rows if r.predicate == 'prefers_concise_replies')
        assert owner_fact.subject_kind == 'owner'
        assert owner_fact.subject_id == owner
        assert json.loads(peer.source_refs_json)  # 非空 msg 来源引用

        # 游标推进到 owner 最大消息 id + 摘要水位推进 + 累计写入计数
        async with async_db_session() as db:
            cur = await db.get(MemoryExtractionCursor, owner)
            max_msg_id = (
                await db.execute(
                    select(func.max(HasnMessages.id)).where(
                        HasnMessages.from_id == owner, HasnMessages.from_type == 1
                    )
                )
            ).scalar()
        assert cur is not None
        assert int(cur.last_message_id) == int(max_msg_id)
        assert int(cur.facts_written) == 3
        assert int(cur.last_session_checkpoint_at) > 0

        # 幂等：游标已推进 → 无新消息，不再调 LLM、零新写入
        result2 = await memory_extraction_service.sweep_extractions(owner_ids=[owner], llm_complete=stub)
        assert result2['candidate_owners'] == 0
        assert result2['written'] == 0
        assert calls['n'] == 1  # 桩未被二次调用

        # owner 隔离：other_owner 未被处理 → 无事实、无游标
        async with async_db_session() as db:
            other_facts = (
                await db.execute(select(SemanticFact).where(SemanticFact.owner_id == other_owner))
            ).scalars().all()
            other_cur = await db.get(MemoryExtractionCursor, other_owner)
        assert other_facts == []
        assert other_cur is None
    finally:
        owners = [owner, other_owner]
        async with async_db_session.begin() as db:
            await db.execute(delete(SemanticFact).where(SemanticFact.owner_id.in_(owners)))
            await db.execute(delete(MemoryExtractionCursor).where(MemoryExtractionCursor.owner_id.in_(owners)))
            await db.execute(delete(HasnMessages).where(HasnMessages.from_id.in_([*owners, agent_id])))
            await db.execute(delete(HasnSessions).where(HasnSessions.owner_id == owner))
