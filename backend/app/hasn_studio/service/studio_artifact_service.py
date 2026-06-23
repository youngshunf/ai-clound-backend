from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_studio.crud.crud_studio_artifact import studio_artifact_dao
from backend.app.hasn_studio.model import StudioArtifact
from backend.app.hasn_studio.schema.studio_artifact import (
    CreateStudioArtifactParam,
    DeleteStudioArtifactParam,
    UpdateStudioArtifactParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class StudioArtifactService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> StudioArtifact:
        """
        获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :param pk: 视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID
        :return:
        """
        studio_artifact = await studio_artifact_dao.get(db, pk)
        if not studio_artifact:
            raise errors.NotFoundError(msg='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）不存在')
        return studio_artifact

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）列表

        :param db: 数据库会话
        :return:
        """
        studio_artifact_select = await studio_artifact_dao.get_select()
        return await paging_data(db, studio_artifact_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[StudioArtifact]:
        """
        获取所有视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :return:
        """
        studio_artifact_list = await studio_artifact_dao.get_all(db)
        return studio_artifact_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateStudioArtifactParam) -> None:
        """
        创建视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :param obj: 创建视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）参数
        :return:
        """
        await studio_artifact_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateStudioArtifactParam) -> int:
        """
        更新视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :param pk: 视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID
        :param obj: 更新视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）参数
        :return:
        """
        count = await studio_artifact_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteStudioArtifactParam) -> int:
        """
        删除视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）

        :param db: 数据库会话
        :param obj: 视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID 列表
        :return:
        """
        count = await studio_artifact_dao.delete(db, obj.pks)
        return count


studio_artifact_service: StudioArtifactService = StudioArtifactService()
