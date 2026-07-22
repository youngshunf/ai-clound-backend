from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.plugin.s3.crud.storage import s3_storage_dao
from backend.plugin.s3.model import S3Storage
from backend.plugin.s3.schema.storage import CreateS3StorageParam, DeleteS3StorageParam, UpdateS3StorageParam
from backend.plugin.s3.service.storage_service import SPEECH_STORAGE_LOCK_KEY


class S3StorageService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> S3Storage:
        """
        获取 S3 存储

        :param db: 数据库会话
        :param pk: S3 存储 ID
        :return:
        """
        s3_storage = await s3_storage_dao.get(db, pk)
        if not s3_storage:
            raise errors.NotFoundError(msg='S3 存储不存在')
        return s3_storage

    @staticmethod
    async def get_list(db: AsyncSession, name: str | None, region: str | None) -> dict[str, Any]:
        """
        获取 S3 存储列表

        :param db: 数据库会话
        :param name: 存储名称
        :param region: 区域
        :return:
        """
        s3_storage_select = await s3_storage_dao.get_select(name, region)
        return await paging_data(db, s3_storage_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[S3Storage]:
        """
        获取所有 S3 存储

        :param db: 数据库会话
        :return:
        """
        s3_storages = await s3_storage_dao.get_all(db)
        return s3_storages

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateS3StorageParam) -> None:
        """
        创建 S3 存储

        :param db: 数据库会话
        :param obj: 创建S3 存储参数
        :return:
        """
        await s3_storage_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateS3StorageParam) -> int:
        """
        更新 S3 存储

        :param db: 数据库会话
        :param pk: S3 存储 ID
        :param obj: 更新S3 存储参数
        :return:
        """
        await db.execute(
            sa.text('SELECT pg_advisory_xact_lock(:lock_key)'),
            {'lock_key': SPEECH_STORAGE_LOCK_KEY},
        )
        from backend.app.hasn.model.hasn_speech_package import HasnSpeechPackage

        referenced = await db.scalar(sa.select(sa.literal(True)).where(HasnSpeechPackage.storage_id == pk).limit(1))
        if referenced:
            raise errors.ConflictError(msg='该 S3 存储已承载不可变语音包，禁止修改配置')
        count = await s3_storage_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteS3StorageParam) -> int:
        """
        删除 S3 存储

        :param db: 数据库会话
        :param obj: S3 存储 ID 列表
        :return:
        """
        await db.execute(
            sa.text('SELECT pg_advisory_xact_lock(:lock_key)'),
            {'lock_key': SPEECH_STORAGE_LOCK_KEY},
        )
        from backend.app.hasn.model.hasn_speech_package import HasnSpeechPackage

        referenced = await db.scalar(
            sa.select(sa.literal(True)).where(HasnSpeechPackage.storage_id.in_(obj.pks)).limit(1)
        )
        if referenced:
            raise errors.ConflictError(msg='选中的 S3 存储已承载不可变语音包，禁止删除')
        count = await s3_storage_dao.delete(db, obj.pks)
        return count


s3_storage_service: S3StorageService = S3StorageService()
