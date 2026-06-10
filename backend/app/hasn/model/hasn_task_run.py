"""兼容 shim：任务执行记录模型已迁至 app/hasn_task（schema hasn_task，ADR-15）。"""

from backend.app.hasn_task.model.run import PROMPT_SNAPSHOT_COMMENT as PROMPT_SNAPSHOT_COMMENT
from backend.app.hasn_task.model.run import TASK_RUN_STATUS_COMMENT as TASK_RUN_STATUS_COMMENT
from backend.app.hasn_task.model.run import TOKEN_USAGE_COMMENT as TOKEN_USAGE_COMMENT
from backend.app.hasn_task.model.run import HasnTaskRun as HasnTaskRun
