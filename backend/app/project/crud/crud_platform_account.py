"""平台账号数据库操作类"""

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.project.model import PlatformAccount
from backend.app.project.schema.platform_account import PlatformAccountCreate, PlatformAccountUpdate
from backend.utils.timezone import timezone


class CRUDPlatformAccount(CRUDPlus[PlatformAccount]):
    """平台账号数据库操作类"""

    async def get(self, db: AsyncSession, account_id: str) -> PlatformAccount | None:
        """获取账号详情"""
        return await self.select_model(db, account_id)

    async def select_all(self, db: AsyncSession, stmt: Select) -> Sequence[PlatformAccount]:
        """执行查询语句并返回结果列表"""
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_by_project_and_id(
        self, db: AsyncSession, project_id: str, account_id: str, platform: str
    ) -> PlatformAccount | None:
        """根据项目ID和平台账号ID获取账号"""
        stmt = select(self.model).where(
            self.model.project_id == project_id,
            self.model.account_id == account_id,
            self.model.platform == platform,
            not self.model.is_deleted
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    async def get_list_by_project(
        self, db: AsyncSession, project_id: str
    ) -> Select:
        """获取项目下的账号列表"""
        return await self.select_order(
            'created_time', 'desc',
            project_id=project_id,
            is_deleted=False
        )

    async def get_list_by_user(
        self, db: AsyncSession, user_id: str
    ) -> Select:
        """获取用户的所有账号列表（跨项目）"""
        return await self.select_order(
            'created_time', 'desc',
            user_id=user_id,
            is_deleted=False
        )

    async def create(
        self, db: AsyncSession, obj: PlatformAccountCreate, user_id: str
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
        self, db: AsyncSession, account_id: str, obj: PlatformAccountUpdate
    ) -> int:
        """更新账号"""
        update_data = obj.model_dump(exclude_unset=True)
        if not update_data:
            return 0

        update_data['server_version'] = PlatformAccount.server_version + 1
        update_data['last_sync_at'] = timezone.now()

        return await self.update_model(db, account_id, update_data)

    async def soft_delete(self, db: AsyncSession, account_id: str) -> int:
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
        project_id: str,
        user_id: str,
        data: dict,
    ) -> PlatformAccount:
        """同步：插入或更新账号

        优先使用客户端提供的 id 进行查找和创建；如果没有，则使用
        (project_id, platform, account_id) 作为业务键进行查找。
        """
        client_id = data.get("id") or data.get("uid")
        platform = data.get("platform")
        account_id = data.get("account_id")

        existing: PlatformAccount | None = None

        # 1. 优先通过客户端 ID 查找
        if client_id:
            existing = await self.select_model(db, client_id)

        # 2. 如果通过 ID 找不到，尝试通过业务键 (project_id, platform, account_id) 查找
        if not existing and platform and account_id:
            stmt = select(self.model).where(
                self.model.project_id == project_id,
                self.model.account_id == account_id,
                self.model.platform == platform,
                self.model.is_deleted.is_(False),
            )
            result = await db.execute(stmt)
            existing = result.scalars().first()

        # 3. 已存在记录：执行更新（Last-Write-Wins）
        if existing:
            update_data = data.copy()
            # 移除只读或不可变字段
            for k in ["project_id", "platform", "account_id", "id", "uid"]:
                update_data.pop(k, None)

            update_data["user_id"] = user_id
            update_data["server_version"] = existing.server_version + 1
            update_data["last_sync_at"] = timezone.now()

            # 如果之前是删除状态，现在重新活跃，则恢复
            if existing.is_deleted and not data.get("is_deleted"):
                update_data["is_deleted"] = False
                update_data["deleted_at"] = None

            for k, v in update_data.items():
                setattr(existing, k, v)

            await db.flush()
            await db.refresh(existing)
            return existing

        # 4. 不存在记录：创建新账号
        create_data = data.copy()
        create_data["project_id"] = project_id
        create_data["user_id"] = user_id
        create_data["server_version"] = 1
        create_data["last_sync_at"] = timezone.now()
        # 如果客户端未提供 id/uid，则由数据库默认生成
        if not create_data.get("id") and not create_data.get("uid"):
            create_data.pop("id", None)
            create_data.pop("uid", None)

        new_account = self.model(**create_data)
        db.add(new_account)
        await db.flush()
        await db.refresh(new_account)
        return new_account


platform_account_dao: CRUDPlatformAccount = CRUDPlatformAccount(PlatformAccount)
