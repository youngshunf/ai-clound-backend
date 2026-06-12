from fastapi import APIRouter
from fastapi.routing import APIRoute

from backend.core.conf import settings

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn_growth.api.v1.admin.lead_source_config import router as admin_lead_source_config_router
from backend.app.hasn_growth.api.v1.admin.lead_collection_job import router as admin_lead_collection_job_router
from backend.app.hasn_growth.api.v1.admin.lead_firecrawl_request import router as admin_lead_firecrawl_request_router
from backend.app.hasn_growth.api.v1.admin.lead_raw_record import router as admin_lead_raw_record_router
from backend.app.hasn_growth.api.v1.admin.lead_contact import router as admin_lead_contact_router
from backend.app.hasn_growth.api.v1.admin.lead_contact_source import router as admin_lead_contact_source_router
from backend.app.hasn_growth.api.v1.admin.lead_rejected_record import router as admin_lead_rejected_record_router
from backend.app.hasn_growth.api.v1.admin.lead_export_batch import router as admin_lead_export_batch_router
from backend.app.hasn_growth.api.v1.admin.lead_export_item import router as admin_lead_export_item_router
from backend.app.hasn_growth.api.v1.admin.lead_audit_log import router as admin_lead_audit_log_router
from backend.app.hasn_growth.api.v1.admin.business import router as admin_business_router
# --- 用户端（仅 JWT） ---
from backend.app.hasn_growth.api.v1.app.lead_source_config import router as app_lead_source_config_router
from backend.app.hasn_growth.api.v1.app.lead_collection_job import router as app_lead_collection_job_router
from backend.app.hasn_growth.api.v1.app.lead_firecrawl_request import router as app_lead_firecrawl_request_router
from backend.app.hasn_growth.api.v1.app.lead_raw_record import router as app_lead_raw_record_router
from backend.app.hasn_growth.api.v1.app.lead_contact import router as app_lead_contact_router
from backend.app.hasn_growth.api.v1.app.lead_contact_source import router as app_lead_contact_source_router
from backend.app.hasn_growth.api.v1.app.lead_rejected_record import router as app_lead_rejected_record_router
from backend.app.hasn_growth.api.v1.app.lead_export_batch import router as app_lead_export_batch_router
from backend.app.hasn_growth.api.v1.app.lead_export_item import router as app_lead_export_item_router
from backend.app.hasn_growth.api.v1.app.lead_audit_log import router as app_lead_audit_log_router
from backend.app.hasn_growth.api.v1.app.business import router as app_business_router
# --- Agent（Agent Key） ---
from backend.app.hasn_growth.api.v1.agent.lead_source_config import router as agent_lead_source_config_router
from backend.app.hasn_growth.api.v1.agent.lead_collection_job import router as agent_lead_collection_job_router
from backend.app.hasn_growth.api.v1.agent.lead_firecrawl_request import router as agent_lead_firecrawl_request_router
from backend.app.hasn_growth.api.v1.agent.lead_raw_record import router as agent_lead_raw_record_router
from backend.app.hasn_growth.api.v1.agent.lead_contact import router as agent_lead_contact_router
from backend.app.hasn_growth.api.v1.agent.lead_contact_source import router as agent_lead_contact_source_router
from backend.app.hasn_growth.api.v1.agent.lead_rejected_record import router as agent_lead_rejected_record_router
from backend.app.hasn_growth.api.v1.agent.lead_export_batch import router as agent_lead_export_batch_router
from backend.app.hasn_growth.api.v1.agent.lead_export_item import router as agent_lead_export_item_router
from backend.app.hasn_growth.api.v1.agent.lead_audit_log import router as agent_lead_audit_log_router
from backend.app.hasn_growth.api.v1.agent.business import router as agent_business_router
# --- 公开（无需认证） ---
from backend.app.hasn_growth.api.v1.open.lead_source_config import router as open_lead_source_config_router
from backend.app.hasn_growth.api.v1.open.lead_collection_job import router as open_lead_collection_job_router
from backend.app.hasn_growth.api.v1.open.lead_firecrawl_request import router as open_lead_firecrawl_request_router
from backend.app.hasn_growth.api.v1.open.lead_raw_record import router as open_lead_raw_record_router
from backend.app.hasn_growth.api.v1.open.lead_contact import router as open_lead_contact_router
from backend.app.hasn_growth.api.v1.open.lead_contact_source import router as open_lead_contact_source_router
from backend.app.hasn_growth.api.v1.open.lead_rejected_record import router as open_lead_rejected_record_router
from backend.app.hasn_growth.api.v1.open.lead_export_batch import router as open_lead_export_batch_router
from backend.app.hasn_growth.api.v1.open.lead_export_item import router as open_lead_export_item_router
from backend.app.hasn_growth.api.v1.open.lead_audit_log import router as open_lead_audit_log_router
from backend.app.hasn_growth.api.v1.open.business import router as open_business_router

# ========================================
# 收编路由（设计 07 §5.0）：canonical 前缀 /api/v1/growth/*；
# 旧 /api/v1/lead-automation/* 保薄转发过渡（双挂载同一批子路由），M8 退役。
# ========================================


def _build_routers(seg: str) -> tuple[APIRouter, APIRouter, APIRouter, APIRouter]:
    """为给定前缀段构建四 scope 路由（v1=admin / app / open / agent）。"""
    base = f'{settings.FASTAPI_API_V1_PATH}/{seg}'

    # --- 管理端 API（JWT + RBAC，前缀 base） ---
    _v1 = APIRouter(prefix=base, tags=['AI lead automation source configuration管理'])
    _v1.include_router(admin_lead_source_config_router, prefix='/lead-source-configs', tags=['AI lead automation source configuration管理-AI lead automation source configuration'])
    _v1.include_router(admin_lead_collection_job_router, prefix='/lead/collection/jobs', tags=['AI lead automation collection job-AI lead automation collection job'])
    _v1.include_router(admin_lead_firecrawl_request_router, prefix='/lead/firecrawl/requests', tags=['Firecrawl request audit for AI lead automation-Firecrawl request audit for AI lead automation'])
    _v1.include_router(admin_lead_raw_record_router, prefix='/lead/raw/records', tags=['Raw crawled lead page record-Raw crawled lead page record'])
    _v1.include_router(admin_lead_contact_router, prefix='/lead/contacts', tags=['Valid deduplicated lead contact-Valid deduplicated lead contact'])
    _v1.include_router(admin_lead_contact_source_router, prefix='/lead/contact/sources', tags=['Lead multi-source evidence-Lead multi-source evidence'])
    _v1.include_router(admin_lead_rejected_record_router, prefix='/lead/rejected/records', tags=['Rejected, invalid, duplicate, or failed lead record-Rejected, invalid, duplicate, or failed lead record'])
    _v1.include_router(admin_lead_export_batch_router, prefix='/lead/export/batchs', tags=['Lead CSV export batch-Lead CSV export batch'])
    _v1.include_router(admin_lead_export_item_router, prefix='/lead/export/items', tags=['Lead CSV export item snapshot-Lead CSV export item snapshot'])
    _v1.include_router(admin_lead_audit_log_router, prefix='/lead/audit/logs', tags=['Lead automation PII and compliance audit log-Lead automation PII and compliance audit log'])
    _v1.include_router(admin_business_router, tags=['AI lead automation业务接口'])

    # --- 用户端 API（仅 JWT，前缀 base/app） ---
    _app = APIRouter(prefix=f'{base}/app', tags=['AI lead automation source configuration用户端'])
    _app.include_router(app_lead_source_config_router, prefix='/lead-source-configs', tags=['AI lead automation source configuration用户端-AI lead automation source configuration'])
    _app.include_router(app_lead_collection_job_router, prefix='/lead/collection/jobs', tags=['AI lead automation collection job-AI lead automation collection job'])
    _app.include_router(app_lead_firecrawl_request_router, prefix='/lead/firecrawl/requests', tags=['Firecrawl request audit for AI lead automation-Firecrawl request audit for AI lead automation'])
    _app.include_router(app_lead_raw_record_router, prefix='/lead/raw/records', tags=['Raw crawled lead page record-Raw crawled lead page record'])
    _app.include_router(app_lead_contact_router, prefix='/lead/contacts', tags=['Valid deduplicated lead contact-Valid deduplicated lead contact'])
    _app.include_router(app_lead_contact_source_router, prefix='/lead/contact/sources', tags=['Lead multi-source evidence-Lead multi-source evidence'])
    _app.include_router(app_lead_rejected_record_router, prefix='/lead/rejected/records', tags=['Rejected, invalid, duplicate, or failed lead record-Rejected, invalid, duplicate, or failed lead record'])
    _app.include_router(app_lead_export_batch_router, prefix='/lead/export/batchs', tags=['Lead CSV export batch-Lead CSV export batch'])
    _app.include_router(app_lead_export_item_router, prefix='/lead/export/items', tags=['Lead CSV export item snapshot-Lead CSV export item snapshot'])
    _app.include_router(app_lead_audit_log_router, prefix='/lead/audit/logs', tags=['Lead automation PII and compliance audit log-Lead automation PII and compliance audit log'])
    _app.include_router(app_business_router, tags=['AI lead automation业务接口'])

    # --- 公开 API（无需认证，前缀 base/open） ---
    _open = APIRouter(prefix=f'{base}/open', tags=['AI lead automation source configuration公开'])
    _open.include_router(open_business_router, tags=['AI lead automation公开业务接口'])

    # --- Agent API（Agent JWT，前缀 base/agent） ---
    _agent = APIRouter(prefix=f'{base}/agent', tags=['AI lead automation source configurationAgent'])
    _agent.include_router(agent_business_router, tags=['AI lead automation Agent业务接口'])

    return _v1, _app, _open, _agent


def _prefix_route_names(router: APIRouter, prefix: str) -> APIRouter:
    """给 router 内全部路由名加前缀，避免与 canonical 同名（route name 全局唯一，
    薄转发与 canonical 双挂载同一批子路由会产生重名，详见 utils.openapi.ensure_unique_route_names）。"""
    for route in router.routes:
        if isinstance(route, APIRoute):
            route.name = f'{prefix}{route.name}'
            route.operation_id = route.name
    return router


# canonical：/api/v1/growth/*
v1, app, open_api, agent = _build_routers('growth')
# 薄转发：/api/v1/lead-automation/*（M8 退役）——路由名加 legacy_ 前缀保持全局唯一
legacy_v1, legacy_app, legacy_open, legacy_agent = (
    _prefix_route_names(r, 'legacy_') for r in _build_routers('lead-automation')
)
