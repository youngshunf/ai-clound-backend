"""兼容 shim：任务定义 CRUD 已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.crud.crud_task import CRUDHasnTask as CRUDHasnTask
from backend.app.hasn_task.crud.crud_task import hasn_task_dao as hasn_task_dao
