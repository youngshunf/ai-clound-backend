"""
MCP 认证中间件

使用 FastAPI Depends 机制验证 Agent JWT 并注入 AgentContext
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import hasn_agents_dao
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.security.agent_jwt import (
    get_agent_scopes_cached,
    get_privileged_grants_cached,
    verify_agent_token,
)
from backend.common.security.scope_policy import MODE_DENY
from backend.database.db import async_db_session
from backend.utils.timezone import timezone


class AgentContext:
    """Agent 执行上下文"""

    def __init__(
        self,
        hasn_id: str,
        owner_id: int,
        agent_status: str,
        metadata: dict,
        agent_name: str = '',
        owner_hasn_id: str | None = None,
        session_uuid: str | None = None,
        token_payload: AgentTokenPayload | None = None,
        default_mode: str = 'allow',
        capability_modes: dict | None = None,
        runtime_location: str = 'cloud',
    ) -> None:
        self.hasn_id = hasn_id
        self.owner_id = owner_id
        self.agent_status = agent_status
        self.metadata = metadata
        self.agent_name = agent_name
        self.owner_hasn_id = owner_hasn_id
        self.session_uuid = session_uuid
        self._token_payload = token_payload
        # 运行位置（local/cloud/remote）快照，鉴权时从 HasnAgents.runtime_location 灌入。
        # ⚠️ 不参与工具暴露判定——工具暴露只受权限 + 付费墙限制，与分身在本地/云端无关
        # （福仔 2026-07-10 拍板退役原 TOOLMIG2-P4「运行位置收口」）。此字段仅供其它用途
        # （如 runtime 派发分叉：cloud 走云端 runtime、local 走本地 sidecar）。
        self.runtime_location = runtime_location
        # 维度① 三态能力授权（D3：消费时活取，凭证不再承载授权权威）。
        # 默认全开（allow）；streamable 鉴权后用 get_agent_scopes_cached 现查覆盖。
        self.default_mode = default_mode
        self.capability_modes = capability_modes or {}
        # register-on-write（doc31/32 RC-P8 泛化）：本次工具调用所属的**工作会话 id**——分身经工作
        # 会话派发时 Hermes/daemon 在出站 MCP 调用戳进 `_hasn_session_id`，server.call_tool dispatch
        # 前剥离并落此字段（工具体不见），供 deck/app 写点把产出登记进「工作会话资源栏」。
        # 主会话直调 / 非工作会话 → None（产物仍凭 resource_uri 进产物 tab）。
        self.work_session_id: str | None = None
        # P7 第三方 MCP 网关：本请求该 Agent 可发现/可调的 external 工具 canonical 名集合
        # （gate1 owner 启用 + gate2 agent binding，由 server.py 每次调用前注入）。
        # external 工具全局共享注册表实例，但发现/调用资格按此集合 per-request 过滤，杜绝串号。
        self.external_allowed_tools: set[str] = set()
        # G1 平台特权门（doc18 §4.1）：Admin 授予表 ∪ ENV bootstrap 的授予值集合
        # （精确 scope 或段尾通配）。默认空 = 特权工具全不可见；两凭证入口鉴权后
        # 用 get_privileged_grants_cached 现查灌入。与已废弃的凭证 scopes 无关。
        self.granted_privileged_scopes: frozenset[str] = frozenset()
        # G3 应用权益门（doc18 §4.3·U3）：主人当前激活企业空间 id（None=个人空间）+
        # per-request 预取的「app_id → 合并准入 dict」map（103 §4 性能条：evaluate 纯查此
        # map、零 IO）。默认空 map = 不挂门（never over-block）；两凭证入口鉴权后由
        # inject_app_access 灌入。免费应用 / 未预取 → 该 app_id 缺席即放行。
        self.active_enterprise_id: int | None = None
        self.app_access_by_id: dict[str, dict] = {}
        # G4 企业角色门（doc18 §4.4·U4）：主人在当前激活企业的「已授予能力族键」集合，
        # 随 active_enterprise_id 一并预取（owner 角色→能力族一次取回）。
        # ⚠️ 外部硬依赖 doc12/02 企业功能权限策略表（角色→能力族）**尚未落地**，故当前恒为
        # None——None = 未解析 → G4 门跳过（inert，never over-block）。策略表落地后由
        # inject_app_access 同步灌入 frozenset，G4 门即生效（evaluate 结构不动）。
        self.enterprise_capability_grants: frozenset[str] | None = None

    @classmethod
    def from_token_payload(
        cls,
        payload: AgentTokenPayload,
        *,
        agent_status: str,
        metadata: dict | None = None,
        default_mode: str = 'allow',
        capability_modes: dict | None = None,
        runtime_location: str = 'cloud',
    ) -> 'AgentContext':
        return cls(
            hasn_id=payload.agent_hasn_id,
            owner_id=payload.owner_user_id,
            agent_status=agent_status,
            metadata=metadata or {},
            agent_name=payload.agent_name,
            owner_hasn_id=payload.owner_hasn_id,
            session_uuid=payload.session_uuid,
            token_payload=payload,
            default_mode=default_mode,
            capability_modes=capability_modes,
            runtime_location=runtime_location,
        )

    def apply_policy(self, policy: dict) -> None:
        """用现查的权限配置覆盖三态字段（D3 活取）。policy 来自 get_agent_scopes_cached。"""
        self.default_mode = policy.get('default_mode', 'allow')
        caps = policy.get('capability_modes')
        self.capability_modes = caps if isinstance(caps, dict) else {}

    def tool_mode(self, tool: object) -> str:
        """工具的有效三态（维度①）：走统一判定服务 CapabilityGuard（已预取策略，无需取库）。"""
        from backend.app.hasn_core.app_platform import capability_guard

        return capability_guard.resolve_from_policy(
            self.default_mode,
            self.capability_modes,
            tool_name=getattr(tool, 'name', ''),
            required_scopes=list(getattr(tool, 'required_scopes', []) or []),
        )

    def is_tool_denied(self, tool: object) -> bool:
        """工具是否被 owner 禁用（mode=deny → 不可见、不可调）。"""
        return self.tool_mode(tool) == MODE_DENY

    def to_token_payload(self) -> AgentTokenPayload:
        if self._token_payload is not None:
            return self._token_payload
        if self.owner_hasn_id is None or self.session_uuid is None:
            raise errors.TokenError(msg='AgentContext 缺少 Agent JWT 字段')
        return AgentTokenPayload(
            agent_hasn_id=self.hasn_id,
            agent_name=self.agent_name,
            owner_hasn_id=self.owner_hasn_id,
            owner_user_id=self.owner_id,
            session_uuid=self.session_uuid,
            expire_time=timezone.now(),
        )

    @property
    def agent_hasn_id(self) -> str:
        return self.hasn_id

    @property
    def owner_user_id(self) -> int:
        return self.owner_id


async def inject_app_access(context: AgentContext, db: AsyncSession) -> None:
    """G3 应用权益门 per-request 预取（doc18 §4.3·U3）：解析主人激活企业空间 + 批量取 app 准入。

    三个凭证入口（本文件 + streamable 的 amk/jwt 两路）鉴权后统一调用，令 evaluate 纯查
    context.app_access_by_id、零 IO。查库瞬时异常绝不阻断鉴权、也绝不 fail-open：退化为空
    map（= 不挂 G3 门，付费墙由网关 `_entitlement_denial` 在真调用时兜底，见 §4 网关收编）。
    """
    from backend.app.hasn.service import app_access_kernel
    from backend.app.mcp.tool_app_registry import GATED_APP_IDS

    owner_hasn_id = context.owner_hasn_id
    if not owner_hasn_id:
        return
    try:
        context.active_enterprise_id = await app_access_kernel.resolve_active_enterprise_id(db, owner_hasn_id)
        context.app_access_by_id = await app_access_kernel.prefetch_app_access_map(
            db,
            owner_hasn_id=owner_hasn_id,
            active_enterprise_id=context.active_enterprise_id,
            app_ids=GATED_APP_IDS,
        )
    except Exception as exc:
        from backend.common.log import log

        log.warning(f'app access prefetch failed for {owner_hasn_id}, G3 gate skipped: {exc!r}')
        context.active_enterprise_id = None
        context.app_access_by_id = {}


async def get_agent_context(
    authorization: Annotated[str, Header()], x_hasn_agent_id: Annotated[str, Header(alias='X-HASN-Agent-ID')]
) -> AgentContext:
    """
    验证 Agent JWT 并返回执行上下文

    使用 FastAPI Depends 机制，在路由层注入 AgentContext

    Args:
        authorization: Bearer token
        x_hasn_agent_id: Agent HASN ID

    Returns:
        AgentContext: Agent 执行上下文

    Raises:
        HTTPException: 认证失败
    """
    # 提取 token
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid authorization header')

    token = authorization[7:]  # 移除 "Bearer " 前缀

    # 验证 Agent JWT and its revocable Redis-backed session record.
    try:
        payload = await verify_agent_token(token)
    except errors.TokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f'Invalid token: {e!s}')

    # 验证 hasn_id 匹配
    if payload.agent_hasn_id != x_hasn_agent_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Agent ID mismatch')

    # 加载 Agent 信息验证状态
    async with async_db_session() as db:
        agent = await hasn_agents_dao.get_by_hasn_id(db, hasn_id=x_hasn_agent_id)

        if not agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Agent not found')

        # 检查 Agent 状态
        if agent.status != 'active':
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'Agent is {agent.status}')

        context = AgentContext.from_token_payload(
            payload,
            agent_status=agent.status,
            runtime_location=getattr(agent, 'runtime_location', 'cloud') or 'cloud',
        )
        # D3 消费时活取：JWT scopes 仅审计快照，三态判定现查 DB（凭证与授权解耦）。
        policy = await get_agent_scopes_cached(x_hasn_agent_id, db)
        context.apply_policy(policy)
        # G1 特权授予同处活取（Admin 授予表 ∪ ENV bootstrap，doc18 §4.1）
        context.granted_privileged_scopes = await get_privileged_grants_cached(x_hasn_agent_id, db)
        # G3 应用权益门 per-request 预取（doc18 §4.3·U3）
        await inject_app_access(context, db)
        return context


# 类型别名，方便在路由中使用
AgentContextDep = Annotated[AgentContext, Depends(get_agent_context)]
