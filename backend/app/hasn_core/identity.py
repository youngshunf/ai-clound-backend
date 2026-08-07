"""身份 façade —— 平台核心身份契约（humans / agents）。

架构候选③ P1（方案 A）：把身份的 DAO 点查与模型访问收敛到**单一 sanctioned 接缝**。

- **点查**走 `identity.get_human(...)` / `identity.get_agent(...)`（返回 ORM 模型，零行为变化），
  或要轻量只读 DTO 时走 `identity.ref_human(...)` / `identity.ref_agent(...)`。
- **SQL JOIN** 类访问从本模块 import `HasnHumans` / `HasnAgents` 模型（方案 A：身份模型升格为
  平台公开契约），而不是 `from backend.app.hasn.model… import …` 抠内脏。

实现仍复用 `app/hasn` 现有 `hasn_humans_dao` / `hasn_agents_dao`（在接缝后私有化），故迁移期
**零行为变化**：兄弟模块的 callsite 只需把 import 源从 `app.hasn.crud/model` 换成 `hasn_core`。

身份投影切片（`docs/.../02-身份投影切片施工清单.md` I1）在此基础上补齐一组**只返回只读 DTO**
的方法，覆盖 20 个 AI-Native 应用模块里"点查 + 归属/状态条件"（B档）与"批量按 id 取展示字段"
两类真实用法，调用方从此不必（也不被允许）自己 import `HasnHumans`/`HasnAgents` 拼 WHERE 条件：

- `refs_for_humans` / `refs_for_agents`：批量按 hasn_id 取只读投影，供"列表富化"场景一次
  `IN (...)` 查询后在调用方内存里合并，替代此前"JOIN 身份表回填展示列"的写法。
- `agent_owned_by`：点查 + 归属（`owner_id`）+ 可选在架状态（`status='active'` 且未软删）
  校验合一，调用方不再自己拼这三个条件。
- `agents_of_owner`：按 owner 取**全部**（不筛状态）分身列表，用于"我的分身"这类需要展示
  已下线分身的场景；仅需在架分身的既有 `active_agents_of_owner` 保持不变。
- `unsearchable_human_hasn_ids_subquery`：可被搜索性过滤的子查询构造器，供调用方
  `.notin_(...)` 拼接，替代此前"JOIN 身份表 + 读 community_settings 字段"的写法——
  子查询对象本身不暴露 `HasnHumans` 类，调用方仍不需要 import 它。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

# 方案 A：身份 DAO 与模型在本接缝后复用 app/hasn 现有实现（实现私有，对外只露 hasn_core）。
from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class HumanRef:
    """人类身份的轻量只读 DTO（跨模块展示/引用用，不暴露可变 ORM 实例）。"""

    hasn_id: str
    star_id: str
    user_id: int
    nickname: str
    avatar: str | None
    bio: str | None
    status: str

    @classmethod
    def from_model(cls, m: Any) -> HumanRef:
        return cls(
            hasn_id=m.hasn_id,
            star_id=m.star_id,
            user_id=m.user_id,
            nickname=m.nickname,
            avatar=m.avatar,
            bio=m.bio,
            status=m.status,
        )


@dataclass(frozen=True)
class AgentRef:
    """分身身份的轻量只读 DTO。``owner_hasn_id`` 对应模型 ``owner_id``（所属 Human 的 hasn_id）。"""

    hasn_id: str
    star_id: str
    owner_hasn_id: str
    display_name: str
    agent_name: str
    avatar: str | None
    profession: str | None
    bio: str | None
    description: str | None

    @classmethod
    def from_model(cls, m: Any) -> AgentRef:
        return cls(
            hasn_id=m.hasn_id,
            star_id=m.star_id,
            owner_hasn_id=m.owner_id,
            display_name=m.display_name,
            agent_name=m.agent_name,
            avatar=m.avatar,
            profession=m.profession,
            bio=m.bio,
            description=m.description,
        )


class IdentityFacade:
    """平台核心身份门面：点查走这里（薄委派现有 DAO，返回 ORM 模型，零行为变化）。

    需要 SQL JOIN 时从 `hasn_core` import `HasnHumans` / `HasnAgents` 模型（方案 A）。
    """

    # ---- humans ----

    async def get_human(self, db: AsyncSession, *, hasn_id: str) -> HasnHumans | None:
        return await hasn_humans_dao.get_by_hasn_id(db, hasn_id)

    async def get_human_by_user_id(self, db: AsyncSession, *, user_id: int) -> HasnHumans | None:
        return await hasn_humans_dao.get_by_user_id(db, user_id)

    async def get_human_by_star_id(self, db: AsyncSession, *, star_id: str) -> HasnHumans | None:
        return await hasn_humans_dao.get_by_star_id(db, star_id)

    async def ref_human(self, db: AsyncSession, *, hasn_id: str) -> HumanRef | None:
        model = await self.get_human(db, hasn_id=hasn_id)
        return HumanRef.from_model(model) if model is not None else None

    # ---- agents ----

    async def get_agent(self, db: AsyncSession, *, hasn_id: str) -> HasnAgents | None:
        return await hasn_agents_dao.get_by_hasn_id(db, hasn_id)

    async def get_agent_by_star_id(self, db: AsyncSession, *, star_id: str) -> HasnAgents | None:
        return await hasn_agents_dao.get_by_star_id(db, star_id)

    async def active_agents_of_owner(self, db: AsyncSession, *, owner_hasn_id: str) -> Sequence[HasnAgents]:
        return await hasn_agents_dao.get_active_agents_by_owner(db, owner_hasn_id)

    async def ref_agent(self, db: AsyncSession, *, hasn_id: str) -> AgentRef | None:
        model = await self.get_agent(db, hasn_id=hasn_id)
        return AgentRef.from_model(model) if model is not None else None

    # ---- 身份投影切片 I1：只返回 DTO 的批量/条件查询 ----

    async def refs_for_humans(self, db: AsyncSession, *, hasn_ids: Sequence[str]) -> dict[str, HumanRef]:
        """按 hasn_id 批量取人类身份只读投影，返回 ``{hasn_id: HumanRef}``。

        单条 `IN (...)` 查询，供"列表富化"场景替代此前"JOIN 身份表回填展示列"的写法；
        查不到的 id 不会出现在返回字典里（调用方按 `.get(hid)` 处理缺失，不要假设全命中）。
        """
        if not hasn_ids:
            return {}
        rows = (
            await db.execute(sa.select(HasnHumans).where(HasnHumans.hasn_id.in_(hasn_ids)))
        ).scalars().all()
        return {m.hasn_id: HumanRef.from_model(m) for m in rows}

    async def refs_for_agents(self, db: AsyncSession, *, hasn_ids: Sequence[str]) -> dict[str, AgentRef]:
        """按 hasn_id 批量取分身身份只读投影，返回 ``{hasn_id: AgentRef}``。语义同 `refs_for_humans`。"""
        if not hasn_ids:
            return {}
        rows = (
            await db.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id.in_(hasn_ids)))
        ).scalars().all()
        return {m.hasn_id: AgentRef.from_model(m) for m in rows}

    async def agent_owned_by(
        self,
        db: AsyncSession,
        *,
        hasn_id: str,
        owner_hasn_id: str,
        require_active: bool = True,
    ) -> AgentRef | None:
        """按 hasn_id 取分身，同时校验归属（``owner_id == owner_hasn_id``）。

        ``require_active=True``（默认）时再叠加在架状态校验（``status == 'active'`` 且未软删）；
        不存在 / 越权 / 不满足状态要求统一返回 ``None``——调用方不再需要自己 import `HasnAgents`
        拼这三个条件的 WHERE 子句（施工清单 §3 B档「点查 + 归属/状态条件」的收敛点）。
        """
        stmt = sa.select(HasnAgents).where(HasnAgents.hasn_id == hasn_id, HasnAgents.owner_id == owner_hasn_id)
        if require_active:
            stmt = stmt.where(HasnAgents.status == 'active', HasnAgents.deleted_at.is_(None))
        model = (await db.execute(stmt)).scalars().first()
        return AgentRef.from_model(model) if model is not None else None

    async def agents_of_owner(self, db: AsyncSession, *, owner_hasn_id: str) -> Sequence[AgentRef]:
        """按 owner_hasn_id 取该主人名下**全部**分身（不筛状态），按创建时间倒序。

        用于「我的分身」这类需要连已下线分身也一并展示的场景；只要在架分身请用
        既有 `active_agents_of_owner`（保持返回 ORM 不变，存量兼容）。
        """
        rows = (
            await db.execute(
                sa.select(HasnAgents)
                .where(HasnAgents.owner_id == owner_hasn_id)
                .order_by(HasnAgents.created_time.desc())
            )
        ).scalars().all()
        return [AgentRef.from_model(m) for m in rows]

    def unsearchable_human_hasn_ids_subquery(self) -> Any:
        """返回"设为不可搜索"的 human hasn_id 子查询。

        判据：``community_settings.searchable`` 或 ``community_settings.show_profile``
        任一显式为字符串 ``'false'``（缺省/其它取值一律视为可搜索，与既有
        `IS DISTINCT FROM 'false'` 判据同语义的补集）。供调用方
        ``content_model.author_hasn_id.notin_(identity.unsearchable_human_hasn_ids_subquery())``
        拼接可见性过滤——只拿到一个可组合的 `Select`，不需要 import `HasnHumans` 本身。
        """
        return sa.select(HasnHumans.hasn_id).where(
            sa.or_(
                HasnHumans.community_settings['searchable'].astext == 'false',
                HasnHumans.community_settings['show_profile'].astext == 'false',
            )
        )


identity = IdentityFacade()

__all__ = [
    'AgentRef',
    'HasnAgents',
    'HasnHumans',
    'HumanRef',
    'IdentityFacade',
    'hasn_agents_dao',
    'hasn_humans_dao',
    'identity',
]
