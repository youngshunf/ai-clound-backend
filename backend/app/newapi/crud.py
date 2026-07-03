"""唤星用户 ↔ new-api 用户映射 CRUD（主库表 llm_newapi_user_mapping）。

> CRUDNewApiDirect（直连 new-api 第二数据库的 raw SQL DAO）已删除（2026-06-15 解耦）：
> 用户/token 创建、quota 读写、用量查询全部改走 new-api HTTP 管理 API（app/newapi/client.py
> + service.py）。本文件只保留唤星主库内的映射表 CRUD。
"""

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.app.newapi.schema.llm_newapi_user_mapping import (
    CreateLlmNewapiUserMappingParam,
    UpdateLlmNewapiUserMappingParam,
)


class CRUDLlmNewapiUserMapping(CRUDPlus[LlmNewapiUserMapping]):
    async def get(self, db: AsyncSession, pk: int) -> LlmNewapiUserMapping | None:
        return await self.select_model(db, pk)

    async def get_by_user(
        self, db: AsyncSession, huanxing_user_id: int, app_code: str = 'huanxing'
    ) -> LlmNewapiUserMapping | None:
        """根据唤星用户 ID + app_code 查询映射"""
        stmt = select(LlmNewapiUserMapping).where(
            LlmNewapiUserMapping.huanxing_user_id == huanxing_user_id,
            LlmNewapiUserMapping.app_code == app_code,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_select(self) -> Select:
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[LlmNewapiUserMapping]:
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateLlmNewapiUserMappingParam) -> None:
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateLlmNewapiUserMappingParam) -> int:
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


llm_newapi_user_mapping_dao: CRUDLlmNewapiUserMapping = CRUDLlmNewapiUserMapping(LlmNewapiUserMapping)
