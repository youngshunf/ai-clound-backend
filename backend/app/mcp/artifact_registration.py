"""产物自动登记（register-on-write）公共接缝 —— AI-Native 应用设计的基本准则（doc31）。

**只要分身参与产出/修改了应用资产，该资产必须自动登记进 `hasn_artifacts` 并绑定当次工作会话。**
不登记 = 主人在工作会话资源栏 / 分身产物 tab 什么都看不到，产出成黑洞。

本模块把「取 descriptor → 调 record_app_resource_artifact → best-effort 吞错」这套样板收成一个
函数，供**两条分身工具面**共用（此前每个应用各抄一份 try/import/warn，铺开即 N 份漂移）：

- ① 平台工具面 `app/mcp/tools/*.py`（经 `mcp/server.py::call_tool`）；
- ② AI-Native 应用工具面 `app/<app>/service/tool_handlers.py`（经 `ai_native_runtime_gateway`）。

两面的会话 id 都由分发入口剥离系统注入的 `_hasn_session_id` 后落进 `mcp/context.py` 的 ContextVar，
故此处默认自动取用，调用方无需关心自己在哪条面上——这正是收敛的意义。
"""

from __future__ import annotations

import logging

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.mcp.context import get_current_work_session_id

logger = logging.getLogger(__name__)

_UNSET = object()


async def register_app_resource_artifact(
    db: AsyncSession,
    *,
    app_id: str,
    resource_kind: str,
    server_id: str | int,
    agent_hasn_id: str,
    owner_hasn_id: str,
    title: str,
    source_tool: str,
    summary: str | None = None,
    session_id: Any = _UNSET,
) -> None:
    """把分身刚写过的应用资源登记为产物（每个写点都调，不要只在 finalize 调）。

    - `resource_kind`：应用 manifest `resources[]` 里声明的 kind（如 `knowledge.base`）。**新应用先
      声明 `resources[]` 再谈登记**——完成卡 / 会话资源栏 / URI 解析 / 详情跳转全从这份声明派生。
    - `server_id`：**必须云端权威 id**（`hasn://{uri_domain}/{server_id}`）。本地 id 永不上 URI，
      否则换设备 / 分享给别人就解析不开（Core-08 铁律）。
    - `session_id`：缺省自动取 ContextVar（主会话直调则为 None，产物仍进分身产物 tab）。仅在调用方
      已持有权威会话 id（如平台工具面的 `ctx.work_session_id`）时才显式传。
    - 幂等：底层按 `(agent, dispatch_id, resource_uri)` UPSERT，反复写只一条 active 行；会话归属
      只进不退（None 不会把已有归属抹掉）。

    **best-effort**：登记失败只 warn，绝不抛。业务写常与登记同事务，抛出会连累正事落库——
    「产物没登记上」是可修的账，「知识库没建成」不是。
    """
    try:
        from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
        from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService

        descriptor = ai_native_app_registry.resource_descriptor(app_id, resource_kind)
        if descriptor is None:
            logger.warning('[%s] 缺 %s 资源描述符，跳过产物登记', app_id, resource_kind)
            return
        # registry 对认不出的 kind 会回落 `resources[0]`（那是给 kind=None 缺省用的语义）。
        # 登记这里必须严格：回落会把产物登记成**另一类资源**、URI 也跟着错，比不登记更难查。
        if descriptor.resource_kind != resource_kind:
            logger.warning(
                '[%s] 未声明资源类型 %s（manifest resources[] 里只有 %s），跳过产物登记',
                app_id,
                resource_kind,
                descriptor.resource_kind,
            )
            return
        await HasnArtifactsService.record_app_resource_artifact(
            db,
            descriptor=descriptor,
            server_id=str(server_id),
            session_id=get_current_work_session_id() if session_id is _UNSET else session_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            title=title,
            summary=summary,
            source_tool=source_tool,
        )
    except Exception as e:
        logger.warning('[%s] register-on-write 登记 hasn_artifacts 失败（非致命）: %s', app_id, e)
