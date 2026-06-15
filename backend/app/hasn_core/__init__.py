"""hasn_core —— 平台核心对外契约层（架构候选③，方案 A）。

已拆出的业务模块（hasn_community / hasn_growth / hasn_task / hasn_deck / hasn_publish /
hasn_knowledge / hasn_memory / marketplace / billing / workbench）需要平台核心的
**身份 / 应用平台 / 资产**等能力时，一律从 `hasn_core` import——**不再**
`from backend.app.hasn.<crud|model|service>.…` 直抠 mega-module 内脏。

目的（设计提案 `docs/design/2026-06-16-app_hasn平台核心façade与god-file拆分-设计提案.md`）：
- 让 `app/hasn` 内部可自由重构（候选④拆 god-file）而**不炸**这 10 个兄弟模块；
- 让跨模块依赖**显式、收敛、可治理**（配套 import-lint 守卫，P4）。

P1 先行落「身份 façade」（实测占 76 处反向 import 的 ~57%）。后续 P2 应用平台 façade、
P3 长尾白名单陆续接入本层。
"""

from backend.app.hasn_core.identity import (
    AgentRef,
    HasnAgents,
    HasnHumans,
    HumanRef,
    IdentityFacade,
    hasn_agents_dao,
    hasn_humans_dao,
    identity,
)

__all__ = [
    'AgentRef',
    'HasnAgents',
    'HasnHumans',
    'HumanRef',
    'IdentityFacade',
    'hasn_agents_dao',
    'hasn_humans_dao',
    'identity',
]
