from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnArtifacts
from backend.app.hasn.schema.hasn_artifacts import CreateHasnArtifactsParam, UpdateHasnArtifactsParam


class CRUDHasnArtifacts(CRUDPlus[HasnArtifacts]):
    async def get(self, db: AsyncSession, pk: int) -> HasnArtifacts | None:
        """
        获取分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）

        :param db: 数据库会话
        :param pk: 分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnArtifacts]:
        """
        获取所有分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnArtifactsParam) -> None:
        """
        创建分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）

        :param db: 数据库会话
        :param obj: 创建分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnArtifactsParam) -> int:
        """
        更新分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）

        :param db: 数据库会话
        :param pk: 分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针） ID
        :param obj: 更新 分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）

        :param db: 数据库会话
        :param pks: 分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_artifacts_dao: CRUDHasnArtifacts = CRUDHasnArtifacts(HasnArtifacts)
