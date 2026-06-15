"""[ADR-15 收编兼容 shim] Owner 记忆模型已迁入 `app/hasn_memory.model.owner_memory`。

记忆子系统已按 ADR-15 收编为独立模块 + 独立 PG schema `hasn_memory`
（见 `docs/hasn-node设计文档/02-记忆与知识库/实施/95-记忆独立模块与schema拆分方案.md`）。
原 `HasnOwnerMemory` / `HasnOwnerMemoryContribution` 已迁入新模块（表名去前缀
`owner_memory` / `owner_memory_contribution`，schema=hasn_memory）。本文件仅 re-export
保持既有 importer（如 `app/hasn/model/__init__.py`）兼容，**不重复定义模型**（避免
"Table already defined"）。
"""

from backend.app.hasn_memory.model.owner_memory import (
    HasnOwnerMemory as HasnOwnerMemory,
    HasnOwnerMemoryContribution as HasnOwnerMemoryContribution,
)
