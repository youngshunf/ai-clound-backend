from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import Playbook
from backend.app.hasn_growth.schema.playbook import CreatePlaybookParam, UpdatePlaybookParam


class CRUDPlaybook(CRUDPlus[Playbook]):
    async def get(self, db: AsyncSession, pk: int) -> Playbook | None:
        """
        获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param pk: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Playbook]:
        """
        获取所有获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreatePlaybookParam) -> None:
        """
        创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param obj: 创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdatePlaybookParam) -> int:
        """
        更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param pk: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID
        :param obj: 更新 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义

        :param db: 数据库会话
        :param pks: 获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


playbook_dao: CRUDPlaybook = CRUDPlaybook(Playbook)
