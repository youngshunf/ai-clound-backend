"""平台账号数据库操作类"""

from typing import List, Sequence

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.project.model import PlatformAccount
from backend.app.project.schema.platform_account import PlatformAccountCreate, PlatformAccountUpdate
from backend.utils.timezone import timezone


class CRUDPlatformAccount(CRUDPlus[PlatformAccount]):
    """平台账号数据库操作类"""

    async def get(self, db: AsyncSession, account_id: int) -> PlatformAccount | None:
        """获取账号详情"""
        return await self.select_model(db, account_id)

    async def select_all(self, db: AsyncSession, stmt: Select) -> Sequence[PlatformAccount]:
        """执行查询语句并返回结果列表"""
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_by_project_and_id(
        self, db: AsyncSession, project_id: int, account_id: str, platform: str
    ) -> PlatformAccount | None:
        """根据项目ID和平台账号ID获取账号"""
        stmt = select(self.model).where(
            self.model.project_id == project_id,
            self.model.account_id == account_id,
            self.model.platform == platform,
            self.model.is_deleted == False
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    async def get_list_by_project(
        self, db: AsyncSession, project_id: int
    ) -> Select:
        """获取项目下的账号列表"""
        return await self.select_order(
            'created_time', 'desc', 
            project_id=project_id, 
            is_deleted=False
        )

    async def get_list_by_user(
        self, db: AsyncSession, user_id: int
    ) -> Select:
        """获取用户的所有账号列表（跨项目）"""
        return await self.select_order(
            'created_time', 'desc', 
            user_id=user_id, 
            is_deleted=False
        )

    async def create(
        self, db: AsyncSession, obj: PlatformAccountCreate, user_id: int
    ) -> PlatformAccount:
        """创建账号"""
        dict_obj = obj.model_dump()
        dict_obj['user_id'] = user_id
        dict_obj['last_sync_at'] = timezone.now()
        
        # 初始版本号为 1
        dict_obj['server_version'] = 1
        
        new_account = self.model(**dict_obj)
        db.add(new_account)
        await db.flush()
        await db.refresh(new_account)
        return new_account

    async def update(
        self, db: AsyncSession, account_id: int, obj: PlatformAccountUpdate
    ) -> int:
        """更新账号"""
        update_data = obj.model_dump(exclude_unset=True)
        if not update_data:
            return 0
            
        update_data['server_version'] = PlatformAccount.server_version + 1
        update_data['last_sync_at'] = timezone.now()
        
        return await self.update_model(db, account_id, update_data)

    async def soft_delete(self, db: AsyncSession, account_id: int) -> int:
        """软删除账号"""
        return await self.update_model(
            db,
            account_id,
            {
                'is_deleted': True, 
                'deleted_at': timezone.now(),
                'server_version': PlatformAccount.server_version + 1,
                'last_sync_at': timezone.now()
            },
        )

    async def sync_upsert(
        self, 
        db: AsyncSession, 
        project_id: int, 
        user_id: int, 
        data: dict
    ) -> PlatformAccount:
        """同步：插入或更新账号"""
        platform = data.get('platform')
        account_id = data.get('account_id')
        
        # 查找是否存在（包括已删除的，以便恢复）
        stmt = select(self.model).where(
            self.model.project_id == project_id,
            self.model.account_id == account_id,
            self.model.platform == platform
        )
        result = await db.execute(stmt)
        existing = result.scalars().first()
        
        if existing:
            # 更新逻辑：简单的 Last-Write-Wins，或者这里总是信任客户端的最新状态
            # 注意：实际同步中可能需要对比 server_version，但 MVP 阶段我们假设客户端推上来就是最新的
            
            update_data = data.copy()
            # 移除只读或不可变字段
            for k in ['project_id', 'platform', 'account_id', 'id', 'uuid']:
                update_data.pop(k, None)
                
            update_data['user_id'] = user_id # 确保归属正确
            update_data['server_version'] = existing.server_version + 1
            update_data['last_sync_at'] = timezone.now()
            
            # 如果之前是删除状态，现在重新活跃，则恢复
            if existing.is_deleted and not data.get('is_deleted'):
                update_data['is_deleted'] = False
                update_data['deleted_at'] = None
                
            for k, v in update_data.items():
                setattr(existing, k, v)
                
            await db.flush()
            await db.refresh(existing)
            return existing
        else:
            # 创建新账号
            data['project_id'] = project_id
            data['user_id'] = user_id
            data['server_version'] = 1
            data['last_sync_at'] = timezone.now()
            
            new_account = self.model(**data)
            db.add(new_account)
            await db.flush()
            await db.refresh(new_account)
            return new_account


platform_account_dao: CRUDPlatformAccount = CRUDPlatformAccount(PlatformAccount)
