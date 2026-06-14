from typing import Any, Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.workbench.model import HasnOwnerWorkbenchPref
from backend.app.workbench.schema.hasn_owner_workbench_pref import CreateHasnOwnerWorkbenchPrefParam, UpdateHasnOwnerWorkbenchPrefParam


class CRUDHasnOwnerWorkbenchPref(CRUDPlus[HasnOwnerWorkbenchPref]):
    async def get(self, db: AsyncSession, pk: int) -> HasnOwnerWorkbenchPref | None:
        """
        获取HASN 主人工作台偏好（主脑指针 + 每日简报偏好）

        :param db: 数据库会话
        :param pk: HASN 主人工作台偏好（主脑指针 + 每日简报偏好） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 主人工作台偏好（主脑指针 + 每日简报偏好）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnOwnerWorkbenchPref]:
        """
        获取所有HASN 主人工作台偏好（主脑指针 + 每日简报偏好）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnOwnerWorkbenchPrefParam) -> None:
        """
        创建HASN 主人工作台偏好（主脑指针 + 每日简报偏好）

        :param db: 数据库会话
        :param obj: 创建HASN 主人工作台偏好（主脑指针 + 每日简报偏好）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnOwnerWorkbenchPrefParam) -> int:
        """
        更新HASN 主人工作台偏好（主脑指针 + 每日简报偏好）

        :param db: 数据库会话
        :param pk: HASN 主人工作台偏好（主脑指针 + 每日简报偏好） ID
        :param obj: 更新 HASN 主人工作台偏好（主脑指针 + 每日简报偏好）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 主人工作台偏好（主脑指针 + 每日简报偏好）

        :param db: 数据库会话
        :param pks: HASN 主人工作台偏好（主脑指针 + 每日简报偏好） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    @staticmethod
    async def get_by_owner(db: AsyncSession, owner_hasn_id: str) -> HasnOwnerWorkbenchPref | None:
        """按主人 hasn_id 取偏好行（每人最多一行）。"""
        result = await db.execute(
            select(HasnOwnerWorkbenchPref).where(HasnOwnerWorkbenchPref.owner_hasn_id == owner_hasn_id)
        )
        return result.scalars().first()

    @staticmethod
    async def upsert_by_owner(db: AsyncSession, *, owner_hasn_id: str, values: dict[str, Any]) -> HasnOwnerWorkbenchPref:
        """按 owner_hasn_id 唯一约束 UPSERT：不存在则插入，存在则只更新传入字段。

        `values` 只含需要写入的列（如 primary_agent_id / briefing_enabled / briefing_time /
        briefing_sources）；缺省字段沿用 DB 默认（插入）或保持不变（更新）。
        """
        insert_values = {'owner_hasn_id': owner_hasn_id, **values}
        update_set = {**values, 'updated_time': func.now()}
        stmt = (
            pg_insert(HasnOwnerWorkbenchPref)
            .values(**insert_values)
            .on_conflict_do_update(index_elements=['owner_hasn_id'], set_=update_set)
            .returning(HasnOwnerWorkbenchPref)
        )
        result = await db.execute(stmt, execution_options={'populate_existing': True})
        await db.flush()
        return result.scalars().first()


hasn_owner_workbench_pref_dao: CRUDHasnOwnerWorkbenchPref = CRUDHasnOwnerWorkbenchPref(HasnOwnerWorkbenchPref)
