"""官方内置任务目录服务（云端权威）。

daemon 经 owner 通道拉取启用中的内置任务目录，按 `catalog_revision` 变化重拉，
再幂等播种成绑主脑的本地任务。目录定义（有哪些/技能包/提示词/默认调度）是云端权威，
本地任务行只是执行物化。设计 §3.3 / §3.4。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_builtin_task_catalog import hasn_builtin_task_catalog_dao
from backend.app.hasn.schema.hasn_builtin_task_catalog import BuiltinTaskCatalogResponse, BuiltinTaskItem


class WorkbenchBuiltinTaskService:
    """内置任务目录读取（拉取播种用）。"""

    @staticmethod
    async def list_enabled(db: AsyncSession) -> BuiltinTaskCatalogResponse:
        """返回启用中的内置任务目录 + 聚合版本号。

        `catalog_revision` = 启用条目 revision 之和：任一条目 revision 递增、新增/撤下、
        或上/下线都会改变它，daemon 据此决定是否重拉（沿用公共技能 revision 折叠思路）。
        """
        rows = await hasn_builtin_task_catalog_dao.list_enabled(db)
        items = [BuiltinTaskItem.model_validate(row) for row in rows]
        catalog_revision = sum(item.revision for item in items)
        return BuiltinTaskCatalogResponse(items=items, catalog_revision=catalog_revision)


workbench_builtin_task_service = WorkbenchBuiltinTaskService()
