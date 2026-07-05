from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_platform_operator_grants import hasn_platform_operator_grants_dao
from backend.app.hasn.model import HasnAgents, HasnHumans, HasnPlatformOperatorGrants
from backend.app.hasn.schema.hasn_platform_operator_grants import (
    CreateHasnPlatformOperatorGrantsParam,
    DeleteHasnPlatformOperatorGrantsParam,
    OperatorGrantAgentOption,
    OperatorGrantOwnerOption,
    OperatorGrantScopeOption,
    UpdateHasnPlatformOperatorGrantsParam,
)
from backend.app.mcp.platform_scopes import PRIVILEGED_SCOPES, is_valid_privileged_grant
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.common.security.agent_jwt import invalidate_privileged_grants_cache

# 用户下拉限量（keyword 搜索时收窄，避免全量用户涌入下拉）
_OWNER_OPTIONS_LIMIT = 50


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

    async def create_batch(
        self, *, db: AsyncSession, agent_hasn_id: str, scopes: list[str], granted_by: str, note: str | None = None
    ) -> int:
        """批量授予：给同一分身一次授予多个特权 scope（幂等·授予即时生效）。

        数据层仍「一行一 (agent, scope)」——本方法把多选展开成多行：
        - 逐个校验特权合法性（非特权直接拒，防误灌）；
        - 保序去重入参，跳过该分身已存在的 (agent, scope)（幂等，重复授予不报错）；
        - 只有真正新建了行才清一次缓存（doc18 §4.1 授予即时生效）。

        :return: 本次实际新建的授予行数（已存在的不计）
        """
        # 先整体校验，任一非法则全拒（不做半批落库）
        for scope in scopes:
            self._validate_grant(scope)
        wanted = list(dict.fromkeys(scopes))  # 保序去重
        existing_rows = await db.execute(
            select(HasnPlatformOperatorGrants.scope).where(HasnPlatformOperatorGrants.agent_hasn_id == agent_hasn_id)
        )
        existing = set(existing_rows.scalars().all())
        to_create = [s for s in wanted if s not in existing]
        for scope in to_create:
            await hasn_platform_operator_grants_dao.create(
                db,
                CreateHasnPlatformOperatorGrantsParam(
                    agent_hasn_id=agent_hasn_id, scope=scope, granted_by=granted_by, note=note
                ),
            )
        if to_create:
            await invalidate_privileged_grants_cache(agent_hasn_id)
        return len(to_create)

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

    @staticmethod
    async def list_owner_options(*, db: AsyncSession, keyword: str | None = None) -> list[OperatorGrantOwnerOption]:
        """授予对象·用户下拉：列 HASN 用户（可按昵称/hasn_id 关键字收窄，限量）。"""
        stmt = select(HasnHumans.hasn_id, HasnHumans.nickname)
        kw = (keyword or '').strip()
        if kw:
            like = f'%{kw}%'
            stmt = stmt.where(HasnHumans.nickname.ilike(like) | HasnHumans.hasn_id.ilike(like))
        stmt = stmt.order_by(HasnHumans.nickname).limit(_OWNER_OPTIONS_LIMIT)
        rows = (await db.execute(stmt)).all()
        return [OperatorGrantOwnerOption(hasn_id=r.hasn_id, nickname=r.nickname or r.hasn_id) for r in rows]

    @staticmethod
    async def list_agent_options(*, db: AsyncSession, owner_hasn_id: str) -> list[OperatorGrantAgentOption]:
        """授予对象·分身下拉：列某 owner 名下全部分身（按显示名排序）。"""
        owner = (owner_hasn_id or '').strip()
        if not owner:
            return []
        stmt = (
            select(HasnAgents.hasn_id, HasnAgents.display_name, HasnAgents.agent_name, HasnAgents.profession)
            .where(HasnAgents.owner_id == owner)
            .order_by(HasnAgents.display_name)
        )
        rows = (await db.execute(stmt)).all()
        return [
            OperatorGrantAgentOption(
                hasn_id=r.hasn_id,
                display_name=r.display_name or r.agent_name or r.hasn_id,
                agent_name=r.agent_name or '',
                profession=r.profession,
            )
            for r in rows
        ]

    @staticmethod
    def list_scope_options() -> list[OperatorGrantScopeOption]:
        """特权 scope 目录（声明驱动·只读）：PRIVILEGED_SCOPES 权威全集 + 展示元数据。

        特权与否是工具的固有安全属性，由工具声明 + PRIVILEGED_SCOPES 权威 + 前缀守卫决定
        （doc18 §4.1 / 实施103 U2）。此处只读暴露给授予页做下拉——新工具声明的特权 scope
        会自动出现（守卫保证工具不会声明名单外的特权 scope），Admin 不手工维护「哪些算特权」。

        展示元数据（label/risk/描述）从**聚合词表** `scope_meta` 取（各应用目录 scopes.py 落地、
        由 app/mcp/scopes.py 聚合，diag 亦如此），此处只管「哪些 scope 算特权」不重复声明元数据。
        """
        from backend.app.mcp.scopes import scope_meta

        options: list[OperatorGrantScopeOption] = []
        for scope in sorted(PRIVILEGED_SCOPES):
            meta = scope_meta(scope)
            options.append(
                OperatorGrantScopeOption(
                    scope=scope,
                    label_zh=meta.get('label') or scope,
                    risk=meta.get('risk') or 'medium',
                    description=meta.get('description') or '平台运维特权',
                )
            )
        return options


hasn_platform_operator_grants_service: HasnPlatformOperatorGrantsService = HasnPlatformOperatorGrantsService()
