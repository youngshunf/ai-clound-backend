"""兼容 shim：内置任务目录模型已迁至 app/hasn_task（schema hasn_task，ADR-15）。"""

from backend.app.hasn_task.model.builtin_catalog import HasnBuiltinTaskCatalog as HasnBuiltinTaskCatalog
