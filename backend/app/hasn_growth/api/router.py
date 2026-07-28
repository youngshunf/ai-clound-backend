from fastapi import APIRouter
from fastapi.routing import APIRoute

from backend.app.hasn_growth.api.v1.admin.business import router as admin_business_router

# --- 管理端低风险配置/作业面；含 PII 的生成 CRUD 仅保留 codegen 产物，不挂路由。 ---
from backend.app.hasn_growth.api.v1.admin.lead_collection_job import router as admin_lead_collection_job_router
from backend.app.hasn_growth.api.v1.admin.lead_export_batch import router as admin_lead_export_batch_router
from backend.app.hasn_growth.api.v1.admin.lead_firecrawl_request import router as admin_lead_firecrawl_request_router

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn_growth.api.v1.admin.lead_source_config import router as admin_lead_source_config_router
from backend.app.hasn_growth.api.v1.admin.opportunity import router as admin_opportunity_router
from backend.app.hasn_growth.api.v1.admin.playbook import router as admin_playbook_router
from backend.app.hasn_growth.api.v1.agent.business import router as agent_business_router

# --- 获客漏斗业务面（M3：funnel/outreach/opportunity/report，仅挂 canonical /api/v1/growth/*） ---
from backend.app.hasn_growth.api.v1.agent.growth import router as agent_growth_router

# --- Agent（Agent Key） ---
from backend.app.hasn_growth.api.v1.app.business import router as app_business_router
from backend.app.hasn_growth.api.v1.app.growth import router as app_growth_router

# --- 用户端（仅 JWT） ---
from backend.app.hasn_growth.api.v1.open.business import router as open_business_router
from backend.app.hasn_growth.api.v1.open.forms import router as open_forms_router

# --- 公开（无需认证） ---
from backend.core.conf import settings

# ========================================
# 收编路由（设计 07 §5.0）：canonical 前缀 /api/v1/growth/*。
# 旧 /api/v1/lead-automation/* 薄转发已于 M8 退役（2026-06-13）——_build_routers 现仅构建 canonical。
# ========================================


def _build_routers(seg: str) -> tuple[APIRouter, APIRouter, APIRouter, APIRouter]:
    """为给定前缀段构建四 scope 路由（v1=admin / app / open / agent）。"""
    base = f'{settings.FASTAPI_API_V1_PATH}/{seg}'

    # --- 管理端 API（JWT + RBAC，前缀 base） ---
    v1_ = APIRouter(prefix=base, tags=['AI lead automation source configuration管理'])
    v1_.include_router(
        admin_lead_source_config_router,
        prefix='/lead-source-configs',
        tags=['AI lead automation source configuration管理-AI lead automation source configuration'],
    )
    v1_.include_router(
        admin_lead_collection_job_router,
        prefix='/lead/collection/jobs',
        tags=['AI lead automation collection job-AI lead automation collection job'],
    )
    v1_.include_router(
        admin_lead_firecrawl_request_router,
        prefix='/lead/firecrawl/requests',
        tags=['Firecrawl request audit for AI lead automation-Firecrawl request audit for AI lead automation'],
    )
    v1_.include_router(
        admin_lead_export_batch_router,
        prefix='/lead/export/batchs',
        tags=['Lead CSV export batch-Lead CSV export batch'],
    )
    v1_.include_router(admin_business_router, tags=['AI lead automation业务接口'])

    # --- 用户端 API（仅 JWT，前缀 base/app） ---
    app_ = APIRouter(prefix=f'{base}/app', tags=['AI lead automation source configuration用户端'])
    app_.include_router(app_business_router, tags=['AI lead automation业务接口'])

    # --- 公开 API（无需认证，前缀 base/open） ---
    open_ = APIRouter(prefix=f'{base}/open', tags=['AI lead automation source configuration公开'])
    open_.include_router(open_business_router, tags=['AI lead automation公开业务接口'])

    # --- Agent API（Agent JWT，前缀 base/agent） ---
    agent_ = APIRouter(prefix=f'{base}/agent', tags=['AI lead automation source configurationAgent'])
    agent_.include_router(agent_business_router, tags=['AI lead automation Agent业务接口'])

    return v1_, app_, open_, agent_


def _prefix_route_names(router: APIRouter, prefix: str) -> APIRouter:
    """给 router 内全部路由名加前缀，保证 route name 全局唯一（app/agent 两 scope 有同名
    handler 如 list_customers，需加 scope 前缀区分；详见 utils.openapi.ensure_unique_route_names）。"""
    for route in router.routes:
        if isinstance(route, APIRoute):
            route.name = f'{prefix}{route.name}'
            route.operation_id = route.name
    return router


# canonical：/api/v1/growth/*
# （旧 /api/v1/lead-automation/* 薄转发已于 M8 退役 2026-06-13——管理端前端确认全量切 /api/v1/growth/* 后双中心清零）
v1, app, open_api, agent = _build_routers('growth')

# 不含 PII 的管理配置仍可走生成 CRUD；客户、触达、活动、表单和退订统一走主体隔离的业务面。
v1.include_router(admin_opportunity_router, prefix='/opportunitys', tags=['获客商机-管理'])
v1.include_router(admin_playbook_router, prefix='/playbooks', tags=['获客打法模板-管理'])

# 获客漏斗业务面（M3：funnel/outreach/opportunity/report）——仅挂 canonical /api/v1/growth/*
# owner: /api/v1/growth/app/* ；agent: /api/v1/growth/agent/* ；open: /api/v1/growth/open/*
# agent/app 两面有同名 handler（list_customers 等）→ 路由名加 scope 前缀保证全局唯一
# （ensure_unique_route_names 全局校验，否则 app 启动即抛 Non-unique route name）。
app.include_router(_prefix_route_names(app_growth_router, 'growth_app_'), tags=['获客漏斗-用户端'])
agent.include_router(_prefix_route_names(agent_growth_router, 'growth_agent_'), tags=['获客漏斗-Agent'])
open_api.include_router(_prefix_route_names(open_forms_router, 'growth_open_'), tags=['获客表单回流-公开'])
