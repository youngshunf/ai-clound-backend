"""规划域产物回指 ``origin_ref`` 权威注册表（PLAN-LOOP §3.1，修 G1 三写法漂移）。

权威格式一律**冒号分段**：``resource:plan:<对象>:<云端权威 id>``，对齐 [02] §6.2 与
hasn:// URI 规范的「云端权威 ID」铁律。历史 daemon 派发写连字符 ``resource:plan:todo-{id}``、
webui 计划轨查 ``resource:plan:project:{id}``——与权威冒号键不一致 → 前端反查**永远查空**
（福仔反馈 F1「看不到结果」的第一根因）。本模块把权威形冻结为契约 + 构造器 + 守卫，
杜绝再漂移；存量数据由 ``backend/sql/hasn/migrations/2026-07-05-artifacts-originref-normalize.sql``
一次迁完（不做双键兼容层，避免兼容层永久化）。

事实源：``docs/hasn-node设计文档/19-规划与目标管理/06-规划全链路缺口对账与详情下钻产物操作闭环设计.md`` §3.1。
"""

from __future__ import annotations

import re

_PREFIX = 'resource:plan'

# 权威形正则（派发/反查五对象里可挂 id 的四类）：resource:plan:(todo|goal|plan|milestone):<正整数 id>
CANONICAL_RE = re.compile(r'^resource:plan:(todo|goal|plan|milestone):\d+$')

# 无 id 的白名单常量（采访规划/习惯/画像/主动规划等，无反查需求，保留不动）。
STATIC_WHITELIST = frozenset({
    'resource:plan:onboarding',
})


def todo_ref(todo_id: int | str) -> str:
    """待办产物回指（云端权威 todo id）。"""
    return f'{_PREFIX}:todo:{todo_id}'


def goal_ref(goal_id: int | str) -> str:
    """目标产物回指（云端权威 goal id）。"""
    return f'{_PREFIX}:goal:{goal_id}'


def plan_ref(plan_id: int | str) -> str:
    """计划产物回指（云端权威 plan id；替代旧漂移值 ``project:{id}``）。"""
    return f'{_PREFIX}:plan:{plan_id}'


def milestone_ref(milestone_id: int | str) -> str:
    """里程碑产物回指（云端权威 milestone id，L3 新增对象）。"""
    return f'{_PREFIX}:milestone:{milestone_id}'


def is_canonical(origin_ref: str) -> bool:
    """``origin_ref`` 是否为权威形（对象:id 冒号形）或静态白名单常量。防再漂移守卫用。"""
    return bool(CANONICAL_RE.match(origin_ref)) or origin_ref in STATIC_WHITELIST
