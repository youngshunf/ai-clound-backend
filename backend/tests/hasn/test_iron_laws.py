"""Phase 7: iron_laws 七铁律单元测试 (覆盖 RESEARCH §B1 + 设计 01 §3.1 顺序)。

被测目标：check_iron_laws(db, sender, receiver, envelope) 按 ①..⑥ 顺序检查；
若命中铁律则返回 DecisionResult(decision, reason, matched_rule)；全部 pass 返回 None。
铁律⑦审计由 permission_engine 负责，不在本文件覆盖范围。

依赖隔离：DB 不需要 (传 None)；redis_client.pipeline() 通过 monkeypatch 替换为 AsyncMock。
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.hasn.constants import ALLOW, CONFIRM, DENY, SCOPE_LTD

pytestmark = pytest.mark.asyncio


def _envelope(**overrides: Any) -> dict[str, Any]:
    """构造一个默认 envelope 骨架，便于各 test 微调。"""
    base = {
        'msg_type': 'message',
        'content': {'body': 'hello'},
        'relation_type': 'social',
        'metadata': {},
        'from_entity_type': 'human',
    }
    base.update(overrides)
    return base


def _patch_redis_pass(monkeypatch) -> None:
    """让 _check_rate_limit 的 redis 滑窗返回 5 条 (低于 100，pass 铁律⑥)。"""
    from backend.app.hasn.service import iron_laws as mod

    pipe = MagicMock()
    pipe.zremrangebyscore = MagicMock(return_value=pipe)
    pipe.zcard = MagicMock(return_value=pipe)
    pipe.zadd = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[0, 5, 1, True])  # 第二项=zcard 结果

    monkeypatch.setattr(mod.redis_client, 'pipeline', lambda: pipe, raising=False)


# ── ① Agent 身份透明 ──
async def test_iron_law_1_agent_must_declare_identity(monkeypatch) -> None:
    from backend.app.hasn.service.iron_laws import check_iron_laws

    _patch_redis_pass(monkeypatch)
    sender = {'hasn_id': 'a_001', 'entity_type': 'agent'}
    receiver = {'hasn_id': 'h_002', 'entity_type': 'human'}
    env = _envelope(from_entity_type=None)  # agent 但未声明

    result = await check_iron_laws(None, sender, receiver, env)
    assert result is not None
    assert result.decision == DENY
    assert result.matched_rule == 'iron_law_1'
    assert result.error_code == 2014


# ── ② Owner 绝对控制权 ──
async def test_iron_law_2_owner_controls_own_agent(monkeypatch) -> None:
    from backend.app.hasn.service.iron_laws import check_iron_laws

    _patch_redis_pass(monkeypatch)
    sender = {'hasn_id': 'h_owner_x', 'entity_type': 'human'}
    receiver = {'hasn_id': 'a_my_agent', 'owner_id': 'h_owner_x', 'entity_type': 'agent'}

    result = await check_iron_laws(None, sender, receiver, _envelope())
    assert result is not None
    assert result.decision == ALLOW
    assert result.matched_rule == 'iron_law_2'


# ── ③ 承诺需人类确认 ──
async def test_iron_law_3_commitment_requires_confirm(monkeypatch) -> None:
    from backend.app.hasn.service.iron_laws import check_iron_laws

    _patch_redis_pass(monkeypatch)
    sender = {'hasn_id': 'h_a', 'entity_type': 'human'}
    receiver = {'hasn_id': 'h_b', 'entity_type': 'human'}
    env = _envelope(metadata={'behavior': 'make_commitment'})

    result = await check_iron_laws(None, sender, receiver, env)
    assert result is not None
    assert result.decision == CONFIRM
    assert result.matched_rule == 'iron_law_3'


# ── ④ 敏感数据禁区 ──
async def test_iron_law_4_sensitive_data_scope_limited(monkeypatch) -> None:
    from backend.app.hasn.service.iron_laws import check_iron_laws

    _patch_redis_pass(monkeypatch)
    sender = {'hasn_id': 'h_a', 'entity_type': 'human'}
    receiver = {'hasn_id': 'h_b', 'entity_type': 'human'}
    env = _envelope(content={'body': 'ok', 'payment_amount': 100, 'bank_account': 'xxx'})

    result = await check_iron_laws(None, sender, receiver, env)
    assert result is not None
    assert result.decision == SCOPE_LTD
    assert result.matched_rule == 'iron_law_4'
    assert result.allowed_fields is not None
    assert 'body' in result.allowed_fields
    assert 'payment_amount' not in result.allowed_fields
    assert 'bank_account' not in result.allowed_fields


# ── ⑤ 通信边界强制 ──
async def test_iron_law_5_commerce_blocks_free_chat(monkeypatch) -> None:
    from backend.app.hasn.service.iron_laws import check_iron_laws

    _patch_redis_pass(monkeypatch)
    sender = {'hasn_id': 'h_a', 'entity_type': 'human'}
    receiver = {'hasn_id': 'a_seller', 'owner_id': 'h_other', 'entity_type': 'agent'}
    env = _envelope(relation_type='commerce', metadata={'behavior': 'free_chat'})

    result = await check_iron_laws(None, sender, receiver, env)
    assert result is not None
    assert result.decision == DENY
    assert result.matched_rule == 'iron_law_5'


# ── ⑥ 频率限制 ──
async def test_iron_law_6_rate_limit_exceeded(monkeypatch) -> None:
    from backend.app.hasn.service import iron_laws as mod

    # 让 zcard 返回 100 (>= 100 触发 deny)
    pipe = MagicMock()
    pipe.zremrangebyscore = MagicMock(return_value=pipe)
    pipe.zcard = MagicMock(return_value=pipe)
    pipe.zadd = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[0, 100, 1, True])
    monkeypatch.setattr(mod.redis_client, 'pipeline', lambda: pipe, raising=False)

    from backend.app.hasn.service.iron_laws import check_iron_laws

    sender = {'hasn_id': 'h_burst', 'entity_type': 'human'}
    receiver = {'hasn_id': 'h_target', 'entity_type': 'human'}
    result = await check_iron_laws(None, sender, receiver, _envelope())
    assert result is not None
    assert result.decision == DENY
    assert result.matched_rule == 'iron_law_6'


# ── 全部 pass ──
async def test_iron_laws_all_pass_returns_none(monkeypatch) -> None:
    _patch_redis_pass(monkeypatch)
    from backend.app.hasn.service.iron_laws import check_iron_laws

    sender = {'hasn_id': 'h_a', 'entity_type': 'human'}
    receiver = {'hasn_id': 'h_b', 'entity_type': 'human'}

    # human-to-human, social, free_chat, no sensitive data → 全部 pass
    result = await check_iron_laws(None, sender, receiver, _envelope())
    assert result is None


# ── ④值检测（C4 安全修复）：content.text 里的真实身份证 / 银行卡，旧实现只查键名会漏 ──
async def test_iron_law_4_detects_id_card_in_text(monkeypatch) -> None:
    _patch_redis_pass(monkeypatch)
    from backend.app.hasn.service.iron_laws import check_iron_laws

    sender = {'hasn_id': 'h_a', 'entity_type': 'human'}
    receiver = {'hasn_id': 'h_b', 'entity_type': 'human'}
    env = _envelope(content={'body': '我的身份证号是 110101199003078515 麻烦记一下'})

    result = await check_iron_laws(None, sender, receiver, env)
    assert result is not None
    assert result.decision == SCOPE_LTD
    assert result.matched_rule == 'iron_law_4'


async def test_iron_law_4_detects_bank_card_luhn(monkeypatch) -> None:
    _patch_redis_pass(monkeypatch)
    from backend.app.hasn.service.iron_laws import check_iron_laws

    sender = {'hasn_id': 'h_a', 'entity_type': 'human'}
    receiver = {'hasn_id': 'h_b', 'entity_type': 'human'}
    # 通过 Luhn 校验的银行卡号（Visa 测试号 4532015112830366）
    env = _envelope(content={'body': '卡号 4532015112830366 帮我转一下'})

    result = await check_iron_laws(None, sender, receiver, env)
    assert result is not None
    assert result.decision == SCOPE_LTD
    assert result.matched_rule == 'iron_law_4'


async def test_iron_law_4_normal_short_numbers_not_flagged(monkeypatch) -> None:
    _patch_redis_pass(monkeypatch)
    from backend.app.hasn.service.iron_laws import check_iron_laws

    sender = {'hasn_id': 'h_a', 'entity_type': 'human'}
    receiver = {'hasn_id': 'h_b', 'entity_type': 'human'}
    # 普通短数字（订单号 / 金额）不应误判为敏感 → 全部 pass（不误杀）
    env = _envelope(content={'body': '订单号 20260626 共 100 元已收到'})

    result = await check_iron_laws(None, sender, receiver, env)
    assert result is None
