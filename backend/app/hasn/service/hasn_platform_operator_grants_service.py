from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_platform_operator_grants import hasn_platform_operator_grants_dao
from backend.app.hasn.model import HasnPlatformOperatorGrants
from backend.app.hasn.schema.hasn_platform_operator_grants import (
    CreateHasnPlatformOperatorGrantsParam,
    DeleteHasnPlatformOperatorGrantsParam,
    UpdateHasnPlatformOperatorGrantsParam,
)
from backend.app.mcp.platform_scopes import is_valid_privileged_grant
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.common.security.agent_jwt import invalidate_privileged_grants_cache


class HasnPlatformOperatorGrantsService:
    @staticmethod
    def _validate_grant(scope: str) -> None:
        """守卫：本表只承载 G1 特权授予（前缀 diag:/ops:/platform: + 精确或段尾整段通配）。

        非特权 scope 一律拒绝——防 Admin 误把普通能力灌进特权授予源（doc18 §4.1）。
        """
        if not is_valid_privileged_grant(scope):
            raise errors.RequestError(
                msg=f'非法特权授予值：{scope}（须为 diag:/ops:/platform: 前缀 + 精确值或段尾整段通配 如 ops:*）'
            )

    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnPlatformOperatorGrants:
        """
        获取平台运维授予源（Admin-only·G1 特权门）

        :param db: 数据库会话
        :param pk: 平台运维授予源（Admin-only·G1 特权门） ID
        :return:
        """
        hasn_platform_operator_grants = await hasn_platform_operator_grants_dao.get(db, pk)
        if not hasn_platform_operator_grants:
            raise errors.NotFoundError(msg='平台运维授予源（Admin-only·G1 特权门）不存在')
        return hasn_platform_operator_grants

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取平台运维授予源（Admin-only·G1 特权门）列表

        :param db: 数据库会话
        :return:
        """
        hasn_platform_operator_grants_select = await hasn_platform_operator_grants_dao.get_select()
        return await paging_data(db, hasn_platform_operator_grants_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnPlatformOperatorGrants]:
        """
        获取所有平台运维授予源（Admin-only·G1 特权门）

        :param db: 数据库会话
        :return:
        """
        hasn_platform_operator_grants_list = await hasn_platform_operator_grants_dao.get_all(db)
        return hasn_platform_operator_grants_list

    async def create(self, *, db: AsyncSession, obj: CreateHasnPlatformOperatorGrantsParam) -> None:
        """
        创建平台运维授予源（Admin-only·G1 特权门）——授予即时生效（清缓存）

        :param db: 数据库会话
        :param obj: 创建平台运维授予源（Admin-only·G1 特权门）参数
        :return:
        """
        self._validate_grant(obj.scope)
        await hasn_platform_operator_grants_dao.create(db, obj)
        # 授予即时生效（消费时活取 + 缓存失效，doc18 §4.1）
        await invalidate_privileged_grants_cache(obj.agent_hasn_id)

    async def update(self, *, db: AsyncSession, pk: int, obj: UpdateHasnPlatformOperatorGrantsParam) -> int:
        """
        更新平台运维授予源（Admin-only·G1 特权门）——改授予即时生效（清缓存）

        :param db: 数据库会话
        :param pk: 平台运维授予源（Admin-only·G1 特权门） ID
        :param obj: 更新平台运维授予源（Admin-only·G1 特权门）参数
        :return:
        """
        self._validate_grant(obj.scope)
        # 取旧行的 agent_hasn_id（改指后旧分身也要清缓存）
        old = await hasn_platform_operator_grants_dao.get(db, pk)
        count = await hasn_platform_operator_grants_dao.update(db, pk, obj)
        if count > 0:
            affected = {obj.agent_hasn_id}
            if old is not None:
                affected.add(old.agent_hasn_id)
            for agent_hasn_id in affected:
                await invalidate_privileged_grants_cache(agent_hasn_id)
        return count

    async def delete(self, *, db: AsyncSession, obj: DeleteHasnPlatformOperatorGrantsParam) -> int:
        """
        删除平台运维授予源（Admin-only·G1 特权门）——撤销即时生效（清缓存）

        :param db: 数据库会话
        :param obj: 平台运维授予源（Admin-only·G1 特权门） ID 列表
        :return:
        """
        # 删前收集受影响分身（删后行没了，无法反查）
        affected: set[str] = set()
        for pk in obj.pks:
            row = await hasn_platform_operator_grants_dao.get(db, pk)
            if row is not None:
                affected.add(row.agent_hasn_id)
        count = await hasn_platform_operator_grants_dao.delete(db, obj.pks)
        if count > 0:
            for agent_hasn_id in affected:
                await invalidate_privileged_grants_cache(agent_hasn_id)
        return count


hasn_platform_operator_grants_service: HasnPlatformOperatorGrantsService = HasnPlatformOperatorGrantsService()
