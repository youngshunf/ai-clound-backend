from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import PlaybookVersion
from backend.app.hasn_growth.schema.playbook_version import CreatePlaybookVersionParam, UpdatePlaybookVersionParam


class CRUDPlaybookVersion(CRUDPlus[PlaybookVersion]):
    async def get(self, db: AsyncSession, pk: int) -> PlaybookVersion | None:
        """
        获取获客打法不可变版本快照，历史执行只读取本

        :param db: 数据库会话
        :param pk: 获客打法不可变版本快照，历史执行只读取本 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客打法不可变版本快照，历史执行只读取本列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[PlaybookVersion]:
        """
        获取所有获客打法不可变版本快照，历史执行只读取本

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreatePlaybookVersionParam) -> None:
        """
        创建获客打法不可变版本快照，历史执行只读取本

        :param db: 数据库会话
        :param obj: 创建获客打法不可变版本快照，历史执行只读取本参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdatePlaybookVersionParam) -> int:
        """
        更新获客打法不可变版本快照，历史执行只读取本

        :param db: 数据库会话
        :param pk: 获客打法不可变版本快照，历史执行只读取本 ID
        :param obj: 更新 获客打法不可变版本快照，历史执行只读取本参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客打法不可变版本快照，历史执行只读取本

        :param db: 数据库会话
        :param pks: 获客打法不可变版本快照，历史执行只读取本 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


playbook_version_dao: CRUDPlaybookVersion = CRUDPlaybookVersion(PlaybookVersion)
