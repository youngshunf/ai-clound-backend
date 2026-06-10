"""兼容 shim：内置任务目录 CRUD 已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.crud.crud_builtin_catalog import (
    CRUDHasnBuiltinTaskCatalog as CRUDHasnBuiltinTaskCatalog,
)
from backend.app.hasn_task.crud.crud_builtin_catalog import (
    hasn_builtin_task_catalog_dao as hasn_builtin_task_catalog_dao,
)
