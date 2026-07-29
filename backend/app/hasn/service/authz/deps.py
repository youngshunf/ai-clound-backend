"""G6 统一资源权限门·HTTP 面依赖工厂（doc33 §S5 / doc32 §8·§15）。

`DependsResourceAccess(resource_type, need, path_param)` 把「按凭证类型构造 `Subject` → 过
`resource_gate.require` → 拿 `AuthorizedResource`」这条 human app 面 / agent REST 面**都要照做**的
纪律收成一个 FastAPI 依赖：两类路由同一个依赖、同一份错误语义（403 `RESOURCE_PERMISSION_INSUFFICIENT`
/ 404 存在性隐藏），从此**新路由不再各写一遍 `_resolve_owner + service 内判权`**（正是本缺陷「人类面
接了、分身面漏了」的漂移形态收口）。

两条凭证支线（按 `subject_kind`）：
- `owner`（默认·Owner JWT）：`request.user.id` → `hasn_humans_dao` → `Subject.human`；
- `agent`（Agent JWT）：`DependsAgentJwtAuth` 解析出的 `AgentTokenPayload` → `Subject.agent`（分身继承主人权限）。

**本仓既有教训**：路由依赖不得放 `TYPE_CHECKING` 块（ruff TC 会把 `Depends(...)` 默认值判成仅类型
引用而删导入 → OpenAPI 生成期 NameError）。故本文件所有运行期符号一律顶层 import。

层次纪律（doc32 §3）：本文件是**门的 HTTP 适配胶水**（解析身份、取 path 参数），可 import 身份层
`hasn_core` 与 `common/security`；判权内核 `resource_gate.require` 仍不反向 import 任何 `app/hasn_*` 应用模块。
"""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from backend.app.hasn.service.authz import resource_gate
from backend.app.hasn.service.authz.resource_gate import AuthorizedResource
from backend.app.hasn.service.authz.subject import Subject
from backend.app.hasn_core import hasn_humans_dao
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession


# 工厂返回 Depends()，命名对齐 FastAPI 依赖惯例（PascalCase），故豁免 N802。
def DependsResourceAccess(  # noqa: N802
    resource_type: str,
    need: str,
    path_param: str,
    *,
    subject_kind: str = 'owner',
):
    """构造一个 FastAPI 依赖：判过返回 `AuthorizedResource`，不过抛 403/404（语义同 `resource_gate.require`）。

    :param resource_type: G6 已注册的资源类型（如 `'deck'`、`'studio_project'`）。
    :param need: 所需档位 `'viewer' | 'editor' | 'manager'`。
    :param path_param: 资源 id 所在的路径参数名（如 `'deck_id'`）——从 `request.path_params` 取。
    :param subject_kind: `'owner'`（Owner JWT，默认）或 `'agent'`（Agent JWT）。
    """
    if subject_kind == 'agent':

        async def _dep_agent(
            request: Request,
            db: CurrentSession,
            agent: AgentTokenPayload = DependsAgentJwtAuth,
        ) -> AuthorizedResource:
            subject = Subject.agent(agent.agent_hasn_id, agent.owner_hasn_id)
            resource_id = str(request.path_params[path_param])
            return await resource_gate.require(
                db, subject, resource_type=resource_type, resource_id=resource_id, need=need
            )

        # 挂声明元数据供守卫内省（S7 共享治理门守卫按此核对「路由带 need=manager 声明」）。
        setattr(
            _dep_agent,
            '_g6_resource_access',
            {'resource_type': resource_type, 'need': need, 'path_param': path_param},
        )
        return Depends(_dep_agent)

    async def _dep_owner(
        request: Request,
        db: CurrentSession,
        # 声明 DependsJwtAuth 子依赖：保证 Owner JWT 认证先跑（request.user 就绪）后再解析主人身份。
        _auth: HTTPAuthorizationCredentials = DependsJwtAuth,
    ) -> AuthorizedResource:
        human = await hasn_humans_dao.get_by_user_id(db, request.user.id)
        if not human:
            raise errors.NotFoundError(msg='用户 HASN 身份不存在')
        subject = Subject.human(human.hasn_id)
        resource_id = str(request.path_params[path_param])
        return await resource_gate.require(db, subject, resource_type=resource_type, resource_id=resource_id, need=need)

    # 挂声明元数据供守卫内省（S7 共享治理门守卫按此核对「路由带 need=manager 声明」）。
    setattr(
        _dep_owner,
        '_g6_resource_access',
        {'resource_type': resource_type, 'need': need, 'path_param': path_param},
    )
    return Depends(_dep_owner)
