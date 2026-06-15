"""身份 façade —— 平台核心身份契约（humans / agents）。

架构候选③ P1（方案 A）：把身份的 DAO 点查与模型访问收敛到**单一 sanctioned 接缝**。

- **点查**走 `identity.get_human(...)` / `identity.get_agent(...)`（返回 ORM 模型，零行为变化），
  或要轻量只读 DTO 时走 `identity.ref_human(...)` / `identity.ref_agent(...)`。
- **SQL JOIN** 类访问从本模块 import `HasnHumans` / `HasnAgents` 模型（方案 A：身份模型升格为
  平台公开契约），而不是 `from backend.app.hasn.model… import …` 抠内脏。

实现仍复用 `app/hasn` 现有 `hasn_humans_dao` / `hasn_agents_dao`（在接缝后私有化），故迁移期
**零行为变化**：兄弟模块的 callsite 只需把 import 源从 `app.hasn.crud/model` 换成 `hasn_core`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
