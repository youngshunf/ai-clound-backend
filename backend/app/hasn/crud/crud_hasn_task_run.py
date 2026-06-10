"""兼容 shim：任务执行记录 CRUD 已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.crud.crud_run import CRUDHasnTaskRun as CRUDHasnTaskRun
from backend.app.hasn_task.crud.crud_run import hasn_task_run_dao as hasn_task_run_dao
