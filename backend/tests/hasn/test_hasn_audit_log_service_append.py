"""Phase 7: hasn_audit_log_service.append 因果链单元测试 (5 个 behavior)。

被测目标：append() 在单 flush 内 SELECT 最新行 + sha256(prev_hash + canonical_json) +
INSERT 新行；actor_id 作链作用域。

数据隔离：使用 conftest 中的 in-memory aiosqlite + AuditLogStub，monkeypatch 替换
service 模块内引用的 HasnAuditLog ORM。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from sqlalchemy import select

from backend.app.hasn.service.hasn_audit_log_service import hasn_audit_log_service
from backend.tests.hasn.conftest import AuditLogStub

pytestmark = pytest.mark.asyncio


async def test_append_creates_row_with_hash(db_session) -> None:
    """Test 1: 单条 append → 新行 hash_chain 长度 64 hex，prev_log_id 为 None。"""
    entry = await hasn_audit_log_service.append(
        db=db_session,
        actor_id='test_append_u1',
        actor_type='human',
        action='permission_decision',
        details={'decision': 'allow', 'reason': 'unit-test'},
    )
    assert entry is not None
    assert entry.prev_log_id is None
    assert isinstance(entry.hash_chain, str)
    assert len(entry.hash_chain) == 64
    int(entry.hash_chain, 16)  # 验证全部为有效十六进制字符


async def test_chain_is_continuous(db_session) -> None:
    """Test 2: 同 actor_id 连续 3 条 → prev_log_id 链式指向，hash 含前驱。"""
    e1 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_chain_u1', action='a1',
        details={'k': 1},
    )
    e2 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_chain_u1', action='a2',
        details={'k': 2},
    )
    e3 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_chain_u1', action='a3',
        details={'k': 3},
    )

    assert e1.prev_log_id is None
    assert e2.prev_log_id == e1.id
    assert e3.prev_log_id == e2.id

    # 验证 e2.hash_chain == sha256(e1.hash_chain + canonical_json({'k': 2}))
    expected_payload = json.dumps({'k': 2}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    expected_hash = hashlib.sha256(
        (e1.hash_chain + expected_payload).encode('utf-8')
    ).hexdigest()
    assert e2.hash_chain == expected_hash


async def test_chain_scoped_per_actor(db_session) -> None:
    """Test 3: 不同 actor_id 链独立 (b 的第一条 prev_log_id is None)。"""
    a1 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_scope_a', action='x', details={'n': 1},
    )
    await hasn_audit_log_service.append(
        db=db_session, actor_id='test_scope_a', action='x', details={'n': 2},
    )
    b1 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_scope_b', action='x', details={'n': 1},
    )
    b2 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_scope_b', action='x', details={'n': 2},
    )

    assert a1.prev_log_id is None
    assert b1.prev_log_id is None  # 独立链根
    assert b2.prev_log_id == b1.id  # b 链内连续


async def test_canonical_payload_deterministic(db_session) -> None:
    """Test 4: 字典 key 顺序不影响 canonical hash (sort_keys=True)。"""
    # 第一条 dict 用 {x, y}，第二条 dict 用 {y, x} 但语义相同
    e1 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_canon_a', action='t',
        details={'x': 1, 'y': 2},
    )
    # 在新 actor 链上 append，使其 prev_hash == ''
    e2 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_canon_b', action='t',
        details={'y': 2, 'x': 1},
    )
    # 两条 root 节点 (prev_hash='') + 同语义 canonical payload → hash 应完全相等
    assert e1.hash_chain == e2.hash_chain


async def test_findings_defaults_empty_list_severity_none(db_session) -> None:
    """Test 5: append 默认 findings == [] 且 severity is None。"""
    entry = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_defaults', action='ping', details={},
    )
    assert entry.findings == []
    assert entry.severity is None

    # 显式 severity 也应保留
    entry2 = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_defaults_2', action='ping',
        details={}, severity='warning',
    )
    assert entry2.severity == 'warning'


async def test_persisted_row_matches_returned_object(db_session) -> None:
    """Bonus: 写入后 SELECT 验证持久化的字段一致 (hash_chain / actor_id 入库正确)。"""
    entry = await hasn_audit_log_service.append(
        db=db_session, actor_id='test_persist',
        action='audit_persist_check', details={'foo': 'bar'},
    )
    await db_session.flush()
    row = (await db_session.execute(
        select(AuditLogStub).where(AuditLogStub.id == entry.id)
    )).scalar_one()
    assert row.actor_id == 'test_persist'
    assert row.action == 'audit_persist_check'
    assert row.hash_chain == entry.hash_chain
    assert row.details == {'foo': 'bar'}
