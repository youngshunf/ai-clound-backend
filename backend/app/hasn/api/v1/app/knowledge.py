"""HASN 知识库凭据 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class KnowledgeCredentialResponse(BaseModel):
    """知识库凭据响应"""

    status: str = Field(..., description="凭据状态: not_provisioned | pending | active | revoked")
    url: str | None = Field(None, description="RAGFlow 服务地址")
    api_key: str | None = Field(None, description="API Key（已解密）")
    instance_id: int | None = Field(None, description="实例 ID")
    ragflow_user_id: str | None = Field(None, description="RAGFlow 用户 ID")


class SaveRagflowInstanceRequest(BaseModel):
    url: str
    admin_api_key: str
    public_pem: str
    default_embd_id: str | None = None
    default_llm_id: str | None = None


@router.get(
    '/knowledge/credentials',
    summary='获取当前用户的知识库凭据',
    description='获取当前用户的 RAGFlow 凭据，用于 daemon 初始化知识库适配器',
    dependencies=[DependsJwtAuth],
)
async def get_knowledge_credentials(request: Request, db: CurrentSession) -> ResponseModel:
    data = await workbench_domain_service.get_current_knowledge_credentials(db, user_id=request.user.id)
    return response_base.success(data=data)


@router.post(
    '/knowledge/credentials/refresh',
    summary='刷新当前用户的知识库凭据',
    description='刷新当前用户的 RAGFlow 凭据，用于 daemon 在工作空间切换后重建适配器状态',
    dependencies=[DependsJwtAuth],
)
async def refresh_knowledge_credentials(request: Request, db: CurrentSessionTransaction) -> ResponseModel:
    data = await workbench_domain_service.refresh_current_knowledge_credentials(db, user_id=request.user.id)
    return response_base.success(data=data)


# RF-CLOUD：数据面中转路由（datasets 列表/创建、search、upload）已删除。
# owner 工作台的知识库浏览/检索/上传现由 hasn-node daemon 经 KnowledgeAdapter
# 直连 RagFlow（控制面/数据面分离，设计 §4.5）；云端只保留凭据下发 + 企业实例配置。


@router.get(
    '/knowledge/enterprise/{enterprise_id}',
    summary='获取企业知识库实例',
    dependencies=[DependsJwtAuth],
)
async def get_enterprise_ragflow_instance(
    request: Request,
    db: CurrentSession,
    enterprise_id: int,
) -> ResponseModel:
    data = await workbench_domain_service.get_enterprise_ragflow_instance(
        db,
        enterprise_id=enterprise_id,
        user_id=request.user.id,
    )
    return response_base.success(data=data)


@router.put(
    '/knowledge/enterprise/{enterprise_id}',
    summary='保存企业知识库实例',
    dependencies=[DependsJwtAuth],
)
async def save_enterprise_ragflow_instance(
    request: Request,
    db: CurrentSessionTransaction,
    enterprise_id: int,
    body: SaveRagflowInstanceRequest,
) -> ResponseModel:
    data = await workbench_domain_service.save_enterprise_ragflow_instance(
        db,
        enterprise_id=enterprise_id,
        user_id=request.user.id,
        url=body.url,
        admin_api_key=body.admin_api_key,
        public_pem=body.public_pem,
        default_embd_id=body.default_embd_id,
        default_llm_id=body.default_llm_id,
    )
    return response_base.success(data=data)


@router.post(
    '/knowledge/enterprise/{enterprise_id}/test',
    summary='测试企业知识库实例',
    dependencies=[DependsJwtAuth],
)
async def test_enterprise_ragflow_instance(
    request: Request,
    db: CurrentSession,
    enterprise_id: int,
) -> ResponseModel:
    data = await workbench_domain_service.test_enterprise_ragflow_instance(
        db,
        enterprise_id=enterprise_id,
        user_id=request.user.id,
    )
    return response_base.success(data=data)


@router.delete(
    '/knowledge/enterprise/{enterprise_id}',
    summary='禁用企业知识库实例',
    dependencies=[DependsJwtAuth],
)
async def disable_enterprise_ragflow_instance(
    request: Request,
    db: CurrentSession,
    enterprise_id: int,
) -> ResponseModel:
    data = await workbench_domain_service.disable_enterprise_ragflow_instance(
        db,
        enterprise_id=enterprise_id,
        user_id=request.user.id,
    )
    return response_base.success(data=data)
