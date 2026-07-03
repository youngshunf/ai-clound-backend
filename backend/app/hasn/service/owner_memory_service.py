"""[ADR-15 收编兼容 shim] Owner 记忆合并服务已迁入 `app/hasn_memory.service.owner_memory_service`。

记忆子系统按 ADR-15 收编为独立模块 `app/hasn_memory`。本文件 re-export 保持既有 importer
（如 `app/hasn/api/v1/agent/hasn_agent_profile.py`）兼容；新代码请直接从 `app/hasn_memory`
导入。
"""

from backend.app.hasn_memory.service.owner_memory_service import (
    LlmComplete as LlmComplete,
)
from backend.app.hasn_memory.service.owner_memory_service import (
    OwnerMemoryService as OwnerMemoryService,
)
from backend.app.hasn_memory.service.owner_memory_service import (
    owner_memory_service as owner_memory_service,
)
