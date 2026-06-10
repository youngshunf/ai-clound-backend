"""兼容 shim：任务执行记录 schema 已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.schema.run import PROMPT_SNAPSHOT_DESCRIPTION as PROMPT_SNAPSHOT_DESCRIPTION
from backend.app.hasn_task.schema.run import TASK_RUN_STATUS_DESCRIPTION as TASK_RUN_STATUS_DESCRIPTION
from backend.app.hasn_task.schema.run import CreateHasnTaskRunParam as CreateHasnTaskRunParam
from backend.app.hasn_task.schema.run import DeleteHasnTaskRunParam as DeleteHasnTaskRunParam
from backend.app.hasn_task.schema.run import GetHasnTaskRunDetail as GetHasnTaskRunDetail
from backend.app.hasn_task.schema.run import HasnTaskRunSchemaBase as HasnTaskRunSchemaBase
from backend.app.hasn_task.schema.run import UpdateHasnTaskRunParam as UpdateHasnTaskRunParam
