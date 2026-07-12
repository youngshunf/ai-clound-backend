"""平台授权层（authz）：资源实例级 ACL 的统一门（G6·doc32）。

三个组件全部放在平台层 `app/hasn/service/authz`，**不属任何应用**：
- `subject.py`：操作主体（人 / 分身）唯一定义，四应用收编来源；
- `resource_registry.py`：资源类型注册表 + adapter 契约（应用唯一要写的东西）；
- `resource_gate.py`：门本体（`require` 命令式 / `enforce_declaration` 声明式）。

层次纪律（doc32 §3）：`authz/` 是平台层，**禁止 import 任何 `app/hasn_*` 应用模块**——
依赖方向恒为「应用→平台」单向；应用侧判权素材一律经 adapter / 钩子注入平台。
"""

from __future__ import annotations

from backend.app.hasn.service.authz.resource_gate import AuthorizedResource
from backend.app.hasn.service.authz.resource_registry import (
    ResourceKindAdapter,
    ResourceMeta,
    resource_kind_registry,
)
from backend.app.hasn.service.authz.subject import Subject

__all__ = [
    'AuthorizedResource',
    'ResourceKindAdapter',
    'ResourceMeta',
    'Subject',
    'resource_kind_registry',
]
