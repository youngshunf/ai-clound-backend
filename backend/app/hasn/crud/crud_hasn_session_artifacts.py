from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnSessionArtifacts
from backend.app.hasn.schema.hasn_session_artifacts import (
    CreateHasnSessionArtifactsParam,
    UpdateHasnSessionArtifactsParam,
)


class CRUDHasnSessionArtifacts(CRUDPlus[HasnSessionArtifacts]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSessionArtifacts | None:
        """
        获取HASN 会话产物

        :param db: 数据库会话
        :param pk: HASN 会话产物 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 会话产物列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSessionArtifacts]:
        """
        获取所有HASN 会话产物

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnSessionArtifactsParam) -> None:
        """
        创建HASN 会话产物

        :param db: 数据库会话
        :param obj: 创建HASN 会话产物参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSessionArtifactsParam) -> int:
        """
        更新HASN 会话产物

        :param db: 数据库会话
        :param pk: HASN 会话产物 ID
        :param obj: 更新 HASN 会话产物参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 会话产物

        :param db: 数据库会话
        :param pks: HASN 会话产物 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_session_artifacts_dao: CRUDHasnSessionArtifacts = CRUDHasnSessionArtifacts(HasnSessionArtifacts)
