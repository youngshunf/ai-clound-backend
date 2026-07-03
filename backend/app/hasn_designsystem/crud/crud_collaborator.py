from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_designsystem.model import Collaborator
from backend.app.hasn_designsystem.schema.collaborator import CreateCollaboratorParam, UpdateCollaboratorParam


class CRUDCollaborator(CRUDPlus[Collaborator]):
    async def get(self, db: AsyncSession, pk: int) -> Collaborator | None:
        """
        获取设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :param pk: 设计系统协作分身绑定（对齐 DECKBIND） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取设计系统协作分身绑定（对齐 DECKBIND）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Collaborator]:
        """
        获取所有设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateCollaboratorParam) -> None:
        """
        创建设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :param obj: 创建设计系统协作分身绑定（对齐 DECKBIND）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateCollaboratorParam) -> int:
        """
        更新设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :param pk: 设计系统协作分身绑定（对齐 DECKBIND） ID
        :param obj: 更新 设计系统协作分身绑定（对齐 DECKBIND）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除设计系统协作分身绑定（对齐 DECKBIND）

        :param db: 数据库会话
        :param pks: 设计系统协作分身绑定（对齐 DECKBIND） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


collaborator_dao: CRUDCollaborator = CRUDCollaborator(Collaborator)
