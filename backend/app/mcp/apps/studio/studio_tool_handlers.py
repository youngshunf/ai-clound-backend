"""统一视频引擎 AI-Native 工具 handler（云端 gateway_internal，设计 doc22 §3/§3.5）。

形态（与 creator/finance/quant 一致）：studio 是 **cloud-brokered** 应用——`hasn.studio.*` 工具一律走
云端 MCP：`ai_native_runtime_gateway` 在 `transport=gateway_internal` 时进程内直调本文件 handler，
handler 再调 `studio_service`（落 hasn_studio PG）+ 经 `montage_engine_provider` 调引擎服务跑真渲染/出片。

每个 handler 签名 `(db, agent: AgentTokenPayload, input_payload: dict) -> dict`：
- 身份恒取自 Agent JWT claims（`owner_hasn_id`/`agent_hasn_id`），绝不从入参读身份（PLANFIX-6）；
- owner 行级隔离由 service 强制；返回**裸 data**（gateway 负责信封/审计）。

⚠️ 双重身份（§3.5）：工具注册在云端 MCP gateway_internal，**任意应用的分身**（creator/task/workflow…）
都能编排调 `hasn.studio.run_pipeline`，产物经 `hasn://asset/` 回填组合（零数据合并）。这天然成立
（handler 只认 agent JWT 身份 + owner 隔离），无需特殊代码。

引擎传输/业务失败：broker 抛 `StudioEngineError`。读类工具（list_pipelines/run_tool）让其冒泡（gateway
归一）；渲染类（run_pipeline/render）service 内已把失败落 job.status=failed + 透传 error（零 fake），
handler 不再额外处理。

注册：`ai_native_runtime_gateway._internal_handlers()` 按 handler 键 `studio.<name>` 注册本 10 handler；
`hasn_studio.manifest.STUDIO_AI_NATIVE_MANIFEST` 声明能力/工具面；`app/mcp/scopes.py` 聚合
`STUDIO_SCOPE_CATALOG`；`app_catalog_registry` 注册 `build_studio_app()`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_studio.service.studio_service import studio_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload


def _int(payload: dict[str, Any], key: str) -> int:
    return int(payload[key])


def _opt_int(payload: dict[str, Any], key: str) -> int | None:
    val = payload.get(key)
    return int(val) if val is not None else None


# ---------------- 读（studio:read，出厂 allow） ----------------


async def handle_list_pipelines(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """经 broker 取引擎管线目录（只 production）。"""
    return await studio_service.list_pipelines()


async def handle_list_projects(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    items = await studio_service.list_projects(db, owner_hasn_id=agent.owner_hasn_id)
    return {'items': items}


async def handle_get_project(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await studio_service.get_project(
        db, owner_hasn_id=agent.owner_hasn_id, project_id=_int(input_payload, 'project_id')
    )


async def handle_list_assets(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    items = await studio_service.list_assets(
        db, owner_hasn_id=agent.owner_hasn_id, project_id=_int(input_payload, 'project_id')
    )
    return {'items': items}


async def handle_list_artifacts(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    items = await studio_service.list_artifacts(
        db, owner_hasn_id=agent.owner_hasn_id, project_id=_opt_int(input_payload, 'project_id')
    )
    return {'items': items}


async def handle_get_render_job(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """读渲染 job（含惰性轮询引擎落状态 + 成功首次物化成品）。owner 隔离。"""
    return await studio_service.get_render_job(
        db, owner_hasn_id=agent.owner_hasn_id, render_job_id=_int(input_payload, 'render_job_id')
    )


# ---------------- 写（studio:write，出厂 allow） ----------------


async def handle_save_project(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """新建/更新视频项目（不出片、不花算力）。新建必带 title；传 project_id 则更新。"""
    return await studio_service.save_project(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        project_id=_opt_int(input_payload, 'project_id'),
        title=input_payload.get('title'),
        description=input_payload.get('description'),
        default_pipeline_key=input_payload.get('default_pipeline_key'),
        settings=input_payload.get('settings'),
        cover_asset_uri=input_payload.get('cover_asset_uri'),
        bound_agent_id=input_payload.get('bound_agent_id'),
        status=input_payload.get('status'),
    )


async def handle_save_storyboard(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """承载分镜脚本（更新 project.settings['storyboard']，避免假 asset_uri）。"""
    return await studio_service.save_storyboard(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        project_id=_int(input_payload, 'project_id'),
        storyboard=input_payload.get('storyboard'),
    )


# ---------------- 渲染（studio:render，出厂 ask；run_tool 同 ask=可能花钱） ----------------


async def handle_run_pipeline(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """复合主入口：落 render_job + broker /v1/render，立即返回 job ref 供 get_render_job 轮询。

    可被**任意应用的分身**跨应用编排调用（§3.5）；产物经 hasn://asset/ 回填组合。
    引擎失败 → job.status=failed + 透传真实 error（零 fake）。
    """
    return await studio_service.run_pipeline(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        project_id=_int(input_payload, 'project_id'),
        pipeline_key=input_payload.get('pipeline_key'),
        input_payload=input_payload.get('input'),
        work_session_id=input_payload.get('work_session_id'),
    )


async def handle_render(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """较低层直渲染：(project_id, props?|demo?, composition_id?, pipeline_key?) → 落 render_job + broker。"""
    return await studio_service.render(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        project_id=_int(input_payload, 'project_id'),
        props=input_payload.get('props'),
        demo=input_payload.get('demo'),
        pipeline_key=input_payload.get('pipeline_key'),
        composition_id=input_payload.get('composition_id'),
        work_session_id=input_payload.get('work_session_id'),
    )


async def handle_run_tool(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """原子工具透传（创作段，provider 可能花钱 → studio:render ask）。broker POST /v1/tools/{tool_name}。"""
    return await studio_service.run_tool(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        tool_name=str(input_payload.get('tool_name') or ''),
        inputs=input_payload.get('inputs'),
    )


# ---------------- 导出（studio:export，出厂 ask） ----------------


async def handle_export(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """导出成片（owner 隔离 + 序列化边界换 CDN 签名 URL）。"""
    return await studio_service.export(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        artifact_id=_int(input_payload, 'artifact_id'),
        fmt=input_payload.get('format'),
    )


# mcp_name → handler 便捷映射（测试/自省用）。gateway 实际注册按 manifest 的 handler 键 `studio.<name>`，
# 直接引用本模块 handle_*（见 `ai_native_runtime_gateway._internal_handlers()`），不消费本 dict。
STUDIO_TOOL_HANDLERS = {
    'hasn.studio.list_pipelines': handle_list_pipelines,
    'hasn.studio.list_projects': handle_list_projects,
    'hasn.studio.get_project': handle_get_project,
    'hasn.studio.list_assets': handle_list_assets,
    'hasn.studio.list_artifacts': handle_list_artifacts,
    'hasn.studio.get_render_job': handle_get_render_job,
    'hasn.studio.save_project': handle_save_project,
    'hasn.studio.save_storyboard': handle_save_storyboard,
    'hasn.studio.run_pipeline': handle_run_pipeline,
    'hasn.studio.render': handle_render,
    'hasn.studio.run_tool': handle_run_tool,
    'hasn.studio.export': handle_export,
}
