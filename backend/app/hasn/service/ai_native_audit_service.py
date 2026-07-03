from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_ai_native_app_audit import hasn_ai_native_app_audit_dao
from backend.app.hasn.model import HasnAiNativeAppAudit
from backend.app.hasn.schema.ai_native_audit import (
    CreateAiNativeAppAuditParam,
    DeleteAiNativeAppAuditParam,
    UpdateAiNativeAppAuditParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class AiNativeAuditService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAiNativeAppAudit:
        item = await hasn_ai_native_app_audit_dao.get(db, pk)
        if not item:
            raise errors.NotFoundError(msg='AI-Native 审计不存在')
        return item

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        return await paging_data(db, await hasn_ai_native_app_audit_dao.get_select())

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAiNativeAppAudit]:
        return await hasn_ai_native_app_audit_dao.get_all(db)

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateAiNativeAppAuditParam) -> None:
        await hasn_ai_native_app_audit_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateAiNativeAppAuditParam) -> int:
        return await hasn_ai_native_app_audit_dao.update(db, pk, obj)

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteAiNativeAppAuditParam) -> int:
        return await hasn_ai_native_app_audit_dao.delete(db, obj.pks)


ai_native_audit_service: AiNativeAuditService = AiNativeAuditService()
