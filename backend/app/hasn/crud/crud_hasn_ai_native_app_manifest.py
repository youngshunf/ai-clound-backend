from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAiNativeAppManifest
from backend.app.hasn.schema.ai_native_app import CreateAiNativeAppManifestParam, UpdateAiNativeAppManifestParam


class CRUDHasnAiNativeAppManifest(CRUDPlus[HasnAiNativeAppManifest]):
    @staticmethod
    def _single_manifest(result: object) -> HasnAiNativeAppManifest | None:
        """将无关联加载的查询结果收紧为单个应用清单。"""
        if result is not None and not isinstance(result, HasnAiNativeAppManifest):
            raise TypeError('应用清单单模型查询返回了关联结果')
        return cast(HasnAiNativeAppManifest | None, result)

    @staticmethod
    def _manifest_sequence(result: Sequence[object]) -> Sequence[HasnAiNativeAppManifest]:
        """将无关联加载的查询结果收紧为应用清单序列。"""
        if not all(isinstance(item, HasnAiNativeAppManifest) for item in result):
            raise TypeError('应用清单列表查询返回了关联结果')
        return cast(Sequence[HasnAiNativeAppManifest], result)

    async def get(self, db: AsyncSession, pk: int) -> HasnAiNativeAppManifest | None:
        return self._single_manifest(await self.select_model(db, pk))

    async def get_select(self) -> Select:
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAiNativeAppManifest]:
        return self._manifest_sequence(await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateAiNativeAppManifestParam) -> None:
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateAiNativeAppManifestParam) -> int:
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)

    async def get_by_app_version(self, db: AsyncSession, *, app_id: str, version: str) -> HasnAiNativeAppManifest | None:
        return self._single_manifest(await self.select_model_by_column(db, app_id=app_id, version=version))

    async def get_latest_by_app_id(
        self, db: AsyncSession, *, app_id: str, status: str | None = None
    ) -> HasnAiNativeAppManifest | None:
        stmt = await self.select_order('id', 'desc')
        stmt = stmt.where(HasnAiNativeAppManifest.app_id == app_id)
        if status:
            stmt = stmt.where(HasnAiNativeAppManifest.status == status)
        return (await db.execute(stmt)).scalars().first()


hasn_ai_native_app_manifest_dao: CRUDHasnAiNativeAppManifest = CRUDHasnAiNativeAppManifest(HasnAiNativeAppManifest)
