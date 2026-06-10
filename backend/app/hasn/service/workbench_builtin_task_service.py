"""兼容 shim：内置任务目录服务已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.service.builtin_task_service import (
    WorkbenchBuiltinTaskService as WorkbenchBuiltinTaskService,
)
from backend.app.hasn_task.service.builtin_task_service import (
    workbench_builtin_task_service as workbench_builtin_task_service,
)
