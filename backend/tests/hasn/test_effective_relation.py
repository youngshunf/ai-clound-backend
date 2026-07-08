"""doc08 RT1.5 §4.1.1：有效关系解析纯判定核 `resolve_effective_relation` 三分叉单测。

纯函数 = 无 DB / 无副作用，直测决策分叉（deliver / request_and_suppress / gate / blocked）
+ 物化边继承的信任等级（D7）。这是入站门控 / A2H / 消息工具三处共用的判定核。
"""
from __future__ import annotations

from backend.app.hasn.service.effective_relation import (
    BLOCKED,
    DELIVER,
    GATE,
    REQUEST_AND_SUPPRESS,
    resolve_effective_relation,
)


# ── 一、直连实体边优先（§4.1.1 step 1）──


def test_direct_edge_ge2_delivers_without_materialize() -> None:
    """直连边≥2 命中 → DELIVER，inherit_trust=None（已有边，无需物化）。"""
    v = resolve_effective_relation(from_is_agent=True, direct_edge_trust=2)
    assert v == {'decision': DELIVER, 'inherit_trust': None}
    v3 = resolve_effective_relation(from_is_agent=True, direct_edge_trust=4)
    assert v3 == {'decision': DELIVER, 'inherit_trust': None}


def test_direct_edge_trust1_gates() -> None:
    """直连边=1（陌生档）→ GATE（尊重主人对该实体的显式设档，不回退主人派生）。"""
    v = resolve_effective_relation(
        from_is_agent=True, direct_edge_trust=1, owner_edge_trust=5,
    )
    assert v == {'decision': GATE, 'inherit_trust': None}


def test_direct_edge_blocked_is_blocked() -> None:
    """直连边黑名单 → BLOCKED（静默拒，不看主人边）。"""
    v = resolve_effective_relation(
        from_is_agent=True, direct_edge_trust=None, direct_edge_blocked=True, owner_edge_trust=5,
    )
    assert v == {'decision': BLOCKED, 'inherit_trust': None}


# ── 二、无直连边 → 分身主人派生（§4.1.1 step 2）──


def test_owner_edge_friend_delivers_and_inherits_trust() -> None:
    """无直连边 + 主人边=3 好友 → DELIVER 且物化边继承主人档 trust=3（D7）。"""
    v = resolve_effective_relation(
        from_is_agent=True, direct_edge_trust=None, owner_edge_trust=3,
    )
    assert v == {'decision': DELIVER, 'inherit_trust': 3}


def test_owner_edge_confidant_inherits_trust4() -> None:
    """主人边=4 密友 → DELIVER 继承 trust=4。"""
    v = resolve_effective_relation(
        from_is_agent=True, direct_edge_trust=None, owner_edge_trust=4,
    )
    assert v == {'decision': DELIVER, 'inherit_trust': 4}


def test_owner_edge_normal_friend_requests_and_suppresses() -> None:
    """主人边=2 普通朋友 → REQUEST_AND_SUPPRESS，代发请求档=2。"""
    v = resolve_effective_relation(
        from_is_agent=True, direct_edge_trust=None, owner_edge_trust=2,
    )
    assert v == {'decision': REQUEST_AND_SUPPRESS, 'inherit_trust': 2}


def test_owner_edge_stranger_gates() -> None:
    """主人边≤1（陌生/无档）→ GATE（走现状陌生人门控）。"""
    assert resolve_effective_relation(
        from_is_agent=True, direct_edge_trust=None, owner_edge_trust=1,
    ) == {'decision': GATE, 'inherit_trust': None}
    assert resolve_effective_relation(
        from_is_agent=True, direct_edge_trust=None, owner_edge_trust=None,
    ) == {'decision': GATE, 'inherit_trust': None}


def test_owner_edge_blocked_is_blocked() -> None:
    """主人边黑名单 → BLOCKED（静默拒）。"""
    v = resolve_effective_relation(
        from_is_agent=True, direct_edge_trust=None, owner_edge_blocked=True,
    )
    assert v == {'decision': BLOCKED, 'inherit_trust': None}


# ── 三、非分身发送方不派生（fail-closed 陌生人）──


def test_non_agent_sender_never_derives() -> None:
    """发送方非分身 + 无直连边 → GATE，即便传了 owner_edge_trust 也不派生。"""
    v = resolve_effective_relation(
        from_is_agent=False, direct_edge_trust=None, owner_edge_trust=5,
    )
    assert v == {'decision': GATE, 'inherit_trust': None}
