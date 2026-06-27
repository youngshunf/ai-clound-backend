import uuid

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_node_bindings import hasn_node_bindings_dao
from backend.app.hasn.model import HasnNodeBindings
from backend.app.hasn.schema.hasn_node_bindings import (
    CreateHasnNodeBindingsParam,
    DeleteHasnNodeBindingsParam,
    UpdateHasnNodeBindingsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone


class HasnNodeBindingsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnNodeBindings:
        """
        获取HASN Node Owner Binding 租约

        :param db: 数据库会话
        :param pk: HASN Node Owner Binding 租约 ID
        :return:
        """
        hasn_node_bindings = await hasn_node_bindings_dao.get(db, pk)
        if not hasn_node_bindings:
            raise errors.NotFoundError(msg='HASN Node Owner Binding 租约不存在')
        return hasn_node_bindings

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN Node Owner Binding 租约列表

        :param db: 数据库会话
        :return:
        """
        hasn_node_bindings_select = await hasn_node_bindings_dao.get_select()
        return await paging_data(db, hasn_node_bindings_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnNodeBindings]:
        """
        获取所有HASN Node Owner Binding 租约

        :param db: 数据库会话
        :return:
        """
        hasn_node_bindingss = await hasn_node_bindings_dao.get_all(db)
        return hasn_node_bindingss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnNodeBindingsParam) -> None:
        """
        创建HASN Node Owner Binding 租约

        :param db: 数据库会话
        :param obj: 创建HASN Node Owner Binding 租约参数
        :return:
        """
        await hasn_node_bindings_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnNodeBindingsParam) -> int:
        """
        更新HASN Node Owner Binding 租约

        :param db: 数据库会话
        :param pk: HASN Node Owner Binding 租约 ID
        :param obj: 更新HASN Node Owner Binding 租约参数
        :return:
        """
        count = await hasn_node_bindings_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnNodeBindingsParam) -> int:
        """
        删除HASN Node Owner Binding 租约

        :param db: 数据库会话
        :param obj: HASN Node Owner Binding 租约 ID 列表
        :return:
        """
        count = await hasn_node_bindings_dao.delete(db, obj.pks)
        return count

    @staticmethod
    async def get_active_binding(*, db: AsyncSession, node_id: str, owner_id: str) -> HasnNodeBindings | None:
        # Core/05 §5.2 租约失效：status=active 且 expires_at > now 才算有效（expires_at 列
        # NOT NULL，默认绑定 +7 天、可续期）。过期 binding 即使 status 仍 active 也 MUST NOT
        # 参与路由 / 鉴权；异常 NULL（不该发生）经 SQL 三值逻辑天然 fail-closed 不返回。
        now = timezone.now()
        result = await db.execute(
            select(HasnNodeBindings).where(
                HasnNodeBindings.node_id == node_id,
                HasnNodeBindings.owner_id == owner_id,
                HasnNodeBindings.status == 'active',
                HasnNodeBindings.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def add_owner_binding(
        *,
        db: AsyncSession,
        node_id: str,
        owner_id: str,
        auth_profile: str,
        scopes: dict | None = None,
        expires_at=None,
    ) -> HasnNodeBindings:
        existing = await HasnNodeBindingsService.get_active_binding(db=db, node_id=node_id, owner_id=owner_id)
        if existing:
            return existing

        if expires_at is None:
            expires_at = timezone.now() + timedelta(days=7)

        binding = HasnNodeBindings(
            binding_id=f"ob_{uuid.uuid4().hex[:12]}",
            node_id=node_id,
            owner_id=owner_id,
            auth_profile=auth_profile,
            scopes=scopes or {'bind_owner': True, 'register_agent': True},
            status='active',
            bound_at=timezone.now(),
            expires_at=expires_at,
            last_used_at=timezone.now(),
        )
        db.add(binding)
        await db.flush()
        return binding

    @staticmethod
    async def renew_owner_binding(
        *,
        db: AsyncSession,
        node_id: str,
        owner_id: str,
        expires_at,
    ) -> HasnNodeBindings:
        binding = await HasnNodeBindingsService.get_active_binding(db=db, node_id=node_id, owner_id=owner_id)
        if not binding:
            raise errors.NotFoundError(msg='Owner Binding 不存在')
        binding.expires_at = expires_at or (timezone.now() + timedelta(days=7))
        binding.renewed_at = timezone.now()
        binding.last_used_at = timezone.now()
        await db.flush()
        return binding

    @staticmethod
    async def remove_owner_binding(*, db: AsyncSession, node_id: str, owner_id: str) -> bool:
        binding = await HasnNodeBindingsService.get_active_binding(db=db, node_id=node_id, owner_id=owner_id)
        if not binding:
            return False
        binding.status = 'removed'
        binding.updated_time = timezone.now()
        await db.flush()
        return True

    @staticmethod
    async def list_active_bindings(*, db: AsyncSession, node_id: str) -> Sequence[HasnNodeBindings]:
        # Core/05 §5.2: 仅返回未过期的 active 租约（过期视为已失效，不参与路由）。
        now = timezone.now()
        result = await db.execute(
            select(HasnNodeBindings).where(
                HasnNodeBindings.node_id == node_id,
                HasnNodeBindings.status == 'active',
                HasnNodeBindings.expires_at > now,
            ).order_by(HasnNodeBindings.created_time.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def expire_stale_bindings(*, db: AsyncSession) -> int:
        """Sweeper：把已过期（expires_at <= now）仍标 active 的租约改 status=expired。

        查询热路径已用 expires_at 过滤兜住安全（过期 binding 不参与路由）；本 sweeper
        负责把状态落实为 expired，供设备管理页 / 审计反映真实租约状态。返回清理条数。
        """
        now = timezone.now()
        result = await db.execute(
            update(HasnNodeBindings)
            .where(
                HasnNodeBindings.status == 'active',
                HasnNodeBindings.expires_at <= now,
            )
            .values(status='expired', updated_time=now)
        )
        return result.rowcount or 0


hasn_node_bindings_service: HasnNodeBindingsService = HasnNodeBindingsService()
