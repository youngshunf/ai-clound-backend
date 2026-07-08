"""L3 工具门（doc08 §4·RT3·云端半场）单测：工具声明 min_trust_level + 判档纯逻辑。

对齐 hasn-node ``crates/hasn-mcp/src/trust_gate.rs`` 的判定口径（逐条同款）：
- 同一 min=3 工具：peer_trust=2 被拒 / =3 放行 / =4 更放行；min=4：3 拒 / 4 放行。
- 主会话（is_external=False）不受限；无对外门工具（min=None）任何会话放行。
- fail-closed：对外会话 peer_trust 缺失当陌生人(1)。
- 结构化拒绝 shape：MCP_9217 + 文案含当前档 + 所需档 + 产品名 + 回绝引导。

纯逻辑 + 工具声明，无需 DB（对端真实 trust 的 PG 解析另见活体测试）。
"""

from __future__ import annotations

import pytest

from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.tools.owner import OwnerCoverageGetTool
from backend.app.mcp.tools.plan import PLAN_TOOLS
from backend.app.mcp.trust_gate import (
    RESERVED_IS_EXTERNAL,
    RESERVED_PEER_ID,
    RESERVED_PEER_TRUST,
    evaluate_min_trust_level,
    pop_trust_context,
    trust_level_label,
)


def _plan_tool(name: str):
    for t in PLAN_TOOLS:
        if t.name == name:
            return t
    raise AssertionError(f'plan 工具未注册: {name}')


def _denied(min_trust, peer_trust, *, is_external=True) -> bool:
    """跑一次门判定，返回是否被结构化拒绝。"""
    try:
        evaluate_min_trust_level(min_trust, peer_trust, is_external=is_external)
    except McpToolError as exc:
        assert exc.code is McpErrorCode.TRUST_LEVEL_INSUFFICIENT
        return True
    else:
        return False


# ── 一、纯判定逻辑（min=3 / min=4 分档）────────────────────────────────────────
def test_min3_denies_normal_friend_allows_friend() -> None:
    """min=3：普通朋友(2) 拒 / 好友(3) 放行 / 密友(4) 更放行。"""
    assert _denied(3, 2) is True
    assert _denied(3, 3) is False
    assert _denied(3, 4) is False


def test_min4_denies_friend_allows_trusted() -> None:
    """min=4：好友(3) 拒 / 密友(4) 放行 / 主人(5) 放行。"""
    assert _denied(4, 3) is True
    assert _denied(4, 4) is False
    assert _denied(4, 5) is False


def test_main_session_unrestricted() -> None:
    """主会话（is_external=False）：min=4 也放行（owner ↔ 自己分身不受限）。"""
    assert _denied(4, None, is_external=False) is False
    assert _denied(3, 0, is_external=False) is False  # 即便档极低，主会话照放


def test_ungated_tool_always_allowed() -> None:
    """无对外门工具（min=None）：任何会话（含对外陌生人）都放行。"""
    assert _denied(None, 0) is False
    assert _denied(None, None) is False
    assert _denied(None, 2, is_external=False) is False


def test_external_missing_trust_fails_closed() -> None:
    """fail-closed：对外会话 peer_trust=None → 当陌生人(1) → min≥2 被拒。"""
    assert _denied(3, None) is True  # 缺档当陌生人(1) < 3 → 拒
    assert _denied(2, None) is True  # 陌生人(1) < 2 → 拒
    assert _denied(1, None) is False  # 陌生人(1) >= 1 → 放行


# ── 二、结构化拒绝 shape（分身据此礼貌回绝）────────────────────────────────────
def test_deny_error_carries_code_and_tiers() -> None:
    """拒绝错误 = MCP_9217 + 含当前档 + 所需档 + 产品名 + 回绝引导。"""
    with pytest.raises(McpToolError) as ei:
        evaluate_min_trust_level(4, 2, is_external=True)
    exc = ei.value
    assert exc.code is McpErrorCode.TRUST_LEVEL_INSUFFICIENT
    assert exc.code.value == 'MCP_9217'
    assert '密友(4)' in exc.message  # 所需档
    assert '普通朋友(2)' in exc.message  # 当前档
    assert '已礼貌回绝' in exc.message  # 回绝引导


def test_deny_fail_closed_shows_stranger() -> None:
    """fail-closed 拒绝时当前档按陌生人(1) 呈现。"""
    with pytest.raises(McpToolError) as ei:
        evaluate_min_trust_level(3, None, is_external=True)
    assert '好友(3)' in ei.value.message
    assert '陌生人(1)' in ei.value.message


def test_trust_level_label_maps_product_names() -> None:
    assert trust_level_label(0) == '黑名单'
    assert trust_level_label(1) == '陌生人'
    assert trust_level_label(2) == '普通朋友'
    assert trust_level_label(3) == '好友'
    assert trust_level_label(4) == '密友'
    assert trust_level_label(5) == '主人'
    assert trust_level_label(9) == '未知'  # 越界诚实回落


# ── 三、保留参数剥离（系统注入·分身不可伪造）──────────────────────────────────
def test_pop_trust_context_absent_defaults_main_session() -> None:
    """无任何保留参数 → 原样返回 + is_external=False（never over-block）。"""
    args = {'start': 'x', 'end': 'y'}
    cleaned, is_external, peer_id, peer_trust = pop_trust_context(args)
    assert cleaned is args  # 无保留键时零拷贝原样返回
    assert (is_external, peer_id, peer_trust) == (False, None, None)


def test_pop_trust_context_strips_reserved_keys() -> None:
    """有保留参数 → 剥离干净 + 正确解出语境（工具体永不见保留键）。"""
    args = {
        'start': 'x',
        RESERVED_IS_EXTERNAL: True,
        RESERVED_PEER_ID: 'a_peer_agent',
        RESERVED_PEER_TRUST: 2,
    }
    cleaned, is_external, peer_id, peer_trust = pop_trust_context(args)
    assert cleaned == {'start': 'x'}
    assert all(k not in cleaned for k in (RESERVED_IS_EXTERNAL, RESERVED_PEER_ID, RESERVED_PEER_TRUST))
    assert (is_external, peer_id, peer_trust) == (True, 'a_peer_agent', 2)


def test_pop_trust_context_coerces_string_trust_ignores_bool() -> None:
    """peer_trust 字符串归一成 int；bool 不当档位（避免 True→1 误伤）。"""
    _, _, _, t_str = pop_trust_context({RESERVED_PEER_TRUST: '3'})
    assert t_str == 3
    _, _, _, t_bool = pop_trust_context({RESERVED_PEER_TRUST: True})
    assert t_bool is None
    _, ext, _, _ = pop_trust_context({RESERVED_IS_EXTERNAL: False, RESERVED_PEER_ID: ''})
    assert ext is False


# ── 四、工具声明 min_trust_level（点名工具挂门、其余无门）──────────────────────
def test_plan_read_tools_declare_min_trust_3() -> None:
    """看日程/看计划/偏好读工具 = 好友(3)。"""
    for name in (
        'hasn.plan.today',
        'hasn.plan.goal.list',
        'hasn.plan.goal.get',
        'hasn.plan.event.list',
        'hasn.plan.todo.list',
        'hasn.plan.availability',
        'hasn.plan.preference.get',
    ):
        assert _plan_tool(name).min_trust_level == 3, name


def test_plan_make_appointment_declares_min_trust_4() -> None:
    """代预约（event.create ≈ make_appointment）= 密友(4)。"""
    assert _plan_tool('hasn.plan.event.create').min_trust_level == 4


def test_owner_coverage_declares_min_trust_4() -> None:
    """主人画像（含居住地址/位置）= 密友(4)。"""
    assert OwnerCoverageGetTool().min_trust_level == 4


def test_plan_owner_own_ops_have_no_external_gate() -> None:
    """未点名的主人自身操作（建目标/分诊/习惯打卡/偏好写）无对外门（None）。"""
    for name in (
        'hasn.plan.capture',
        'hasn.plan.triage',
        'hasn.plan.goal.create',
        'hasn.plan.todo.create',
        'hasn.plan.habit.checkin',
        'hasn.plan.preference.set',
    ):
        assert _plan_tool(name).min_trust_level is None, name


# ── 五、工具 × 会话联动：同一工具 peer_trust=2 被拒 / =3 放行 ────────────────────
def test_same_tool_denied_at_2_allowed_at_3() -> None:
    """min=3 读工具 hasn.plan.today：对外会话 peer_trust=2 被拒、=3 放行、主会话不受限。"""
    tool = _plan_tool('hasn.plan.today')
    assert tool.min_trust_level == 3
    assert _denied(tool.min_trust_level, 2, is_external=True) is True
    assert _denied(tool.min_trust_level, 3, is_external=True) is False
    assert _denied(tool.min_trust_level, 2, is_external=False) is False  # 主会话不受限


def test_appointment_tool_denied_at_3_allowed_at_4() -> None:
    """min=4 代预约工具 hasn.plan.event.create：peer_trust=3 被拒、=4 放行。"""
    tool = _plan_tool('hasn.plan.event.create')
    assert tool.min_trust_level == 4
    assert _denied(tool.min_trust_level, 3, is_external=True) is True
    assert _denied(tool.min_trust_level, 4, is_external=True) is False


# ── 六、server.call_tool 内 L3 门（群/快路 peer_trust 路径，无 peer_id 不触 DB）─────
def _ctx():
    from backend.app.mcp.auth import AgentContext

    return AgentContext(
        hasn_id='a_trust_gate_test',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id='h_trust_gate_owner',
        session_uuid='amk_trust_gate_test',
    )


@pytest.mark.asyncio
async def test_server_gate_group_lowest_tier_denies_and_strips() -> None:
    """群会话（daemon 填 roster 最低档 2，无 peer_id）+ today(min3) → 结构化拒绝。"""
    from backend.app.mcp.server import mcp_server

    tool = _plan_tool('hasn.plan.today')
    args = {'start': 'x', 'end': 'y', RESERVED_IS_EXTERNAL: True, RESERVED_PEER_TRUST: 2}
    with pytest.raises(McpToolError) as ei:
        await mcp_server._enforce_conversation_trust_gate(_ctx(), tool, args)
    assert ei.value.code is McpErrorCode.TRUST_LEVEL_INSUFFICIENT


@pytest.mark.asyncio
async def test_server_gate_allows_and_returns_cleaned_args() -> None:
    """群会话 roster 最低档=3 满足 today(min3) → 放行，返回剥离保留参数后的干净入参。"""
    from backend.app.mcp.server import mcp_server

    tool = _plan_tool('hasn.plan.today')
    args = {'start': 'x', 'end': 'y', RESERVED_IS_EXTERNAL: True, RESERVED_PEER_TRUST: 3}
    cleaned = await mcp_server._enforce_conversation_trust_gate(_ctx(), tool, args)
    assert cleaned == {'start': 'x', 'end': 'y'}


@pytest.mark.asyncio
async def test_server_gate_main_session_unrestricted_no_db() -> None:
    """主会话（无保留参数）：min=4 工具也放行，且不触 DB、原样返回入参。"""
    from backend.app.mcp.server import mcp_server

    tool = _plan_tool('hasn.plan.event.create')
    args = {'title': 'x', 'start': 'a', 'end': 'b'}
    cleaned = await mcp_server._enforce_conversation_trust_gate(_ctx(), tool, args)
    assert cleaned == args
