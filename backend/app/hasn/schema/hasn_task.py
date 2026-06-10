"""兼容 shim：任务定义 schema 已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.schema.task import TASK_STATE_DESCRIPTION as TASK_STATE_DESCRIPTION
from backend.app.hasn_task.schema.task import CreateHasnTaskParam as CreateHasnTaskParam
from backend.app.hasn_task.schema.task import DeleteHasnTaskParam as DeleteHasnTaskParam
from backend.app.hasn_task.schema.task import GetHasnTaskDetail as GetHasnTaskDetail
from backend.app.hasn_task.schema.task import HasnTaskSchemaBase as HasnTaskSchemaBase
from backend.app.hasn_task.schema.task import UpdateHasnTaskParam as UpdateHasnTaskParam
