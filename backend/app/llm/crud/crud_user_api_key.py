"""用户 API Key CRUD"""

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model.user_api_key import UserApiKey
from backend.app.llm.schema.user_api_key import CreateUserApiKeyParam, UpdateUserApiKeyParam
from backend.utils.timezone import timezone


class CRUDUserApiKey(CRUDPlus[UserApiKey]):
    """用户 API Key 数据库操作类"""

    async def get(self, db: AsyncSession, pk: int) -> UserApiKey | None:
        return await self.select_model(db, pk)

    async def get_by_hash(self, db: AsyncSession, key_hash: str) -> UserApiKey | None:
        return await self.select_model_by_column(db, key_hash=key_hash)

    async def get_by_prefix(self, db: AsyncSession, key_prefix: str) -> UserApiKey | None:
        return await self.select_model_by_column(db, key_prefix=key_prefix)

    async def get_list(
        self,
        *,
        user_id: int | None = None,
        name: str | None = None,
        status: str | None = None,
    ) -> Select:
        filters = {}
        if user_id is not None:
            filters['user_id'] = user_id
        if name is not None:
            filters['name__like'] = f'%{name}%'
        if status is not None:
            filters['status'] = status
        return await self.select_order('id', 'desc', **filters)

    async def get_user_keys(self, db: AsyncSession, user_id: int) -> list[UserApiKey]:
        stmt = await self.select_order('id', 'desc', user_id=user_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        db: AsyncSession,
        obj: CreateUserApiKeyParam,
        *,
        user_id: int,
        key_prefix: str,
        key_hash: str,
        key_encrypted: str,
    ) -> UserApiKey:
        create_data = obj.model_dump()
        create_data.update({
            'user_id': user_id,
            'key_prefix': key_prefix,
            'key_hash': key_hash,
            'key_encrypted': key_encrypted,
            'status': 'ACTIVE',
        })
        new_obj = UserApiKey(**create_data)
        db.add(new_obj)
        await db.flush()
        await db.refresh(new_obj)
        return new_obj

    async def update(self, db: AsyncSession, pk: int, obj: UpdateUserApiKeyParam) -> int:
        return await self.update_model(db, pk, obj)

    async def update_last_used(self, db: AsyncSession, pk: int) -> int:
        return await self.update_model(db, pk, {'last_used_at': timezone.now()})

    async def delete(self, db: AsyncSession, pk: int) -> int:
        return await self.delete_model(db, pk)


user_api_key_dao: CRUDUserApiKey = CRUDUserApiKey(UserApiKey)
