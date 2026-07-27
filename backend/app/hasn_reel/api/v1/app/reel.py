"""短视频（reel）用户端（owner）业务 API（设计 doc29 §5 owner 面）。

认证：Owner JWT。主人在 webui `/apps/reel` 操作项目化创作全链路——建项目 / 看创作历史 / 看一次创作的
进度与产物。**webui 经 daemon `/api/v1/reel/*` 薄代理调用本面**（铁律：webui 不直连云端、不直连引擎）。

定位与 studio 的关键区别——**reel 引擎是本地 sidecar（downloadable_local），不经云端**：
- 渲染/出片跑在本地 daemon 侧；本面只读写云端**权威数据**（项目/创作/进度/产物引用）；
- daemon 把本地引擎/工作会话推进经 `PUT /creations/{id}/sync` 写回这里（进度透明的数据层落点）。

身份恒取自 Owner JWT（request.user.id → owner_hasn_id，行级隔离）；owner 直接操作时 agent_hasn_id 留空。
一律返回统一信封（ResponseModel + response_base.success）。
"""

from fastapi import APIRouter, Request

from backend.app.hasn_core.app_platform import resolve_owner_hasn_id
from backend.app.hasn_reel.schema.owner import CreateCreationParam, SaveProjectParam, SyncCreationParam
from backend.app.hasn_reel.service.reel_service import reel_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _owner(db: CurrentSession | CurrentSessionTransaction, request: Request) -> str:
    """从 Owner JWT 解析 owner_hasn_id；无平台身份映射则拒（行级隔离前提）。"""
    owner_hasn_id = await resolve_owner_hasn_id(db, user_id=request.user.id)
    if not owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号未关联唤星身份，无法访问短视频')
    return owner_hasn_id


# ============================ 项目 ============================


@router.get('/projects', summary='[Owner] 列短视频项目', dependencies=[DependsJwtAuth])
async def list_reel_projects(request: Request, db: CurrentSession, include_archived: bool = False) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await reel_service.list_projects(db, owner_hasn_id=owner_hasn_id, include_archived=include_archived)
    return response_base.success(data=data)


@router.get('/projects/{project_id}', summary='[Owner] 项目详情（含创作历史）', dependencies=[DependsJwtAuth])
async def get_reel_project(request: Request, db: CurrentSession, project_id: int) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await reel_service.get_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
    return response_base.success(data=data)


@router.post('/projects', summary='[Owner] 新建/更新短视频项目', dependencies=[DependsJwtAuth])
async def save_reel_project(request: Request, db: CurrentSessionTransaction, obj: SaveProjectParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await reel_service.save_project(
        db,
        owner_hasn_id=owner_hasn_id,
        project_id=obj.project_id,
        title=obj.title,
        description=obj.description,
        settings=obj.settings,
        cover_asset_uri=obj.cover_asset_uri,
        bound_agent_id=obj.bound_agent_id,
        status=obj.status,
    )
    return response_base.success(data=data)


@router.delete('/projects/{project_id}', summary='[Owner] 删除项目（含其创作元数据）', dependencies=[DependsJwtAuth])
async def delete_reel_project(request: Request, db: CurrentSessionTransaction, project_id: int) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    await reel_service.delete_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
    return response_base.success()


# ============================ 创作（一次创作 = 进度 + 产物 + 历史） ============================


@router.get('/creations', summary='[Owner] 列创作历史（可按项目过滤）', dependencies=[DependsJwtAuth])
async def list_creations(request: Request, db: CurrentSession, project_id: int | None = None) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await reel_service.list_creations(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
    return response_base.success(data=data)


@router.get('/creations/{creation_id}', summary='[Owner] 一次创作详情（进度/产物）', dependencies=[DependsJwtAuth])
async def get_creation(request: Request, db: CurrentSession, creation_id: int) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await reel_service.get_creation(db, owner_hasn_id=owner_hasn_id, creation_id=creation_id)
    return response_base.success(data=data)


@router.post('/creations', summary='[Owner] 开一次创作（统一三种发起方式）', dependencies=[DependsJwtAuth])
async def create_creation(request: Request, db: CurrentSessionTransaction, obj: CreateCreationParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await reel_service.create_creation(
        db,
        owner_hasn_id=owner_hasn_id,
        project_id=obj.project_id,
        kind=obj.kind,
        title=obj.title,
        idea=obj.idea,
        session_id=obj.session_id,
        engine_task_id=obj.engine_task_id,
    )
    return response_base.success(data=data)


@router.put('/creations/{creation_id}/sync', summary='[Owner] 同步创作进度/产物（daemon 回写）', dependencies=[DependsJwtAuth])
async def sync_creation(
    request: Request, db: CurrentSessionTransaction, creation_id: int, obj: SyncCreationParam
) -> ResponseModel:
    """daemon 把本地引擎/工作会话推进经此写回创作进度与产物（doc29 §3 黑盒→透明的数据层落点）。"""
    owner_hasn_id = await _owner(db, request)
    data = await reel_service.sync_creation(
        db,
        owner_hasn_id=owner_hasn_id,
        creation_id=creation_id,
        status=obj.status,
        stage=obj.stage,
        progress=obj.progress,
        session_id=obj.session_id,
        engine_task_id=obj.engine_task_id,
        video_ref=obj.video_ref,
        thumbnail_asset_uri=obj.thumbnail_asset_uri,
        duration_sec=obj.duration_sec,
        resolution=obj.resolution,
        result_refs=obj.result_refs,
        error=obj.error,
    )
    return response_base.success(data=data)
