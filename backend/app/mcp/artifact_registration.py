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

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
from backend.app.mcp.context import get_current_project_id, get_current_work_session_id

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
    project_id: Any = _UNSET,
    action: Literal['create', 'update'] = 'create',
    dispatch_id: str | None = None,
) -> ArtifactRegistration | None:
    """把分身刚写过的应用资源登记为产物（每个写点都调，不要只在 finalize 调）。

    - `resource_kind`：应用 manifest `resources[]` 里声明的 kind（如 `knowledge.base`）。**新应用先
      声明 `resources[]` 再谈登记**——完成卡 / 会话资源栏 / URI 解析 / 详情跳转全从这份声明派生。
    - `server_id`：**必须云端权威 id**（`hasn://{uri_domain}/{server_id}`）。本地 id 永不上 URI，
      否则换设备 / 分享给别人就解析不开（Core-08 铁律）。
    - `session_id`：缺省自动取 ContextVar（主会话直调则为 None，产物仍进分身产物 tab）。仅在调用方
      已持有权威会话 id（如平台工具面的 `ctx.session_id`）时才显式传。
    - `project_id`（doc38 §3.4）：缺省自动取 ContextVar（不在项目中工作则为 None，产物不挂项目）。
      分发入口剥离系统注入的 `_hasn_project_id` 后落进 ContextVar，故与 `session_id` 同管道——已接
      应用**零改造**即自动打标；落底层「只进不退」（None 不会把已锁定的项目归属抹掉）。
    - 幂等：底层按 `(agent, dispatch_id, resource_uri)` UPSERT，反复写只一条 active 行；会话归属
      只进不退（None 不会把已有归属抹掉）。

    **best-effort**：登记失败只 warn，绝不抛。业务写常与登记同事务，抛出会连累正事落库——
    「产物没登记上」是可修的账，「知识库没建成」不是。

    **返回 `ArtifactRegistration(artifact_id, resource_uri)`**（doc36 §3.1）——写工具把 `resource_uri`
    放进返回体，分身写完就知道怎么打开，不必二次查询。两条失败路径**语义不同，别搞混**：

    - **descriptor 解析不出**（kind 不认识 / manifest 没声明）→ 返 `None`。URI 算不出来（不知道
      uri_domain），写工具省略 `uri` 字段——**不许**返回空串或假 URI。
    - **descriptor 已解析、只是落库失败** → 仍返回带 `resource_uri` 的结果（`artifact_id=None`）。
      URI 是 `(uri_domain, server_id)` 的纯函数，业务写已成功、资源真实存在且能打开；此时不返 URI
      会让分身既拿不到地址、又查不到产物（没登记），凭空双输。
    """
    try:
        from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

        descriptor = ai_native_app_registry.resource_descriptor(app_id, resource_kind)
        if descriptor is None:
            logger.warning('[%s] 缺 %s 资源描述符，跳过产物登记', app_id, resource_kind)
            return None
        # registry 对认不出的 kind 会回落 `resources[0]`（那是给 kind=None 缺省用的语义）。
        # 登记这里必须严格：回落会把产物登记成**另一类资源**、URI 也跟着错，比不登记更难查。
        if descriptor.resource_kind != resource_kind:
            logger.warning(
                '[%s] 未声明资源类型 %s（manifest resources[] 里只有 %s），跳过产物登记',
                app_id,
                resource_kind,
                descriptor.resource_kind,
            )
            return None
    except Exception as e:
        logger.warning('[%s] 解析 %s 资源描述符失败，跳过产物登记（非致命）: %s', app_id, resource_kind, e)
        return None

    resolved_session_id = get_current_work_session_id() if session_id is _UNSET else session_id
    resolved_project_id = get_current_project_id() if project_id is _UNSET else project_id

    try:
        from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService

        # 登记仅是业务写后的 best-effort 投影。以 SAVEPOINT 隔离其失败，不能把调用方已开始的
        # 真实业务事务标记为回滚态。
        async with db.begin_nested():
            return await HasnArtifactsService.record_app_resource_artifact(
                db,
                descriptor=descriptor,
                server_id=str(server_id),
                session_id=resolved_session_id,
                agent_hasn_id=agent_hasn_id,
                owner_hasn_id=owner_hasn_id,
                title=title,
                summary=summary,
                source_tool=source_tool,
                # doc38 §3.4：缺省自动取 ContextVar project_id（不在项目中则 None），已接应用零改造。
                project_id=resolved_project_id,
                action=action,
                dispatch_id=dispatch_id,
            )
    except Exception as e:
        logger.warning('[%s] register-on-write 登记 hasn_artifacts 失败（非致命）: %s', app_id, e)
        try:
            from backend.app.hasn.service.artifact_registration_outbox_service import (
                artifact_registration_outbox_service,
            )

            await artifact_registration_outbox_service.enqueue_app_resource_repair_intent(
                db,
                descriptor=descriptor,
                server_id=str(server_id),
                agent_hasn_id=agent_hasn_id,
                owner_hasn_id=owner_hasn_id,
                title=title,
                summary=summary,
                source_tool=source_tool,
                work_session_id=resolved_session_id,
                project_id=resolved_project_id,
                action=action,
                dispatch_id=dispatch_id,
            )
        except Exception as enqueue_error:
            logger.warning('[%s] register-on-write 修复意图入队失败（非致命）: %s', app_id, enqueue_error)
        # 登记没成，但资源建成了、地址算得出来——照常把 URI 交给分身（见 docstring）。
        return ArtifactRegistration(artifact_id=None, resource_uri=descriptor.build_uri(server_id))


def merge_resource_uri(payload: dict[str, Any], registration: ArtifactRegistration | None) -> dict[str, Any]:
    """把登记算出的 `hasn://` 地址并进写工具返回体（doc36 §3.2 契约，全仓统一走这里）。

    契约：**凡是 register-on-write 的写工具，返回体必须含 `uri` 字段**，值 = 登记返回的
    `resource_uri`。以前 URI 在登记那一刻算出来就地扔掉，分身写完只拿到一个裸 id、不知道
    怎么打开自己刚做的东西——这正是 doc36 要修的根因。

    登记返 `None`（descriptor 解析不出、URI 无从算起）→ 原样返回、**省略** `uri` 字段；不许
    返空串或假 URI——分身拿到打不开的地址，只会以为是自己用错了。

    字段名固定 `uri`（写工具面统一名，与 knowledge `_document_dict` 既有形状一致）；
    `artifact.list/get` 查询面的同一个东西叫 `resource_uri`（历史形状保留），两边工具描述里
    互相指认，不许出现第三个名字。
    """
    if registration is None:
        return payload
    return {**payload, 'uri': registration.resource_uri}
