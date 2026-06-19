"""规划与目标管理应用（plan）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/19-规划与目标管理/01-规划与目标管理总体设计.md §9.1；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

落地真相（P2 hasn-mcp `crates/hasn-mcp/src/plan.rs`，本期 P1 先铸 scope + 云端 Agent CRUD）：
- 写类（capture/triage/CRUD/decompose/event 等改 owner 数据的）统一 `plan:write`（出厂 Allow，owner 三态可覆盖）；
- 排程（schedule/reschedule，PLAN-P4b）统一 `plan:schedule`（出厂 Allow，独立 scope 便于 owner 单独管控自动排程）；
- 读类（list/get）无 required_scopes（不在此登记，避免假闸门）；
- 委托（plan:delegate）随 P5 落地再铸。
"""

from __future__ import annotations

PLAN_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'plan:read': {
        'label_zh': '查看规划数据',
        'domain': 'plan',
        'risk': 'low',
        'description': '以 Agent 身份读取主人的目标/计划/待办/日程/习惯（owner 隔离，只读）',
    },
    'plan:write': {
        'label_zh': '管理规划数据',
        'domain': 'plan',
        'risk': 'medium',
        'description': '以 Agent 身份建/改/删主人的目标/计划/待办/日程/习惯与排期（owner 隔离）',
    },
    'plan:schedule': {
        'label_zh': '自动排程日历',
        'domain': 'plan',
        'risk': 'medium',
        'description': '以 Agent 身份按 Motion 风格把待办自动排进/重排主人的日历空档（建/删弹性时间块，owner 隔离）',
    },
}
