"""兼容 shim：内置任务目录 schema 已迁至 app/hasn_task（ADR-15）。"""

from backend.app.hasn_task.schema.builtin_catalog import BuiltinTaskCatalogResponse as BuiltinTaskCatalogResponse
from backend.app.hasn_task.schema.builtin_catalog import BuiltinTaskItem as BuiltinTaskItem
from backend.app.hasn_task.schema.builtin_catalog import (
    CreateHasnBuiltinTaskCatalogParam as CreateHasnBuiltinTaskCatalogParam,
)
from backend.app.hasn_task.schema.builtin_catalog import (
    DeleteHasnBuiltinTaskCatalogParam as DeleteHasnBuiltinTaskCatalogParam,
)
from backend.app.hasn_task.schema.builtin_catalog import (
    GetHasnBuiltinTaskCatalogDetail as GetHasnBuiltinTaskCatalogDetail,
)
from backend.app.hasn_task.schema.builtin_catalog import (
    HasnBuiltinTaskCatalogSchemaBase as HasnBuiltinTaskCatalogSchemaBase,
)
from backend.app.hasn_task.schema.builtin_catalog import (
    UpdateHasnBuiltinTaskCatalogParam as UpdateHasnBuiltinTaskCatalogParam,
)
