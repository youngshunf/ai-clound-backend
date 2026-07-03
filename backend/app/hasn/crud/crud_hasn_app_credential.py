from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAppCredential
from backend.app.hasn.schema.hasn_app_credential import CreateHasnAppCredentialParam, UpdateHasnAppCredentialParam


class CRUDHasnAppCredential(CRUDPlus[HasnAppCredential]):
    async def get(self, db: AsyncSession, pk: int) -> HasnAppCredential | None:
        """
        获取AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）

        :param db: 数据库会话
        :param pk: AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAppCredential]:
        """
        获取所有AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAppCredentialParam) -> None:
        """
        创建AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）

        :param db: 数据库会话
        :param obj: 创建AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAppCredentialParam) -> int:
        """
        更新AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）

        :param db: 数据库会话
        :param pk: AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential） ID
        :param obj: 更新 AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）

        :param db: 数据库会话
        :param pks: AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_app_credential_dao: CRUDHasnAppCredential = CRUDHasnAppCredential(HasnAppCredential)
