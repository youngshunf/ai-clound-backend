"""兼容 shim：任务执行记录服务已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.service.run_service import HasnTaskRunService as HasnTaskRunService
from backend.app.hasn_task.service.run_service import hasn_task_run_service as hasn_task_run_service
