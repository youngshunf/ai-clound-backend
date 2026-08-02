"""模型注册表读写（Admin 面）。

写入面**只开人工标注列**：new-api 权威的成本/分组/供应商/网关状态由
``model_registry_sync_service`` 覆盖，人工改了下轮同步就被冲掉；而「手工新增一行」等于
放开手输模型名——正是本设计要消灭的事故根因，故不提供创建/删除。
"""

from typing import Any, Sequence

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_model_registry import hasn_model_registry_dao
from backend.app.hasn.model import HasnModelRegistry
from backend.app.hasn.schema.hasn_model_registry import PatchModelAnnotationParam
from backend.app.hasn.service.model_registry_sync_service import (
    CAPABILITIES,
    COST_TIERS,
    DIALECTS,
    INPUT_REQUIREMENTS,
    QUALITIES,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data

# 允许 PATCH 的人工标注列（其余列由同步器权威覆盖）。
_ANNOTATION_FIELDS = (
    'capability',
    'inputs',
    'dialect',
    'quality',
    'scenario',
    'agent_visible',
    'sort_order',
    'cost_tier_override',
)

# 可清空的可选列：传空串即置 NULL（JSON 里没法用「不传」表达「清空」）。
_CLEARABLE_FIELDS = ('dialect', 'quality', 'scenario', 'cost_tier_override')


def _validate_annotation(field: str, value: Any) -> Any:
    """校验单个标注值，非法即拒绝保存。

    枚举写错不报错、静默存进去，等于让运营以为标过了而分身其实拿不到——宁可当场拒，
    也不要留一个看起来标好了的坏值（零 fake）。
    """
    if field in _CLEARABLE_FIELDS and isinstance(value, str) and not value.strip():
        return None
    if field == 'capability':
        if value not in CAPABILITIES:
            raise errors.RequestError(msg=f'capability 非法：{value!r}，合法值 {"/".join(CAPABILITIES)}')
    elif field == 'dialect' and value is not None:
        if value not in DIALECTS:
            raise errors.RequestError(msg=f'dialect 非法：{value!r}，合法值 {"/".join(DIALECTS)}')
    elif field == 'quality' and value is not None:
        if value not in QUALITIES:
            raise errors.RequestError(msg=f'quality 非法：{value!r}，合法值 {"/".join(QUALITIES)}')
    elif field == 'cost_tier_override' and value is not None:
        if value not in COST_TIERS:
            raise errors.RequestError(msg=f'cost_tier_override 非法：{value!r}，合法值 {"/".join(COST_TIERS)}')
    elif field == 'inputs':
        if not isinstance(value, dict):
            raise errors.RequestError(msg='inputs 必须是对象')
        for key, requirement in value.items():
            if requirement not in INPUT_REQUIREMENTS:
                raise errors.RequestError(
                    msg=f'inputs.{key} 非法：{requirement!r}，合法值 {"/".join(INPUT_REQUIREMENTS)}'
                )
    return value


class HasnModelRegistryService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnModelRegistry:
        """按主键取一行；不存在即 404（不静默造一行）。"""
        row = await hasn_model_registry_dao.get(db, pk)
        if not row:
            raise errors.NotFoundError(msg='模型注册表条目不存在')
        return row

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        capability: str | None = None,
        upstream_status: str | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """分页列出注册表（可按能力类别 / 网关状态 / 模型名关键字过滤）。

        排序按「能力类别 → sort_order → 模型名」，与下发时的候选顺序一致：运营在 Admin 看到
        的先后就是分身 failover 的先后。
        """
        stmt = sa.select(HasnModelRegistry)
        if capability:
            stmt = stmt.where(HasnModelRegistry.capability == capability)
        if upstream_status:
            stmt = stmt.where(HasnModelRegistry.upstream_status == upstream_status)
        if keyword:
            stmt = stmt.where(HasnModelRegistry.model_name.ilike(f'%{keyword.strip()}%'))
        stmt = stmt.order_by(
            HasnModelRegistry.capability.asc(),
            HasnModelRegistry.sort_order.asc(),
            HasnModelRegistry.model_name.asc(),
        )
        return await paging_data(db, stmt)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnModelRegistry]:
        """取全部行（下发端点与 PDC 写入校验用）。"""
        return await hasn_model_registry_dao.get_all(db)

    @staticmethod
    async def patch_annotation(*, db: AsyncSession, pk: int, obj: PatchModelAnnotationParam) -> HasnModelRegistry:
        """局部更新人工标注列（Admin 唯一写入面）。未传的字段一律不动。"""
        row = await hasn_model_registry_dao.get(db, pk)
        if not row:
            raise errors.NotFoundError(msg='模型注册表条目不存在')
        for field, value in obj.model_dump(exclude_unset=True).items():
            if field not in _ANNOTATION_FIELDS:
                continue  # 防御：schema 已限定，这里再挡一层，避免将来加字段时漏改
            setattr(row, field, _validate_annotation(field, value))
        await db.flush()
        await db.refresh(row)
        return row


hasn_model_registry_service: HasnModelRegistryService = HasnModelRegistryService()
