"""统一视频引擎用户端（owner）业务 API（设计 doc22 §3 owner read-API + 工作台）。

认证：Owner JWT。主人在 WebUI `/apps/studio` 操作全链路——建项目 / 存分镜 / 挑流水线 / 派分身出片 /
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
from pydantic import BaseModel, Field

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
from backend.app.hasn_studio.service import media_credentials
from backend.app.hasn_studio.service.studio_service import Subject, studio_service
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


async def _subject(db: CurrentSession | CurrentSessionTransaction, request: Request) -> Subject:
    """owner JWT → 操作主体（human）。分享/可访问列表/发布按 Subject 走 resource_share。"""
    return Subject.human(await _owner(db, request))


# ---------------- 分享 / 发布 请求体 ----------------


class AddShareRequest(BaseModel):
    grantee_type: str = Field(description='human/agent/enterprise')
    grantee_id: str = Field(min_length=1, description='被授权对象 ID（人/分身 hasn_id 或企业 id）')
    permission: str = Field(description='viewer/editor/manager')


class PublishArtifactRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200, description='对外标题（缺省用成品标题）')
    visibility: str = Field(default='unlisted', description='private/password/unlisted/public')
    password: str | None = Field(default=None, description='visibility=password 时口令明文')
    allow_download: bool = Field(default=False, description='是否允许下载成片')


class SetMediaCredentialRequest(BaseModel):
    provider: str = Field(min_length=1, description='媒体 provider 标识（fal/suno/heygen …）')
    value: str = Field(min_length=1, description='凭据明文（加密落库，绝不回参/记录）')


# ============================ 引擎健康 + 流水线目录 ============================


@router.get('/healthz', summary='[Owner] 视频引擎探活', dependencies=[DependsJwtAuth])
async def studio_healthz() -> ResponseModel:
    """探活 montage-engine-service（看板诊断；未配置/不可达回诚实 ok:false）。"""
    return response_base.success(data=await montage_engine_provider.healthz())


@router.get('/pipelines', summary='[Owner] 可用视频流水线', dependencies=[DependsJwtAuth])
async def list_pipelines() -> ResponseModel:
    """经 broker 取引擎流水线目录（只 production）。引擎不可达 → 透传真实错误（零 fake）。"""
    return response_base.success(data=await studio_service.list_pipelines())


# ============================ 项目 ============================


@router.get('/projects', summary='[Owner] 项目列表（我的 ∪ 共享给我的）', dependencies=[DependsJwtAuth])
async def list_projects(request: Request, db: CurrentSession) -> ResponseModel:
    subject = await _subject(db, request)
    items = await studio_service.list_accessible_projects(db, subject=subject)
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


@router.get('/artifacts', summary='[Owner] 成品列表（我的 ∪ 共享给我的，换签名 URL）', dependencies=[DependsJwtAuth])
async def list_artifacts(request: Request, db: CurrentSession, project_id: int | None = None) -> ResponseModel:
    subject = await _subject(db, request)
    items = await studio_service.list_accessible_artifacts(db, subject=subject, project_id=project_id)
    return response_base.success(data={'items': items})


@router.post('/artifacts/export', summary='[Owner] 导出成片（签名下载 URL）', dependencies=[DependsJwtAuth])
async def export_artifact(request: Request, db: CurrentSession, obj: ExportParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await studio_service.export(db, owner_hasn_id=owner_hasn_id, artifact_id=obj.artifact_id, fmt=obj.format)
    return response_base.success(data=data)


# ============================ 渲染（job 式） ============================


@router.post('/render/pipeline', summary='[Owner] 跑流水线出片（花算力，job 式）', dependencies=[DependsJwtAuth])
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


# ============================ 分享协作（项目；§3.6 全复用 resource_share） ============================


@router.get('/projects/{project_id}/shares', summary='[Owner] 查看项目协作名单', dependencies=[DependsJwtAuth])
async def list_project_shares(request: Request, db: CurrentSession, project_id: int) -> ResponseModel:
    subject = await _subject(db, request)
    data = await studio_service.list_project_shares(db, subject=subject, project_id=project_id)
    return response_base.success(data=data)


@router.post('/projects/{project_id}/shares', summary='[Owner] 添加/更新项目协作者', dependencies=[DependsJwtAuth])
async def add_project_share(
    request: Request, db: CurrentSessionTransaction, project_id: int, body: AddShareRequest
) -> ResponseModel:
    subject = await _subject(db, request)
    data = await studio_service.add_project_share(
        db,
        subject=subject,
        project_id=project_id,
        grantee_type=body.grantee_type,
        grantee_id=body.grantee_id,
        permission=body.permission,
    )
    return response_base.success(data=data)


@router.delete('/projects/{project_id}/shares', summary='[Owner] 撤销项目协作者', dependencies=[DependsJwtAuth])
async def revoke_project_share(
    request: Request, db: CurrentSessionTransaction, project_id: int, grantee_type: str, grantee_id: str
) -> ResponseModel:
    subject = await _subject(db, request)
    ok = await studio_service.revoke_project_share(
        db, subject=subject, project_id=project_id, grantee_type=grantee_type, grantee_id=grantee_id
    )
    return response_base.success(data={'revoked': ok})


# ============================ 分享协作（成品；§3.6 全复用 resource_share） ============================


@router.get('/artifacts/{artifact_id}/shares', summary='[Owner] 查看成品协作名单', dependencies=[DependsJwtAuth])
async def list_artifact_shares(request: Request, db: CurrentSession, artifact_id: int) -> ResponseModel:
    subject = await _subject(db, request)
    data = await studio_service.list_artifact_shares(db, subject=subject, artifact_id=artifact_id)
    return response_base.success(data=data)


@router.post('/artifacts/{artifact_id}/shares', summary='[Owner] 添加/更新成品协作者', dependencies=[DependsJwtAuth])
async def add_artifact_share(
    request: Request, db: CurrentSessionTransaction, artifact_id: int, body: AddShareRequest
) -> ResponseModel:
    subject = await _subject(db, request)
    data = await studio_service.add_artifact_share(
        db,
        subject=subject,
        artifact_id=artifact_id,
        grantee_type=body.grantee_type,
        grantee_id=body.grantee_id,
        permission=body.permission,
    )
    return response_base.success(data=data)


@router.delete('/artifacts/{artifact_id}/shares', summary='[Owner] 撤销成品协作者', dependencies=[DependsJwtAuth])
async def revoke_artifact_share(
    request: Request, db: CurrentSessionTransaction, artifact_id: int, grantee_type: str, grantee_id: str
) -> ResponseModel:
    subject = await _subject(db, request)
    ok = await studio_service.revoke_artifact_share(
        db, subject=subject, artifact_id=artifact_id, grantee_type=grantee_type, grantee_id=grantee_id
    )
    return response_base.success(data={'revoked': ok})


# ============================ 对外公开发布（成品；M18 web 发布全复用） ============================


@router.get('/artifacts/{artifact_id}/publish', summary='[Owner] 读取成片对外发布态', dependencies=[DependsJwtAuth])
async def get_artifact_publication(request: Request, db: CurrentSession, artifact_id: int) -> ResponseModel:
    subject = await _subject(db, request)
    site = await studio_service.get_artifact_publication(db, subject=subject, artifact_id=artifact_id)
    return response_base.success(data={'site': site})


@router.post(
    '/artifacts/{artifact_id}/publish',
    summary='[Owner] 发布成片为可分享网页（/s/{slug}）',
    dependencies=[DependsJwtAuth],
)
async def publish_artifact(
    request: Request, db: CurrentSessionTransaction, artifact_id: int, body: PublishArtifactRequest
) -> ResponseModel:
    subject = await _subject(db, request)
    data = await studio_service.publish_artifact(
        db,
        subject=subject,
        artifact_id=artifact_id,
        title=body.title,
        visibility=body.visibility,
        password=body.password,
        allow_download=body.allow_download,
    )
    return response_base.success(data=data)


@router.delete(
    '/artifacts/{artifact_id}/publish',
    summary='[Owner] 撤销成片对外发布（URL 返回 410）',
    dependencies=[DependsJwtAuth],
)
async def unpublish_artifact(request: Request, db: CurrentSessionTransaction, artifact_id: int) -> ResponseModel:
    subject = await _subject(db, request)
    ok = await studio_service.unpublish_artifact(db, subject=subject, artifact_id=artifact_id)
    return response_base.success(data={'revoked': ok})


# ==================== 媒体凭据 BYO 管理（owner-via-webui，非 agent 工具；doc22 §5 P7） ====================
#
# 长尾媒体 provider（fal/Suno/HeyGen …）的自带 key。**owner 专属**——刻意不开 MCP 工具 / agent scope：
# 媒体凭据管理是主人经 WebUI 的能力，分身不该读写主人凭据。加密落 hasn_app_credential（app_id='studio'），
# **绝不**回明文/密文，只回脱敏状态（has_key + status）。网关族（image/tts/stt/video）经 new-api 用主人自己
# 的 token，不在此配。


@router.get('/credentials', summary='[Owner] 媒体凭据状态（脱敏，含族路由）', dependencies=[DependsJwtAuth])
async def list_media_credentials(request: Request, db: CurrentSession) -> ResponseModel:
    """列长尾媒体 provider 的凭据状态（只 has_key/status，绝不回值）+ 族路由表（哪些族走网关/BYO）。"""
    owner_hasn_id = await _owner(db, request)
    items = await media_credentials.list_byo_credentials(db, owner_hasn_id=owner_hasn_id)
    return response_base.success(data={'items': items, 'family_routing': media_credentials.family_routing()})


@router.post('/credentials', summary='[Owner] 配置/更换媒体 provider 自带 key', dependencies=[DependsJwtAuth])
async def set_media_credential(
    request: Request, db: CurrentSessionTransaction, body: SetMediaCredentialRequest
) -> ResponseModel:
    """主人配/换某 provider 的 BYO key：加密 upsert（绝不回明文/密文，只回脱敏结果）。"""
    owner_hasn_id = await _owner(db, request)
    data = await media_credentials.upsert_byo_credential(
        db, owner_hasn_id=owner_hasn_id, provider=body.provider, value=body.value
    )
    return response_base.success(data=data)


@router.delete('/credentials', summary='[Owner] 吊销媒体 provider 自带 key', dependencies=[DependsJwtAuth])
async def revoke_media_credential(request: Request, db: CurrentSessionTransaction, provider: str) -> ResponseModel:
    """吊销主人某 provider 的 BYO key（清密文 + status=revoked）。"""
    owner_hasn_id = await _owner(db, request)
    ok = await media_credentials.revoke_byo_credential(db, owner_hasn_id=owner_hasn_id, provider=provider)
    return response_base.success(data={'revoked': ok})
