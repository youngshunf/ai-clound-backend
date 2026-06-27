"""Phase 7: 七铁律检查器 (设计 01 §3.1)。

职责：在 permission_engine.evaluate() 矩阵之前按 ①..⑥ 顺序检查七铁律 (铁律⑦由
permission_engine 自身保证审计因果链)。命中任一铁律即返回 DecisionResult；
全部 pass 返回 None，evaluate 继续走矩阵。

依赖：constants.py 的 ALLOW/DENY/CONFIRM/SCOPE_LTD 字面量；redis_client (铁律⑥滑窗)。
"""
from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Any

from backend.app.hasn.constants import ALLOW, CONFIRM, DENY, SCOPE_LTD
from backend.common.log import log
from backend.database.redis import redis_client


# ── 决策结果 (immutable，对齐 common/coding-style.md) ──
@dataclass(frozen=True)
class DecisionResult:
    """四态判决结果。decision 字面量与 Rust hasn_core::PermissionDecision 字节对齐。"""

    decision: str  # 'allow' / 'deny' / 'confirm_required' / 'scope_limited'
    reason: str
    matched_rule: str
    allowed_fields: list[str] | None = None
    error_code: int | None = None


# ── 敏感字段白名单 (铁律④) ──
_SENSITIVE_FIELDS: frozenset[str] = frozenset({
    'payment_amount',
    'bank_account',
    'id_card',
    'password',
    'ssn',
    'credit_card',
})

# ── 承诺类行为 (铁律③) ──
_COMMITMENT_BEHAVIORS: frozenset[str] = frozenset({
    'make_commitment',
    'schedule',
    'payment',
})


def _infer_behavior(envelope: dict[str, Any]) -> str:
    """从 envelope.metadata.behavior 推断行为；缺省 free_chat。"""
    return (envelope.get('metadata') or {}).get('behavior', 'free_chat')


# ── 敏感值模式 (铁律④：不止看键名，还要查文本值里的真实敏感数据) ──
_ID_CARD_RE = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')        # 中国身份证 18 位（末位可 X）
_CARD_CANDIDATE_RE = re.compile(r'(?<!\d)\d{13,19}(?!\d)')    # 银行卡 / 信用卡候选 13-19 位


def _luhn_valid(number: str) -> bool:
    """Luhn 校验（银行卡 / 信用卡通用）：降低普通长数字（订单号 / 时间戳）误判为卡号。"""
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iter_text_values(value: Any) -> list[str]:
    """递归收集 content 内所有字符串值（铁律④值级扫描用：含 content.text 等嵌套）。"""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_iter_text_values(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_iter_text_values(v))
    return out


def _contains_sensitive(content: dict[str, Any]) -> bool:
    """铁律④敏感检测：① content 顶层键名命中敏感字段白名单；或 ② 任一文本值
    （含 content.text 等嵌套）匹配身份证 / 通过 Luhn 的银行卡号模式。

    旧实现只查键名（content 含 'id_card' 键），漏掉真实消息把卡号 / 身份证写进
    content.text 文本值的场景——本修复补上值级正则 + Luhn 扫描。
    """
    content = content or {}
    if any(k in _SENSITIVE_FIELDS for k in content.keys()):
        return True
    for text in _iter_text_values(content):
        if _ID_CARD_RE.search(text):
            return True
        for match in _CARD_CANDIDATE_RE.finditer(text):
            if _luhn_valid(match.group()):
                return True
    return False


def _non_sensitive_fields(envelope: dict[str, Any]) -> list[str]:
    content = envelope.get('content') or {}
    return [k for k in content.keys() if k not in _SENSITIVE_FIELDS]


async def _check_rate_limit(sender_id: str, receiver_id: str) -> bool:
    """Redis ZSET 滑窗：60s 内 100 msg；超限返回 True。

    Redis 故障 → fail-open 到矩阵 (T-07-02-06 显式 accept；单 Owner 场景风险低)。
    """
    import time

    key = f'hasn:rate:{sender_id}:{receiver_id}'
    now = time.time()
    window_start = now - 60
    try:
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, 120)
        results = await pipe.execute()
        return results[1] >= 100
    except Exception as exc:
        log.warning(f'[iron_law_6] rate limit redis error, fail-open: {exc}')
        return False


async def check_iron_laws(
    db: Any,  # AsyncSession，本 phase 内未使用 (预留矩阵扩展)
    sender: dict[str, Any],
    receiver: dict[str, Any],
    envelope: dict[str, Any],
) -> DecisionResult | None:
    """按 ①..⑥ 顺序检查七铁律；命中任一即返回 DecisionResult，全部 pass 返回 None。

    铁律⑦ (审计) 由 permission_engine 自身保证 (调用 hasn_audit_log_service.append)。
    """
    # ── ① Agent 身份透明 ──
    if sender.get('entity_type') == 'agent' and not envelope.get('from_entity_type'):
        return DecisionResult(
            decision=DENY,
            reason='agent identity not declared',
            matched_rule='iron_law_1',
            error_code=2014,
        )

    # ── ② Owner 绝对控制权 (Owner → 自己的 Agent 直接放行) ──
    sender_id = sender.get('hasn_id')
    if sender_id and sender_id == receiver.get('owner_id'):
        return DecisionResult(
            decision=ALLOW,
            reason='owner controls own agent',
            matched_rule='iron_law_2',
        )

    # ── ③ 承诺需人类确认 ──
    behavior = _infer_behavior(envelope)
    if behavior in _COMMITMENT_BEHAVIORS:
        return DecisionResult(
            decision=CONFIRM,
            reason=f'behavior {behavior} requires human confirmation',
            matched_rule='iron_law_3',
        )

    # ── ④ 敏感数据禁区 ──
    content = envelope.get('content') or {}
    if _contains_sensitive(content):
        return DecisionResult(
            decision=SCOPE_LTD,
            reason='sensitive fields redacted',
            matched_rule='iron_law_4',
            allowed_fields=_non_sensitive_fields(envelope),
        )

    # ── ⑤ 通信边界强制 (commerce 关系禁止 free_chat) ──
    if envelope.get('relation_type') == 'commerce' and behavior == 'free_chat':
        return DecisionResult(
            decision=DENY,
            reason='commerce relation forbids free chat',
            matched_rule='iron_law_5',
            error_code=2014,
        )

    # ── ⑥ 频率限制 ──
    if await _check_rate_limit(
        sender.get('hasn_id', ''),
        receiver.get('hasn_id', ''),
    ):
        return DecisionResult(
            decision=DENY,
            reason='rate limit exceeded (60s/100msg)',
            matched_rule='iron_law_6',
            error_code=2012,
        )

    # 全部 pass — 留给矩阵继续判定
    return None
