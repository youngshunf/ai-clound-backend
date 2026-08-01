from uuid import UUID

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_hosting.crud.crud_hasn_node_authorization_codes import hasn_node_authorization_codes_dao
from backend.app.hasn_hosting.model import HasnNodeAuthorizationCodes
from backend.app.hasn_hosting.schema.hasn_node_authorization_codes import CreateHasnNodeAuthorizationCodesParam, DeleteHasnNodeAuthorizationCodesParam, UpdateHasnNodeAuthorizationCodesParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnNodeAuthorizationCodesService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: UUID) -> HasnNodeAuthorizationCodes:
        """
        获取云端节点设备授权码

        :param db: 数据库会话
        :param pk: 云端节点设备授权码 ID
        :return:
        """
        hasn_node_authorization_codes = await hasn_node_authorization_codes_dao.get(db, pk)
        if not hasn_node_authorization_codes:
            raise errors.NotFoundError(msg='云端节点设备授权码不存在')
        return hasn_node_authorization_codes

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取云端节点设备授权码列表

        :param db: 数据库会话
        :return:
        """
        hasn_node_authorization_codes_select = await hasn_node_authorization_codes_dao.get_select()
        return await paging_data(db, hasn_node_authorization_codes_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnNodeAuthorizationCodes]:
        """
        获取所有云端节点设备授权码

        :param db: 数据库会话
        :return:
        """
        hasn_node_authorization_codes_list = await hasn_node_authorization_codes_dao.get_all(db)
        return hasn_node_authorization_codes_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnNodeAuthorizationCodesParam) -> None:
        """
        创建云端节点设备授权码

        :param db: 数据库会话
        :param obj: 创建云端节点设备授权码参数
        :return:
        """
        await hasn_node_authorization_codes_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: UUID, obj: UpdateHasnNodeAuthorizationCodesParam) -> int:
        """
        更新云端节点设备授权码

        :param db: 数据库会话
        :param pk: 云端节点设备授权码 ID
        :param obj: 更新云端节点设备授权码参数
        :return:
        """
        count = await hasn_node_authorization_codes_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnNodeAuthorizationCodesParam) -> int:
        """
        删除云端节点设备授权码

        :param db: 数据库会话
        :param obj: 云端节点设备授权码 ID 列表
        :return:
        """
        count = await hasn_node_authorization_codes_dao.delete(db, obj.pks)
        return count


hasn_node_authorization_codes_service: HasnNodeAuthorizationCodesService = HasnNodeAuthorizationCodesService()
