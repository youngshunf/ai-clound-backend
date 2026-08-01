from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_imagelab.model import HasnImagelabProject


class CRUDHasnImagelabProject(CRUDPlus[HasnImagelabProject]):
    async def get(self, db: AsyncSession, pk: UUID) -> HasnImagelabProject | None:
        """按兼容 server_id 读取历史图坊引用登记行。"""
        return await self.select_model(db, pk)

    async def get_by_owner_and_local_ref(
        self, db: AsyncSession, *, owner_id: str, local_ref: str
    ) -> HasnImagelabProject | None:
        """按 (owner_id, local_ref) 唯一键取登记行（幂等 upsert 依据 + owner 行级隔离）。"""
        return await self.select_model_by_column(db, owner_id=owner_id, local_ref=local_ref)

    async def create_registration(
        self, db: AsyncSession, *, owner_id: str, local_ref: str, name: str
    ) -> HasnImagelabProject:
        """新登记一行（首次登记，兼容 id 由 DB 默认 gen_random_uuid 生成）。

        返回持久化后的实例（含 flush 出的 id），供上层读 str(id) 作 server_id 回给 daemon。
        """
        obj = HasnImagelabProject(owner_id=owner_id, local_ref=local_ref, name=name)
        db.add(obj)
        await db.flush()
        return obj


hasn_imagelab_project_dao: CRUDHasnImagelabProject = CRUDHasnImagelabProject(HasnImagelabProject)
