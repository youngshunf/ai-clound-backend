"""操作主体：人或分身（分身背后总有主人），全平台唯一定义（doc32 §3）。

历史上 knowledge / deck / designsystem / studio 四个应用各自复制了一份一模一样的
`Subject` frozen dataclass（judge 内核 `resolve_effective_permission` 的入参载体）。G6 把这四份
收编为本文件一份——各应用 service 改 `from backend.app.hasn.service.authz import Subject`，
并在各自模块级保留 `Subject` 再导出（模块名字不消失，既有调用点不必批量改）。

判权内核约定：`kind='human'` 时 `hasn_id == owner_hasn_id`（人即自己的主人）；`kind='agent'`
时 `hasn_id` 是分身 id、`owner_hasn_id` 是其背后主人——分身继承主人权限（`resolve_effective_permission`
的 explicit_grant 匹配集合含 `subject_owner_hasn_id`）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Subject:
    """操作主体：人或分身（分身背后总有主人）。全平台唯一定义。"""

    hasn_id: str
    kind: str  # 'human' | 'agent'
    owner_hasn_id: str  # 背后主人（human 时 == hasn_id）

    @staticmethod
    def human(hasn_id: str) -> Subject:
        return Subject(hasn_id=hasn_id, kind='human', owner_hasn_id=hasn_id)

    @staticmethod
    def agent(agent_hasn_id: str, owner_hasn_id: str) -> Subject:
        return Subject(hasn_id=agent_hasn_id, kind='agent', owner_hasn_id=owner_hasn_id)
