"""平台项目（project，对外「项目管理」；模块 14 doc38）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/38-项目管理一级应用(平台项目·联邦挂靠)设计.md §5.3。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据（由 `app/mcp/scopes.py` 聚合）。

两条 scope（doc38 §3-5：均 Allow 出厂）：
- `project:read`——读平台项目 / 里程碑 / 聚合摘要 / 产物流并集（分身随便看自己主人名下的项目）；
- `project:write`——建/改/归档项目、建/改/完成里程碑、link/unlink 挂靠（**均在主人自己名下操作，
  不外发、不动钱、不接管应用容器**，故出厂 Allow，对齐 plan/deck 的创作类哲学）。

项目**不是权限边界**（doc38 三铁律）：这两条 scope 只控「分身能否操作主人的项目容器」，
owner 隔离由 `agent_context.owner_hasn_id` 强制，绝不跨 owner。
"""

from __future__ import annotations

PROJECT_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'project:read': {
        'label_zh': '查看项目',
        'label_en': 'View projects',
        'domain': 'project',
        'default_mode': 'allow',
        'risk': 'low',
        'description': '以 Agent 身份读取主人名下的平台项目、里程碑、聚合摘要与产物流（owner 隔离）',
        'description_en': 'Read the owner\'s platform projects, milestones, aggregate summary, and artifact flow as the agent (owner-isolated)',
    },
    'project:write': {
        'label_zh': '管理项目',
        'label_en': 'Manage projects',
        'domain': 'project',
        # 均在主人自己名下操作（建/改/归档项目、里程碑、挂靠 link/unlink），不外发不动钱，出厂 allow。
        'default_mode': 'allow',
        'risk': 'low',
        'description': '建/改/归档平台项目、建/改/完成里程碑、把资源挂靠进/摘出项目（均在主人名下，owner 隔离）',
        'description_en': 'Create/edit/archive platform projects, manage milestones, and link/unlink resources to a project (all under the owner, owner-isolated)',
    },
}
