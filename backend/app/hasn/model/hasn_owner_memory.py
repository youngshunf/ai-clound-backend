"""[ADR-15 收编兼容 shim] Owner 记忆模型已迁入 `app/hasn_memory.model.owner_memory`。

记忆子系统已按 ADR-15 收编为独立模块 + 独立 PG schema `hasn_memory`
（见 `docs/产品与技术/技术设计/02-平台能力/记忆与知识库/01-记忆领域与数据权威.md`）。
`HasnOwnerMemory` 已迁入新模块（表名 `owner_memory`，schema=hasn_memory）。本文件仅 re-export
保持既有 importer（如 `app/hasn/model/__init__.py`）兼容，**不重复定义模型**（避免
"Table already defined"）。
"""

from backend.app.hasn_memory.model.owner_memory import (
    HasnOwnerMemory as HasnOwnerMemory,
)
