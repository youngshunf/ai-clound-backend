from fastapi import APIRouter

from backend.core.conf import settings

# --- 管理端（JWT + RBAC） ---
from backend.app.hasn_designsystem.api.v1.admin.design_system import router as admin_design_system_router
from backend.app.hasn_designsystem.api.v1.admin.revision import router as admin_revision_router
from backend.app.hasn_designsystem.api.v1.admin.collaborator import router as admin_collaborator_router
from backend.app.hasn_designsystem.api.v1.admin.consumer_link import router as admin_consumer_link_router
# --- 用户端（仅 JWT） ---
from backend.app.hasn_designsystem.api.v1.app.design_system import router as app_design_system_router
from backend.app.hasn_designsystem.api.v1.app.revision import router as app_revision_router
from backend.app.hasn_designsystem.api.v1.app.collaborator import router as app_collaborator_router
from backend.app.hasn_designsystem.api.v1.app.consumer_link import router as app_consumer_link_router
# --- Agent（Agent Key） ---
from backend.app.hasn_designsystem.api.v1.agent.design_system import router as agent_design_system_router
from backend.app.hasn_designsystem.api.v1.agent.revision import router as agent_revision_router
from backend.app.hasn_designsystem.api.v1.agent.collaborator import router as agent_collaborator_router
from backend.app.hasn_designsystem.api.v1.agent.consumer_link import router as agent_consumer_link_router
# --- 公开（无需认证） ---
from backend.app.hasn_designsystem.api.v1.open.design_system import router as open_design_system_router
from backend.app.hasn_designsystem.api.v1.open.revision import router as open_revision_router
from backend.app.hasn_designsystem.api.v1.open.collaborator import router as open_collaborator_router
from backend.app.hasn_designsystem.api.v1.open.consumer_link import router as open_consumer_link_router

# ========================================
# 管理端 API（JWT + RBAC）
# 路径前缀: /api/v1/hasn_designsystem/
# ========================================
v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_designsystem', tags=['设计系统（云端权威）管理'])

v1.include_router(admin_design_system_router, prefix='/design-system', tags=['设计系统（云端权威）管理-设计系统（云端权威）'])
v1.include_router(admin_revision_router, prefix='/revisions', tags=['演示文稿版本快照（云端权威历史）-演示文稿版本快照（云端权威历史）'])
v1.include_router(admin_collaborator_router, prefix='/collaborators', tags=['设计系统协作分身绑定（对齐 DECKBIND）-设计系统协作分身绑定（对齐 DECKBIND）'])
v1.include_router(admin_consumer_link_router, prefix='/consumer/links', tags=['设计系统下游消费登记（换系统重渲染追踪）-设计系统下游消费登记（换系统重渲染追踪）'])

# ========================================
# 用户端 API（仅 JWT，无 RBAC）
# 路径前缀: /api/v1/hasn_designsystem/app/
# ========================================
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_designsystem/app', tags=['设计系统（云端权威）用户端'])

app.include_router(app_design_system_router, prefix='/design-system', tags=['设计系统（云端权威）用户端-设计系统（云端权威）'])
app.include_router(app_revision_router, prefix='/revisions', tags=['演示文稿版本快照（云端权威历史）-演示文稿版本快照（云端权威历史）'])
app.include_router(app_collaborator_router, prefix='/collaborators', tags=['设计系统协作分身绑定（对齐 DECKBIND）-设计系统协作分身绑定（对齐 DECKBIND）'])
app.include_router(app_consumer_link_router, prefix='/consumer/links', tags=['设计系统下游消费登记（换系统重渲染追踪）-设计系统下游消费登记（换系统重渲染追踪）'])

# ========================================
# 公开 API（无需认证）
# 路径前缀: /api/v1/hasn_designsystem/open/
# ========================================
open_api = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_designsystem/open', tags=['设计系统（云端权威）公开'])

open_api.include_router(open_design_system_router, prefix='/design-system', tags=['设计系统（云端权威）公开-设计系统（云端权威）'])
open_api.include_router(open_revision_router, prefix='/revisions', tags=['演示文稿版本快照（云端权威历史）-演示文稿版本快照（云端权威历史）'])
open_api.include_router(open_collaborator_router, prefix='/collaborators', tags=['设计系统协作分身绑定（对齐 DECKBIND）-设计系统协作分身绑定（对齐 DECKBIND）'])
open_api.include_router(open_consumer_link_router, prefix='/consumer/links', tags=['设计系统下游消费登记（换系统重渲染追踪）-设计系统下游消费登记（换系统重渲染追踪）'])

# ========================================
# Agent API
# 路径前缀: /api/v1/hasn_designsystem/agent/
# ========================================
agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/hasn_designsystem/agent', tags=['设计系统（云端权威）Agent'])

agent.include_router(agent_design_system_router, prefix='/design-system', tags=['设计系统（云端权威）Agent-设计系统（云端权威）'])
agent.include_router(agent_revision_router, prefix='/revisions', tags=['演示文稿版本快照（云端权威历史）-演示文稿版本快照（云端权威历史）'])
agent.include_router(agent_collaborator_router, prefix='/collaborators', tags=['设计系统协作分身绑定（对齐 DECKBIND）-设计系统协作分身绑定（对齐 DECKBIND）'])
agent.include_router(agent_consumer_link_router, prefix='/consumer/links', tags=['设计系统下游消费登记（换系统重渲染追踪）-设计系统下游消费登记（换系统重渲染追踪）'])
