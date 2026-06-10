"""兼容 shim：任务定义模型已迁至 app/hasn_task（schema hasn_task，ADR-15）。

保留本文件仅为兼容旧导入路径；新代码请直接 import backend.app.hasn_task.model。
"""

from backend.app.hasn_task.model.task import TASK_STATE_COMMENT as TASK_STATE_COMMENT
from backend.app.hasn_task.model.task import HasnTask as HasnTask
