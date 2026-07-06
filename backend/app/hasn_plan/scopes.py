"""规划与目标管理应用（plan）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/19-规划与目标管理/01-规划与目标管理总体设计.md §9.1；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

落地真相（P2 hasn-mcp `crates/hasn-mcp/src/plan.rs`，本期 P1 先铸 scope + 云端 Agent CRUD）：
- 写类（capture/triage/CRUD/decompose/event 等改 owner 数据的）统一 `plan:write`（出厂 Allow，owner 三态可覆盖）；
- 排程（schedule/reschedule，PLAN-P4b）统一 `plan:schedule`（出厂 Allow，独立 scope 便于 owner 单独管控自动排程）；
- 读类（list/get）无 required_scopes（不在此登记，避免假闸门）；
- 委托（delegate，PLAN-P5）统一 `plan:delegate`（出厂 **Allow**——2026-07-05 定策略「只拦外发/动钱，放开
  生成/委托」，委托自己/子分身干活是分身核心自主能力、产物留本地、可追踪可接管，不再每次拦；主人要把关可在
  权限页单独改 Ask。落地真相在 `plan.rs::Delegate`，用 HasnTool trait 默认 `default_capability_mode=Allow`）。
"""

from __future__ import annotations

PLAN_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'plan:read': {
        'label_zh': '查看规划数据',
        'label_en': 'View planning data',
        'domain': 'plan',
        'risk': 'low',
        'description': '以 Agent 身份读取主人的目标/计划/待办/日程/习惯（owner 隔离，只读）',
        'description_en': "Read the owner's goals, plans, to-dos, schedules, and habits as the agent (owner-isolated, read-only)",
    },
    'plan:write': {
        'label_zh': '管理规划数据',
        'label_en': 'Manage planning data',
        'domain': 'plan',
        'risk': 'medium',
        'description': '以 Agent 身份建/改/删主人的目标/计划/待办/日程/习惯与排期（owner 隔离）',
        'description_en': "Create, edit, and delete the owner's goals, plans, to-dos, schedules, habits, and scheduling as the agent (owner-isolated)",
    },
    'plan:schedule': {
        'label_zh': '自动排程日历',
        'label_en': 'Auto-schedule calendar',
        'domain': 'plan',
        'risk': 'medium',
        'description': '以 Agent 身份按 Motion 风格把待办自动排进/重排主人的日历空档（建/删弹性时间块，owner 隔离）',
        'description_en': "Motion-style auto-scheduling that fits and reshuffles to-dos into the owner's open calendar slots as the agent (create/delete flexible time blocks, owner-isolated)",
    },
    'plan:delegate': {
        'label_zh': '委托分身执行',
        'label_en': 'Delegate to the agent',
        'domain': 'plan',
        'risk': 'high',
        'default_mode': 'allow',
        'description': '以 Agent 身份把待办/计划委托给分身经工作会话真执行（出厂 Allow，分身核心自主能力，主人要把关可在权限页单独改）',
        'description_en': 'Delegate a to-do or plan to the agent for real execution via a work session (Allow by default; a core agent autonomy — the owner may switch it to Ask in the permissions page)',
    },
    'plan:manage': {
        'label_zh': '管理企业会议协同',
        'label_en': 'Manage enterprise meetings',
        'domain': 'plan',
        'risk': 'medium',
        'description': (
            '以 Agent 身份管理企业会议协同：加/减参会人（invite）、代主人回复 RSVP'
            '（PLAN-ENT 企业双模，owner 隔离 + 企业角色两刀交集）'
        ),
        'description_en': "Coordinate enterprise meetings as the agent: add or remove attendees (invite) and reply to RSVPs on the owner's behalf (PLAN-ENT enterprise mode, owner isolation plus enterprise-role intersection)",
    },
}
