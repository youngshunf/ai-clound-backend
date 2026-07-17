"""hasn_im.adapters.sqlalchemy_identity_view · 身份只读视图实现（§9.3 阶段一·R2-09）

`astra_im_service` 授权的**同库身份只读投影**：只读 `hasn_humans` / `hasn_agents` 的
`hasn_id / status / owner_id`，派生最小 `IdentityRef`。绝不写身份表（身份生命周期属身份域）。

**存活判据**（对齐两张身份表的 status 语义）：
- human `hasn_humans.status`：`active` 存活；`suspended` / `deleted` 停用；
- agent `hasn_agents.status`：`active` 存活；`disabled` / `revoked` / `archived` / `deleted` 停用。

**owner_id**：human 自身即主人（= 自己的 hasn_id）；agent 取 `hasn_agents.owner_id`。

**解析路由**：按 `hasn_id` 前缀（human `h_` / agent `a_`）择表；前缀不识别（群 `g:`、系统主体等）
返回 None——它们不是「身份」，不经本视图。fail-closed 由 `ports.require_active` 统一施加。

依赖方向（§0.1）：adapter 层**允许**读现网身份 model（收编期过渡）；业务模块只认
`hasn_im.ports.IdentityView` 抽象，不直接 import 本 adapter。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn_im.ports.identity_view import IdentityRef

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

# 存活态：human / agent 均以 status=='active' 为存活，其余生命周期态一律停用。
_ACTIVE_STATUS = 'active'
# 身份 hasn_id 前缀（08 号资源寻址规范：human h_{uuid} / agent a_{uuid}）。
_HUMAN_PREFIX = 'h_'
_AGENT_PREFIX = 'a_'


@dataclass(slots=True)
class SqlAlchemyIdentityView:
    """IdentityView 的现网实现（同库只读投影，绝不写身份表）。"""

    # 会话工厂：默认走全局 async_db_session；测试注入每测试 NullPool sessionmaker 隔离事件循环
    # （与 SqlAlchemyRelationGateway 同款测试缝）。port 契约仍「同库只读视图」，不变。
    session_factory: async_sessionmaker | None = None

    def _session(self):
        if self.session_factory is not None:
            return self.session_factory()
        from backend.database.db import async_db_session

        return async_db_session()

    async def resolve(self, hasn_id: str) -> IdentityRef | None:
        """解析身份最小投影：命中→IdentityRef（active 按 status 派生），未命中→None。"""
        from backend.app.hasn.model import HasnAgents, HasnHumans

        if hasn_id.startswith(_AGENT_PREFIX):
            async with self._session() as db:
                row = (
                    await db.execute(
                        sa.select(HasnAgents.status, HasnAgents.owner_id).where(
                            HasnAgents.hasn_id == hasn_id
                        )
                    )
                ).first()
            if row is None:
                return None
            status, owner_id = row
            return IdentityRef(
                hasn_id=hasn_id,
                kind='agent',
                active=(status == _ACTIVE_STATUS),
                owner_id=owner_id or '',
            )

        if hasn_id.startswith(_HUMAN_PREFIX):
            async with self._session() as db:
                status = await db.scalar(
                    sa.select(HasnHumans.status).where(HasnHumans.hasn_id == hasn_id)
                )
            if status is None:
                return None
            return IdentityRef(
                hasn_id=hasn_id,
                kind='human',
                active=(status == _ACTIVE_STATUS),
                owner_id=hasn_id,  # human 自身即主人
            )

        # 前缀不识别（群/系统主体等）——非身份，不经本视图。
        return None
