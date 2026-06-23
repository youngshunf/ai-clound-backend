from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_studio.model import StudioArtifact
from backend.app.hasn_studio.schema.studio_artifact import CreateStudioArtifactParam, UpdateStudioArtifactParam


class CRUDStudioArtifact(CRUDPlus[StudioArtifact]):
    async def get(self, db: AsyncSession, pk: int) -> StudioArtifact | None:
        """
        获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :param pk: 视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[StudioArtifact]:
        """
        获取所有视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateStudioArtifactParam) -> None:
        """
        创建视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :param obj: 创建视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateStudioArtifactParam) -> int:
        """
        更新视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :param pk: 视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID
        :param obj: 更新 视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :param pks: 视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


studio_artifact_dao: CRUDStudioArtifact = CRUDStudioArtifact(StudioArtifact)
