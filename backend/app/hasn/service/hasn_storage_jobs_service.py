from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_storage_jobs import hasn_storage_jobs_dao
from backend.app.hasn.model import HasnStorageJobs
from backend.app.hasn.schema.hasn_storage_jobs import CreateHasnStorageJobsParam, DeleteHasnStorageJobsParam, UpdateHasnStorageJobsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnStorageJobsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnStorageJobs:
        """
        获取用户云存储持久作业与补偿 outbox

        :param db: 数据库会话
        :param pk: 用户云存储持久作业与补偿 outbox ID
        :return:
        """
        hasn_storage_jobs = await hasn_storage_jobs_dao.get(db, pk)
        if not hasn_storage_jobs:
            raise errors.NotFoundError(msg='用户云存储持久作业与补偿 outbox不存在')
        return hasn_storage_jobs

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户云存储持久作业与补偿 outbox列表

        :param db: 数据库会话
        :return:
        """
        hasn_storage_jobs_select = await hasn_storage_jobs_dao.get_select()
        return await paging_data(db, hasn_storage_jobs_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnStorageJobs]:
        """
        获取所有用户云存储持久作业与补偿 outbox

        :param db: 数据库会话
        :return:
        """
        hasn_storage_jobs_list = await hasn_storage_jobs_dao.get_all(db)
        return hasn_storage_jobs_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnStorageJobsParam) -> None:
        """
        创建用户云存储持久作业与补偿 outbox

        :param db: 数据库会话
        :param obj: 创建用户云存储持久作业与补偿 outbox参数
        :return:
        """
        await hasn_storage_jobs_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnStorageJobsParam) -> int:
        """
        更新用户云存储持久作业与补偿 outbox

        :param db: 数据库会话
        :param pk: 用户云存储持久作业与补偿 outbox ID
        :param obj: 更新用户云存储持久作业与补偿 outbox参数
        :return:
        """
        count = await hasn_storage_jobs_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnStorageJobsParam) -> int:
        """
        删除用户云存储持久作业与补偿 outbox

        :param db: 数据库会话
        :param obj: 用户云存储持久作业与补偿 outbox ID 列表
        :return:
        """
        count = await hasn_storage_jobs_dao.delete(db, obj.pks)
        return count


hasn_storage_jobs_service: HasnStorageJobsService = HasnStorageJobsService()
