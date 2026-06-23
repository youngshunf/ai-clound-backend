"""统一视频引擎用户端（owner）业务 API（设计 doc22 §3 owner read-API + 工作台）。

认证：Owner JWT。主人在 WebUI `/apps/studio` 操作全链路——建项目 / 存分镜 / 挑管线 / 派分身出片 /
轮询渲染 / 管成品 / 导出。**WebUI 经 daemon `/api/v1/studio/*` 薄代理调用本面**（铁律：WebUI 不直连
云端、不直连引擎）。

定位：owner 面 = 业务操作（包裹 studio_service），不是 codegen 裸 CRUD；Agent 工具面（`hasn.studio.*`）
走云端 MCP（gateway_internal handler），不经本面。**单一 Broker**：owner 面与 Agent 面共用同一
`studio_service` + `montage_engine_provider`，引擎是唯一耦合点。

身份恒取自 Owner JWT（request.user.id → owner_hasn_id，行级隔离）；owner 直接操作时 agent_hasn_id 留空。
一律返回统一信封（ResponseModel + response_base.success）。渲染失败时引擎真实错误落在 job.error，
HTTP 仍 200（传输成功、业务态在 data.status/data.error 里），零 fake。
"""

from fastapi import APIRouter, Request

from backend.app.hasn.service.app_catalog_service import resolve_owner_hasn_id
from backend.app.hasn_studio.provider import montage_engine_provider
from backend.app.hasn_studio.schema.owner import (
    ExportParam,
    RenderParam,
    RunPipelineParam,
    RunToolParam,
    SaveProjectParam,
    SaveStoryboardParam,
)
from backend.app.hasn_studio.service.studio_service import studio_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _owner(db: CurrentSession | CurrentSessionTransaction, request: Request) -> str:
    """从 Owner JWT 解析 owner_hasn_id；无平台身份映射则拒（行级隔离前提）。"""
    owner_hasn_id = await resolve_owner_hasn_id(db, user_id=request.user.id)
    if not owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号未关联唤星身份，无法访问视频引擎')
    return owner_hasn_id


# ============================ 引擎健康 + 管线目录 ============================


@router.get('/healthz', summary='[Owner] 视频引擎探活', dependencies=[DependsJwtAuth])
async def studio_healthz() -> ResponseModel:
    """探活 montage-engine-service（看板诊断；未配置/不可达回诚实 ok:false）。"""
    return response_base.success(data=await montage_engine_provider.healthz())


@router.get('/pipelines', summary='[Owner] 可用视频管线', dependencies=[DependsJwtAuth])
async def list_pipelines() -> ResponseModel:
    """经 broker 取引擎管线目录（只 production）。引擎不可达 → 透传真实错误（零 fake）。"""
    return response_base.success(data=await studio_service.list_pipelines())


# ============================ 项目 ============================


@router.get('/projects', summary='[Owner] 项目列表', dependencies=[DependsJwtAuth])
async def list_projects(request: Request, db: CurrentSession) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    items = await studio_service.list_projects(db, owner_hasn_id=owner_hasn_id)
    return response_base.success(data={'items': items})


@router.post('/projects', summary='[Owner] 新建/更新项目', dependencies=[DependsJwtAuth])
async def save_project(request: Request, db: CurrentSessionTransaction, obj: SaveProjectParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.save_project(
        db,
        owner_hasn_id=owner_hasn_id,
        agent_hasn_id=None,  # owner 直接操作，非分身代理
        project_id=obj.project_id,
        title=obj.title,
        description=obj.description,
        default_pipeline_key=obj.default_pipeline_key,
        settings=obj.settings,
        cover_asset_uri=obj.cover_asset_uri,
        bound_agent_id=obj.bound_agent_id,
        status=obj.status,
    )
    return response_base.success(data=data)


@router.get('/projects/{project_id}', summary='[Owner] 项目详情（含素材）', dependencies=[DependsJwtAuth])
async def get_project(request: Request, db: CurrentSession, project_id: int) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.get_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
    return response_base.success(data=data)


@router.post('/storyboard', summary='[Owner] 保存分镜脚本', dependencies=[DependsJwtAuth])
async def save_storyboard(request: Request, db: CurrentSessionTransaction, obj: SaveStoryboardParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.save_storyboard(
        db, owner_hasn_id=owner_hasn_id, project_id=obj.project_id, storyboard=obj.storyboard
    )
    return response_base.success(data=data)


# ============================ 素材 / 成品 ============================


@router.get('/projects/{project_id}/assets', summary='[Owner] 项目素材', dependencies=[DependsJwtAuth])
async def list_assets(request: Request, db: CurrentSession, project_id: int) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    items = await studio_service.list_assets(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
    return response_base.success(data={'items': items})


@router.get('/artifacts', summary='[Owner] 成品列表（换签名 URL）', dependencies=[DependsJwtAuth])
async def list_artifacts(request: Request, db: CurrentSession, project_id: int | None = None) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    items = await studio_service.list_artifacts(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
    return response_base.success(data={'items': items})


@router.post('/artifacts/export', summary='[Owner] 导出成片（签名下载 URL）', dependencies=[DependsJwtAuth])
async def export_artifact(request: Request, db: CurrentSession, obj: ExportParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.export(
        db, owner_hasn_id=owner_hasn_id, artifact_id=obj.artifact_id, fmt=obj.format
    )
    return response_base.success(data=data)


# ============================ 渲染（job 式） ============================


@router.post('/render/pipeline', summary='[Owner] 跑管线出片（花算力，job 式）', dependencies=[DependsJwtAuth])
async def run_pipeline(request: Request, db: CurrentSessionTransaction, obj: RunPipelineParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.run_pipeline(
        db,
        owner_hasn_id=owner_hasn_id,
        agent_hasn_id=None,
        project_id=obj.project_id,
        pipeline_key=obj.pipeline_key,
        input_payload=obj.input,
        work_session_id=obj.work_session_id,
    )
    return response_base.success(data=data)


@router.post('/render', summary='[Owner] 直接渲染（花算力，job 式）', dependencies=[DependsJwtAuth])
async def render(request: Request, db: CurrentSessionTransaction, obj: RenderParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.render(
        db,
        owner_hasn_id=owner_hasn_id,
        agent_hasn_id=None,
        project_id=obj.project_id,
        props=obj.props,
        demo=obj.demo,
        composition_id=obj.composition_id,
        pipeline_key=obj.pipeline_key,
        work_session_id=obj.work_session_id,
    )
    return response_base.success(data=data)


@router.post('/tools/run', summary='[Owner] 调用引擎原子工具（创作段）', dependencies=[DependsJwtAuth])
async def run_tool(request: Request, db: CurrentSession, obj: RunToolParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.run_tool(
        db, owner_hasn_id=owner_hasn_id, agent_hasn_id=None, tool_name=obj.tool_name, inputs=obj.inputs
    )
    return response_base.success(data=data)


@router.get('/render/jobs/{render_job_id}', summary='[Owner] 读渲染任务', dependencies=[DependsJwtAuth])
async def get_render_job(request: Request, db: CurrentSessionTransaction, render_job_id: int) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.get_render_job(db, owner_hasn_id=owner_hasn_id, render_job_id=render_job_id)
    return response_base.success(data=data)
