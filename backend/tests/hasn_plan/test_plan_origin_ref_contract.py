"""PLAN-LOOP L0（修 G1）：origin_ref 权威注册表契约 + 防漂移守卫（纯逻辑，无 DB）。

冻结「派发/反查 origin_ref 必须匹配 ^resource:plan:(todo|goal|plan|milestone):\\d+$ 或白名单常量」
（doc06 §3.1 #4）。杜绝历史 daemon 连字符 ``todo-{id}`` / webui ``project:{id}`` 再漂移。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_plan.service import origin_ref as oref


def test_canonical_builders_are_colon_segmented() -> None:
    assert oref.todo_ref(42) == 'resource:plan:todo:42'
    assert oref.goal_ref(7) == 'resource:plan:goal:7'
    assert oref.plan_ref(3) == 'resource:plan:plan:3'
    assert oref.milestone_ref(9) == 'resource:plan:milestone:9'


@pytest.mark.parametrize(
    'ref',
    [
        'resource:plan:todo:1',
        'resource:plan:goal:100',
        'resource:plan:plan:5',
        'resource:plan:milestone:12',
        'resource:plan:onboarding',  # 白名单常量
    ],
)
def test_canonical_and_whitelist_accepted(ref: str) -> None:
    assert oref.is_canonical(ref) is True


@pytest.mark.parametrize(
    'drift',
    [
        'resource:plan:todo-42',  # daemon 历史连字符（G1 根因）
        'resource:plan:project:3',  # webui 计划轨漂移键
        'resource:plan:todo:',  # 缺 id
        'resource:plan:todo:abc',  # 非数字 id
        'plan:todo:1',  # 缺前缀
        'resource:plan:unknown:1',  # 非注册对象
    ],
)
def test_drift_values_rejected(drift: str) -> None:
    assert oref.is_canonical(drift) is False


def test_all_builders_output_matches_guard_regex() -> None:
    """构造器产出恒过守卫正则——防「造得出、守卫却拒」的自相矛盾。"""
    for ref in (oref.todo_ref(1), oref.goal_ref(1), oref.plan_ref(1), oref.milestone_ref(1)):
        assert oref.CANONICAL_RE.match(ref), ref
        assert oref.is_canonical(ref)
