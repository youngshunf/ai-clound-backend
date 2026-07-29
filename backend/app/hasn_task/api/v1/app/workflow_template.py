"""工作流模板 - 用户端 API（hasn_task 应用，P3 模板层 doc11 §4）。

认证方式: DependsJwtAuth（仅当前登录用户）。可见性 = 内置 + 自己名下（owner=current_owner_id）。
路径前缀: /api/v1/hasn-task/app
- 列模板（首页模板条 / 画廊；可按 domain 过滤、按 sort_order 排序）+ 领域分组元数据
- 取单模板详情（含 graph_spec 图蓝图，供实例化向导预览）
- 自定义场景搭建器：建模板（POST）/ 改模板（PUT）/ 搭建器选项集（GET builder-options）

WebUI 只调 daemon 铁律：webui 实际走 daemon 的 /api/v1/workflow-templates，由 daemon local_first
镜像后 read-through 到本组云端接口；场景实例化由 daemon 通过 Owner 网关调用本模块的云端权威入口，
取得稳定定义后才在本地物化并 fire。
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from backend.app.hasn_task.api.v1.app.task import current_owner_id
from backend.app.hasn_task.schema.workflow_template import (
    OwnerCreateTemplateParam,
    OwnerInstantiateTemplateParam,
    OwnerUpdateTemplateParam,
)
from backend.app.hasn_task.service.workflow_template_service import workflow_template_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '/workflow-templates',
    summary='列工作流模板 + 领域分组',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_template_app_list',
)
async def list_templates(
    request: Request,
    db: CurrentSession,
    domain_only: Annotated[bool, Query(description='只取场景模板（domain 非空）')] = False,
    domain: Annotated[str | None, Query(description='按领域 code 精确过滤')] = None,
    status: Annotated[str | None, Query(description='按状态过滤（缺省不过滤）')] = None,
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    data = await workflow_template_service.list_templates(
        db, owner_id=owner_id, domain_only=domain_only, domain=domain, status=status
    )
    return response_base.success(data=data)


# ⚠️ 静态路径必须在 /{template_key} 之前注册，否则会被动态段吞掉（builder-options 会当成 template_key）。
@router.get(
    '/workflow-templates/builder-options',
    summary='自定义场景搭建器选项集（应用/人设/产出/领域）',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_template_app_builder_options',
)
async def builder_options(request: Request, db: CurrentSession) -> ResponseModel:
    # 选项来自云端权威源（app_catalog_registry + ai_native_app_registry + sys_dict），与服务端校验同源。
    await current_owner_id(request, db)  # 仅确认登录态（选项集本身不含用户私有数据）
    data = await workflow_template_service.builder_options(db)
    return response_base.success(data=data)


@router.post(
    '/workflow-templates',
    summary='建自定义场景模板（主人搭建器）',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_template_app_create',
)
async def create_template(request: Request, db: CurrentSession, obj: OwnerCreateTemplateParam) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    template = await workflow_template_service.create_owner_template(
        db, owner_id=owner_id, params=obj.model_dump(exclude_none=True)
    )
    return response_base.success(data={'template': template})


@router.put(
    '/workflow-templates/{template_key}',
    summary='改自定义场景模板（主人搭建器）',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_template_app_update',
)
async def update_template(
    request: Request,
    db: CurrentSession,
    template_key: Annotated[str, Path()],
    obj: OwnerUpdateTemplateParam,
) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    template = await workflow_template_service.update_template(
        db, owner_id=owner_id, template_key=template_key, params=obj.model_dump(exclude_none=True)
    )
    return response_base.success(data={'template': template})


@router.post(
    '/workflow-templates/{template_key}:instantiate',
    summary='在云端实例化场景定义',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_template_app_instantiate',
)
async def instantiate_template(
    request: Request,
    db: CurrentSessionTransaction,
    template_key: Annotated[str, Path()],
    obj: OwnerInstantiateTemplateParam,
) -> ResponseModel:
    """Owner 先取得云端权威定义；daemon 只可用返回的稳定 UUID 做本地镜像和执行。"""
    owner_id = await current_owner_id(request, db)
    data = await workflow_template_service.instantiate_owner_template(
        db, owner_id=owner_id, template_key=template_key, params=obj.model_dump(mode='json')
    )
    return response_base.success(data=data)


@router.get(
    '/workflow-templates/{template_key}',
    summary='取工作流模板详情（含 graph_spec）',
    dependencies=[DependsJwtAuth],
    name='hasn_workflow_template_app_get',
)
async def get_template(request: Request, db: CurrentSession, template_key: Annotated[str, Path()]) -> ResponseModel:
    owner_id = await current_owner_id(request, db)
    template = await workflow_template_service.get_template(db, owner_id=owner_id, template_key=template_key)
    return response_base.success(data={'template': template})
