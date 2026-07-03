"""Agent 权限管理服务（三态 + catalog，D2/D3/Q2）

设计事实源：93-doc §P5、13-doc §5.1/§5.2。
- get_agent_scopes：返回三态配置（default_mode + capability_modes）。
- update_agent_scopes：D3 写表 + 失效缓存，**不重签 JWT**（凭证与授权解耦，开关即时生效）。
- get_scope_catalog：D2 聚合可见工具 → 按来源分组 → 每条标三态 + scopes.py 元数据。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
from backend.app.hasn.schema.agent_scopes import (
    AgentScopesConfig,
    ScopeCatalogResponse,
    UpdateAgentScopesRequest,
    UpdateAgentScopesResponse,
)
from backend.common.exception import errors
from backend.common.response.response_code import StandardResponseCode
from backend.common.security.agent_jwt import (
    get_agent_scopes_cached,
    update_agent_modes,
)

# 审批请求非 pending 时回给主人的友好中文文案（不暴露 timeout/consumed 等英文状态机词）。
_STALE_APPROVAL_MESSAGES: dict[str, str] = {
    'timeout': '这条请求已超时，无法再确认',
    'expired': '这条请求已超时，无法再确认',
    'approved': '这条请求刚刚已经确认过了',
    'denied': '这条请求已被拒绝',
    'consumed': '这条请求已经处理过了',
}


def _friendly_stale_message(status: str) -> str:
    """把审批行的内部状态翻成主人能看懂的中文提示。"""
    return _STALE_APPROVAL_MESSAGES.get(status, '这条请求已处理，无需重复操作')


class AgentScopesService:
    """Agent 权限管理服务"""

    async def _assert_owns(self, db: AsyncSession, agent_hasn_id: str, owner_hasn_id: str, *, write: bool) -> None:
        """校验该 Agent 归属当前主人，否则 404/403。"""
        agent = await hasn_agents_dao.get_by_hasn_id(db, hasn_id=agent_hasn_id)
        if not agent:
            raise errors.NotFoundError(msg=f'Agent {agent_hasn_id} 不存在')
        if agent.owner_id != owner_hasn_id:
            verb = '修改' if write else '访问'
            raise errors.ForbiddenError(msg=f'无权{verb}该 Agent 的权限配置')

    async def _assert_owns_approval(
        self, db: AsyncSession, agent_hasn_id: str, owner_hasn_id: str, request_id: str
    ):
        """校验某审批请求确归当前主人裁决，返回该行；否则 404/403。

        **权威按审批行的 owner_hasn_id**（创建时记录、也即审批卡片送达对象），不再用
        agent.owner_id 间接判断——分身归属可能因身份迁移与历史审批不一致，会误把合法主人
        判成无权（线上 cloud-pend 点按钮 403 的根因）。审批行 owner 才是“这条审批该谁批”。
        """
        from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao

        row = await hasn_agent_approval_requests_dao.get_by_request_id(db, request_id)
        if row is None or row.agent_hasn_id != agent_hasn_id:
            raise errors.NotFoundError(msg='审批请求不存在')
        if row.owner_hasn_id != owner_hasn_id:
            raise errors.ForbiddenError(msg='无权处理该审批请求')
        return row

    def _config_from_policy(self, cfg: dict) -> AgentScopesConfig:
        return AgentScopesConfig(
            default_mode=cfg.get('default_mode', 'allow'),
            capability_modes=cfg.get('capability_modes', {}),
        )

    def _assert_known_capability_keys(self, capability_modes: dict[str, str] | None) -> None:
        """B7 键校验（实施102 S2）：capability_modes 的键必须是已知能力 scope。

        白名单 = 全局 SCOPE_CATALOG（= platform 展示词表 ∪ 各应用目录 scopes.py ∪ 本地工具 scope，
        已注册云端工具的 required_scopes 由零漂移守卫保证均有 catalog 条目）。未知键 422 报
        unknown_capability_key，避免脏键静默落库污染三态判定（旧 image:generate 这类死键即此类）。
        """
        from backend.app.mcp.scopes import SCOPE_CATALOG

        unknown = sorted(k for k in (capability_modes or {}) if k not in SCOPE_CATALOG)
        if unknown:
            raise errors.RequestError(
                code=StandardResponseCode.HTTP_422,
                msg=f'unknown_capability_key: {", ".join(unknown)}',
            )

    async def _bump_scopes(self, db: AsyncSession, owner_hasn_id: str) -> None:
        """S4·U-L4：三态授权写库后 best-effort 发射 WSPUSH ``kind=scopes`` 给该 owner 在线节点。

        push 失败不抛（离线节点靠重连握手 sync_pull 兜底追平）——绝不能因推送失败让权限写点失败。
        """
        import logging

        from backend.app.hasn.service.sync_invalidate_service import KIND_SCOPES, bump_owner

        try:
            await bump_owner(KIND_SCOPES, db, owner_id=owner_hasn_id)
        except Exception as exc:
            logging.getLogger(__name__).warning('[scopes] bump WSPUSH failed owner=%s: %s', owner_hasn_id, exc)

    async def get_agent_scopes(self, db: AsyncSession, agent_hasn_id: str, owner_hasn_id: str) -> AgentScopesConfig:
        """查询 Agent 权限配置（含三态）。"""
        await self._assert_owns(db, agent_hasn_id, owner_hasn_id, write=False)
        cfg = await get_agent_scopes_cached(agent_hasn_id, db)
        return self._config_from_policy(cfg)

    async def update_agent_scopes(
        self,
        db: AsyncSession,
        agent_hasn_id: str,
        owner_hasn_id: str,
        request: UpdateAgentScopesRequest,
    ) -> UpdateAgentScopesResponse:
        """更新 Agent 三态授权（D3：写表 + 失效缓存，不重签 JWT，即时生效）。"""
        await self._assert_owns(db, agent_hasn_id, owner_hasn_id, write=True)
        self._assert_known_capability_keys(request.capability_modes)

        await update_agent_modes(
            db=db,
            agent_hasn_id=agent_hasn_id,
            default_mode=request.default_mode,
            capability_modes=request.capability_modes,
        )
        # S4·U-L4：三态改动后 push WSPUSH kind=scopes 给该 owner 在线节点 →
        # daemon 秒级刷新 CapabilityModeMirror（治「设备 A 改了、设备 B 镜像不更新」）。
        await self._bump_scopes(db, owner_hasn_id)

        cfg = await get_agent_scopes_cached(agent_hasn_id, db)
        return UpdateAgentScopesResponse(config=self._config_from_policy(cfg))

    async def get_scope_catalog(self, db: AsyncSession, agent_hasn_id: str, owner_hasn_id: str) -> ScopeCatalogResponse:
        """工具/scope 目录（D2，按来源分组，每条带三态当前值）。

        聚合「全部已注册可见工具」的 required_scopes，叠加 scopes.py 展示元数据；
        external 分组结构保留但为空（Q5）。对象级关系门控（维度②）不进 catalog。
        """
        await self._assert_owns(db, agent_hasn_id, owner_hasn_id, write=False)

        from backend.app.mcp.auth import AgentContext
        from backend.app.mcp.server import mcp_server

        cfg = await get_agent_scopes_cached(agent_hasn_id, db)
        # catalog 仅按 owner_hasn_id 鉴权、按 agent_hasn_id 取策略聚合；owner_user_id 不参与隔离，置 0。
        ctx = AgentContext(
            hasn_id=agent_hasn_id,
            owner_id=0,
            agent_status='active',
            metadata={},
            owner_hasn_id=owner_hasn_id,
            session_uuid=f'catalog:{agent_hasn_id}',
            default_mode=cfg.get('default_mode', 'allow'),
            capability_modes=cfg.get('capability_modes', {}),
        )
        # 确保 App 工具已投影进注册表（builtin + 已发布 manifest），catalog 才能含 app 分组。
        await mcp_server._load_app_tools(ctx)
        catalog = mcp_server.tool_directory.build_scope_catalog(ctx)
        return ScopeCatalogResponse.model_validate(catalog)

    async def list_ask_requests(self, db: AsyncSession, agent_hasn_id: str, owner_hasn_id: str) -> list[dict]:
        """列出该 Agent 当前挂起的 ask 批准请求（P6，主人 UI 用）。"""
        await self._assert_owns(db, agent_hasn_id, owner_hasn_id, write=False)
        from backend.app.mcp.ask_gate import ask_approval_gate

        return await ask_approval_gate.list_pending(agent_hasn_id)

    async def decide_ask_request(
        self, db: AsyncSession, agent_hasn_id: str, owner_hasn_id: str, request_id: str, decision: str
    ) -> None:
        """主人对某挂起 ask 请求做批准/拒绝决定（P6）。

        授权按**审批行的 owner_hasn_id**（创建时记录、也即审批卡片送达对象）校验，而非
        agent.owner_id——分身归属可能因身份迁移与当前不一致，审批行的 owner 才是“该谁批”的权威。
        """
        await self._assert_owns_approval(db, agent_hasn_id, owner_hasn_id, request_id)
        from backend.app.mcp.ask_gate import ask_approval_gate

        await ask_approval_gate.submit_decision(request_id, decision)

    async def grant_approval(
        self, db: AsyncSession, *, agent_hasn_id: str, owner_hasn_id: str, request_id: str, scope: str
    ) -> dict:
        """主人批准一条 ask 审批请求 → 签一次性能力票据（令牌重试 doc15 §3.3 / A-P2）。

        scope=always 额外把**实际触发 ask** 的 capability_keys 写回 capability_modes=allow（以后不再 ask）。
        返回 {request_id, grant_scope, tool_name, capability_ticket}；ticket 仅回 daemon owner 通道，
        绝不进卡片/日志。仅对 pending 且未超时的请求有效。
        """
        row = await self._assert_owns_approval(db, agent_hasn_id, owner_hasn_id, request_id)
        from backend.app.hasn.crud.crud_hasn_agent_approval_requests import hasn_agent_approval_requests_dao
        from backend.common.security.agent_jwt import get_agent_scopes_from_db, update_agent_modes
        from backend.common.security.capability_ticket import issue_capability_ticket
        from backend.utils.timezone import timezone

        if row.status != 'pending':
            raise errors.ForbiddenError(msg=_friendly_stale_message(row.status))
        if row.expires_time and row.expires_time < timezone.now():
            await hasn_agent_approval_requests_dao.update_model(
                db, row.id, {'status': 'timeout', 'decided_time': timezone.now()}
            )
            await db.commit()
            raise errors.ForbiddenError(msg=_friendly_stale_message('timeout'))

        grant_scope = 'always' if scope == 'always' else 'once'
        ticket, jti = issue_capability_ticket(
            request_id=row.request_id,
            agent_hasn_id=row.agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            tool_name=row.tool_name,
            args_hash=row.args_hash,
        )

        # always：把实际触发 ask 的能力 key 写回 allow（update_agent_modes 自带 commit + 失效缓存）。
        if grant_scope == 'always' and row.capability_keys:
            policy = await get_agent_scopes_from_db(db, agent_hasn_id)
            caps = dict(policy.get('capability_modes') or {})
            for key in row.capability_keys:
                caps[key] = 'allow'
            await update_agent_modes(
                db, agent_hasn_id, default_mode=policy.get('default_mode', 'allow'), capability_modes=caps
            )

        await hasn_agent_approval_requests_dao.update_model(
            db,
            row.id,
            {'status': 'approved', 'grant_scope': grant_scope, 'ticket_jti': jti, 'decided_time': timezone.now()},
        )
        await db.commit()
        # always 写穿了 capability_modes → push WSPUSH kind=scopes（S4·U-L4），
        # 让主人各在线设备的 CapabilityModeMirror 秒级对齐「以后不再 ask」。
        if grant_scope == 'always' and row.capability_keys:
            await self._bump_scopes(db, owner_hasn_id)
        return {
            'request_id': row.request_id,
            'grant_scope': grant_scope,
            'tool_name': row.tool_name,
            'capability_ticket': ticket,
        }


agent_scopes_service = AgentScopesService()
