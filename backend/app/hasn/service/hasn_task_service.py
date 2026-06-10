"""兼容 shim：任务定义服务已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.service.task_service import HasnTaskService as HasnTaskService
from backend.app.hasn_task.service.task_service import calc_next_run_at as calc_next_run_at
from backend.app.hasn_task.service.task_service import hasn_task_service as hasn_task_service
